"""The token is a password, and the site card is a permission decision.

Both are small enough to look like plumbing, and both are where a mistake turns
into either a leak or a page of buttons that lie. So the tests are about what
must not happen: a token readable in a stored row, a token in a log line, a
site offered for editing to somebody the CMS will refuse.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.cms.client import CmsSiteRecord
from app.cms.errors import TOKEN_EXPIRED, CmsError
from app.cms.sites import editable_by, normalise_roles, site_cards
from app.cms.tokens import EXPIRY_SKEW, TokenCipher, is_expired, mask

SECRET = "deployment-secret"


def site(**overrides) -> CmsSiteRecord:
    defaults = dict(
        id=7,
        title="Main",
        ctx="/cms/main",
        domain=None,
        cover_pic="/file/cover.png",
        space_id=11,
        space_vers=3,
        admin_userid=None,
        created_by=None,
    )
    return CmsSiteRecord(**{**defaults, **overrides})


def test_a_token_survives_a_round_trip_through_storage():
    cipher = TokenCipher(SECRET)
    token = "tenant@user@0123456789abcdef"

    assert cipher.unseal(cipher.seal(token)) == token


def test_the_stored_form_does_not_contain_the_token():
    cipher = TokenCipher(SECRET)
    token = "tenant@user@0123456789abcdef"

    sealed = cipher.seal(token)

    assert token not in sealed
    assert "0123456789abcdef" not in sealed


def test_another_deployments_secret_cannot_read_it():
    sealed = TokenCipher(SECRET).seal("tenant@user@hash")

    with pytest.raises(CmsError) as caught:
        TokenCipher("someone-elses-secret").unseal(sealed)

    assert caught.value.code == TOKEN_EXPIRED, "the person's next step is to sign in again"


def test_a_tampered_row_says_sign_in_rather_than_failing_obscurely():
    sealed = TokenCipher(SECRET).seal("tenant@user@hash")

    with pytest.raises(CmsError) as caught:
        TokenCipher(SECRET).unseal(sealed[:-4] + "AAAA")

    assert caught.value.code == TOKEN_EXPIRED


def test_an_empty_token_is_refused_at_seal_time():
    with pytest.raises(ValueError):
        TokenCipher(SECRET).seal("")


def test_a_secret_is_required_to_hold_tokens_at_all():
    with pytest.raises(ValueError):
        TokenCipher("")


def test_a_token_is_stale_before_it_actually_expires():
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)

    assert is_expired(now + EXPIRY_SKEW - timedelta(seconds=1), now=now)
    assert not is_expired(now + EXPIRY_SKEW + timedelta(minutes=1), now=now)
    assert not is_expired(None, now=now), "an unknown expiry is not an expired token"


def test_a_naive_expiry_is_read_as_utc_rather_than_crashing():
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)

    assert not is_expired(datetime(2026, 11, 5, 18, 0), now=now)


def test_masking_says_there_is_a_token_and_nothing_else(caplog):
    token = "tenant@user@0123456789abcdef"

    with caplog.at_level(logging.INFO):
        logging.getLogger("test").info("used %s", mask(token))

    assert token not in caplog.text
    assert "token:" in caplog.text


@pytest.mark.parametrize(
    "roles",
    [["SUPPERADMIN"], ["SYSOPSUSER"], ["CMS-ADMIN"], ["CMS-SITEDEV"], ["cms_sitedev"]],
)
def test_the_roles_the_cms_gate_names_may_edit(roles):
    assert editable_by(site(), user_id=1, roles=roles) == (True, None)


def test_the_sites_own_administrator_may_edit_without_a_role():
    assert editable_by(site(admin_userid=42), user_id=42, roles=[]) == (True, None)


def test_the_person_who_created_the_site_may_edit_it():
    assert editable_by(site(created_by=42), user_id=42, roles=[]) == (True, None)


def test_somebody_else_is_told_the_site_is_not_theirs():
    editable, reason = editable_by(site(admin_userid=9), user_id=42, roles=[])

    assert editable is False
    assert "其他人负责" in reason


def test_somebody_with_no_role_on_an_unowned_site_is_told_which_role_is_missing():
    editable, reason = editable_by(site(), user_id=42, roles=["VIEWER"])

    assert editable is False
    assert "CMS-SITEDEV" in reason


def test_role_spellings_are_one_role():
    assert normalise_roles(["cms_admin", " CMS-Admin ", ""]) == {"CMS-ADMIN"}


def test_the_card_carries_a_cover_url_only_when_there_is_a_cover():
    cards = site_cards(
        [site(), site(id=8, cover_pic=None)],
        user_id=1,
        roles=["SUPPERADMIN"],
        cover_base="https://portal.example.com",
    )

    assert cards[0].cover_url == "https://portal.example.com/file/cover.png"
    assert cards[1].cover_url is None
    assert cards[0].to_json()["editable"] is True
