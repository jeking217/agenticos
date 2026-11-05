"""Where a person's CMS token comes from, and what happens when it is wrong.

The module is useless without a platform token, and the sign-in flow deliberately
does not keep one: the platform's token is spent on the identity it named and the
local session that follows is a different thing entirely. So the token is handed
over once, by the person, and stored encrypted - which makes this small path the
one that decides whether the whole feature can open at all.

Two rules it has to keep, and both are about what a stored token costs when it is
wrong. It is checked with the CMS before it is kept, because a stored bad token
turns the next page load into "your session expired" - a sentence that sends the
person looking for a password that is not the problem. And it is never stored in
a form that can be read back out of the database by anyone who can read it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.cms.errors import TOKEN_EXPIRED, UNREACHABLE, CmsError
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


class FakeClient:
    """A CMS that answers one question, so the check can be tested on its own."""

    def __init__(self, *, accepts: bool) -> None:
        self.accepts = accepts
        self.closed = False
        self.seen_token = ""

    async def is_authed(self) -> bool:
        return self.accepts

    async def aclose(self) -> None:
        self.closed = True


def user(**overrides):
    base = dict(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        oauth_provider="unione",
        oauth_id="100:10000",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def service(monkeypatch, *, accepts: bool = True):
    db = FakeDb()
    flow = CmsSitesService(db)

    def fake_client(token: str) -> FakeClient:
        client = FakeClient(accepts=accepts)
        client.seen_token = token
        return client

    monkeypatch.setattr(flow, "_client", fake_client)
    return flow, db


@pytest.mark.anyio
async def test_a_working_token_is_kept_encrypted(monkeypatch):
    flow, db = service(monkeypatch)

    await flow.connect(user(), None, "tenant@user@hash")

    assert len(db.added) == 1, "one row, not one per attempt"
    stored = db.added[0]
    assert "tenant@user@hash" not in stored.token_cipher
    assert flow._cipher().unseal(stored.token_cipher) == "tenant@user@hash"


@pytest.mark.anyio
async def test_the_token_is_checked_before_it_is_kept(monkeypatch):
    """A stored bad token is worse than no token: it reads as an expired session."""
    flow, db = service(monkeypatch, accepts=False)

    with pytest.raises(CmsError) as caught:
        await flow.connect(user(), None, "typo")

    assert caught.value.code == TOKEN_EXPIRED
    assert db.added == []


@pytest.mark.anyio
async def test_a_cms_that_is_down_is_not_reported_as_a_bad_token(monkeypatch):
    """The two send a person to different places: one retypes, one waits.

    `is_authed` answers False only for the refusals that mean "this token", and
    lets a network failure through - this is the test that keeps it that way.
    """
    db = FakeDb()
    flow = CmsSitesService(db)

    class Unreachable(FakeClient):
        async def is_authed(self) -> bool:
            raise CmsError(UNREACHABLE, "the CMS did not answer")

    monkeypatch.setattr(flow, "_client", lambda _token: Unreachable(accepts=False))

    with pytest.raises(CmsError) as caught:
        await flow.connect(user(), None, "tenant@user@hash")

    assert caught.value.code == UNREACHABLE
    assert db.added == []


@pytest.mark.anyio
async def test_surrounding_whitespace_is_not_part_of_the_token(monkeypatch):
    """It arrives by copy-paste, and a trailing newline fails as "wrong token"."""
    flow, db = service(monkeypatch)

    await flow.connect(user(), None, "  tenant@user@hash\n")

    assert flow._cipher().unseal(db.added[0].token_cipher) == "tenant@user@hash"


@pytest.mark.anyio
async def test_an_empty_token_is_refused_before_the_cms_is_asked(monkeypatch):
    flow, db = service(monkeypatch)

    with pytest.raises(CmsError) as caught:
        await flow.connect(user(), None, "   ")

    assert caught.value.code == TOKEN_EXPIRED
    assert db.added == []


@pytest.mark.anyio
async def test_a_person_with_no_stored_token_is_told_so(monkeypatch):
    flow, _ = service(monkeypatch)

    assert await flow.has_token(user()) is False


@pytest.mark.anyio
async def test_a_person_with_a_stored_token_is_told_so(monkeypatch):
    flow, db = service(monkeypatch)
    await flow.connect(user(), None, "tenant@user@hash")

    assert await flow.has_token(user()) is True
