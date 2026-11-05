"""Whether the module can be opened at all, and by whom.

The console decides this before it draws a sidebar entry, so the rule has to be
one plain function rather than a cascade of `if`s in a component. A local
account has no platform identity and therefore no CMS token; telling that person
"the module is broken" instead of "sign in with your platform account" is the
failure this test exists to prevent.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.cms_sites import CmsAccess, platform_identity


def user(**overrides):
    base = dict(oauth_provider=None, oauth_id=None, id="00000000-0000-0000-0000-000000000001")
    base.update(overrides)
    return SimpleNamespace(**base)


def test_a_platform_account_carries_the_identity_the_cms_knows():
    assert platform_identity(user(oauth_provider="unione", oauth_id="3:42")) == (3, 42)


def test_a_local_account_has_no_platform_identity():
    assert platform_identity(user()) is None


def test_an_unreadable_identity_is_no_identity_rather_than_a_crash():
    assert platform_identity(user(oauth_provider="unione", oauth_id="not-a-pair")) is None


def test_a_deployment_without_a_cms_says_which_setting_is_missing():
    access = CmsAccess(configured=False, platform_linked=True)

    assert access.can_use is False
    assert "CMS_BASE_URL" in access.reason


def test_a_local_account_is_told_to_sign_in_with_the_platform():
    access = CmsAccess(configured=True, platform_linked=False)

    assert access.can_use is False
    assert "Unione" in access.reason


def test_both_conditions_are_enough():
    access = CmsAccess(configured=True, platform_linked=True)

    assert access.can_use is True
    assert access.reason is None
