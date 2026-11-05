"""Where a person's CMS token comes from, and what happens when there is none.

The module is useless without a platform token, and the platform hands one out
exactly once: alongside the identity it names, during sign-in. So the token is
kept there rather than asked for later - the person never copies one out of a
browser tab - and what this file holds the store to is the two things that costs
when it is wrong. It is never kept in a form that can be read back out of the
database by anyone who can read it, and a row that cannot work (revoked, expired)
answers "no token" rather than sending the console down a path that will 401.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.cms.errors import TOKEN_EXPIRED, CmsError
from app.services.cms_sites import CmsSitesService


class FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class FakeDb:
    """Enough of an `AsyncSession` for the statements this path runs."""

    def __init__(self, row=None) -> None:
        self.row = row
        self.added: list[object] = []
        self.flushes = 0

    async def execute(self, _statement) -> FakeResult:
        return FakeResult(self.row)

    def add(self, row) -> None:
        self.added.append(row)
        self.row = row

    async def flush(self) -> None:
        self.flushes += 1


def user(**overrides):
    base = dict(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        oauth_provider="unione",
        oauth_id="100:10000",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def service(row=None) -> tuple[CmsSitesService, FakeDb]:
    db = FakeDb(row)
    return CmsSitesService(db), db


@pytest.mark.anyio
async def test_the_platform_token_is_kept_encrypted():
    flow, db = service()

    await flow.remember_token(user(), None, "tenant@user@hash")

    assert len(db.added) == 1, "one row, not one per sign-in"
    stored = db.added[0]
    assert "tenant@user@hash" not in stored.token_cipher
    assert flow._cipher().unseal(stored.token_cipher) == "tenant@user@hash"


@pytest.mark.anyio
async def test_the_tenant_is_read_from_the_identity_not_the_token():
    """So a deployment bound to the wrong platform is visible, rather than silent."""
    flow, db = service()

    await flow.remember_token(user(), None, "tenant@user@hash")

    assert db.added[0].tenant_id == 100


@pytest.mark.anyio
async def test_signing_in_again_replaces_the_token_in_place():
    flow, db = service()
    await flow.remember_token(user(), None, "old-token")

    await flow.remember_token(user(), None, "new-token")

    assert len(db.added) == 1, "a second row would leave the old token usable"
    assert flow._cipher().unseal(db.row.token_cipher) == "new-token"


@pytest.mark.anyio
async def test_an_empty_token_is_refused():
    flow, db = service()

    with pytest.raises(ValueError):
        await flow.remember_token(user(), None, "")

    assert db.added == []


@pytest.mark.anyio
async def test_a_person_with_no_stored_token_is_told_so():
    flow, _ = service()

    assert await flow.has_token(user()) is False


@pytest.mark.anyio
async def test_a_person_with_a_stored_token_is_told_so():
    flow, _ = service()
    await flow.remember_token(user(), None, "tenant@user@hash")

    assert await flow.has_token(user()) is True


@pytest.mark.anyio
async def test_a_revoked_token_does_not_count():
    """Signing out forgets it, and a forgotten token is not a token to try."""
    flow, _ = service()
    await flow.remember_token(user(), None, "tenant@user@hash")

    await flow.forget_token(user())

    assert await flow.has_token(user()) is False


@pytest.mark.anyio
async def test_an_expired_token_does_not_count():
    flow, _ = service()
    await flow.remember_token(
        user(), None, "tenant@user@hash", expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    assert await flow.has_token(user()) is False


@pytest.mark.anyio
async def test_the_token_is_handed_back_in_the_clear():
    """The CMS needs the real value; the sealing is only about the database."""
    flow, _ = service()
    await flow.remember_token(user(), None, "tenant@user@hash")

    assert await flow.token_for(user()) == "tenant@user@hash"


@pytest.mark.anyio
async def test_a_session_without_a_token_is_asked_to_sign_in_again():
    flow, _ = service()

    with pytest.raises(CmsError) as caught:
        await flow.token_for(user())

    assert caught.value.code == TOKEN_EXPIRED
