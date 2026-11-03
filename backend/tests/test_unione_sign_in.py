"""Turning the platform's identity into an account here (#2101).

The three rules worth pinning: which address an identity is given, that an address a
local account already holds is refused rather than taken over, and that a deployment
which does not auto-provision says so instead of creating an account.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import EmailStr, TypeAdapter

from app.core.config import settings
from app.core.exceptions import AuthenticationError, AuthorizationError, ConflictError
from app.repositories import user_repo
from app.services import unione as unione_pkg
from app.services.unione import sign_in as sign_in_module
from app.services.unione.client import UnioneIdentity
from app.services.unione.sign_in import UnioneSignInService, email_for

pytestmark = pytest.mark.anyio


def _identity(**overrides: object) -> UnioneIdentity:
    fields = {
        "subject": "7:42",
        "user_id": 42,
        "tenant_id": 7,
        "username": "zhangsan",
        "email": None,
        "full_name": "张三",
        "token": "tok-1",
    }
    return UnioneIdentity(**{**fields, **overrides})  # type: ignore[arg-type]


class TestAddress:
    def test_the_platforms_own_address_wins(self) -> None:
        """A platform that reports one is believed: it is an address somebody can reach."""
        assert email_for(_identity(email="ZhangSan@Example.com")) == "zhangsan@example.com"

    def test_the_username_when_there_is_no_address(self) -> None:
        """`UserPrincipal` carries no email, so this is the ordinary path."""
        assert email_for(_identity()) == "zhangsan@unione.internal"

    def test_the_id_when_the_username_is_not_an_address(self) -> None:
        """A username past 40 characters is a DN or a UUID, not a local part."""
        assert email_for(_identity(username="x" * 41)) == "42@unione.internal"

    def test_characters_an_address_cannot_carry_are_replaced(self) -> None:
        """The stored value has to survive `EmailStr` on the way back out of `/auth/me`."""
        assert email_for(_identity(username="张三")) == "42@unione.internal"

    def test_a_mixed_username_keeps_what_it_can(self) -> None:
        assert email_for(_identity(username="Zhang San")) == "zhang-san@unione.internal"

    def test_a_leading_dot_is_not_kept(self) -> None:
        """`.x@…` is not a legal local part."""
        assert email_for(_identity(username=".zhangsan.")) == "zhangsan@unione.internal"

    def test_every_address_this_mints_survives_email_validation(self) -> None:
        """`.local` looked right - unroutable by design - and is refused by `EmailStr`.

        The page after a successful sign-in read this column back through
        `EmailStr`, so the whole bridge answered 500 for every platform account.
        """
        validator = TypeAdapter(EmailStr)
        for identity in (
            _identity(),
            _identity(username="x" * 41),
            _identity(username="张三"),
            _identity(username=".zhangsan."),
        ):
            assert validator.validate_python(email_for(identity))


class TestAccountFor:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No repository, no policy lookup, no welcome mail: these are about the rules."""
        monkeypatch.setattr(user_repo, "get_by_oauth", AsyncMock(return_value=None))
        monkeypatch.setattr(user_repo, "get_by_email", AsyncMock(return_value=None))
        created = SimpleNamespace(id="local-1", is_active=True, email="zhangsan@unione.internal")
        self.provision = AsyncMock(return_value=created)
        monkeypatch.setattr(
            sign_in_module.UserService, "get_or_create_oauth_user", self.provision
        )

    async def test_a_returning_identity_is_matched_on_its_tenant_and_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An id is unique inside a tenant and nowhere else, so the tenant is half the key."""
        existing = SimpleNamespace(is_active=True)
        lookup = AsyncMock(return_value=existing)
        monkeypatch.setattr(user_repo, "get_by_oauth", lookup)

        user = await UnioneSignInService(SimpleNamespace()).account_for(_identity())

        assert user is existing
        lookup.assert_awaited_once()
        assert lookup.await_args.args[1:] == ("unione", "7:42")
        self.provision.assert_not_awaited()

    async def test_a_first_sign_in_creates_the_account_through_the_sign_up_policy(
        self,
    ) -> None:
        """Not by writing a row: the same factory OIDC and directory sign-ins use, so a
        closed deployment is not opened by the bridge."""
        await UnioneSignInService(SimpleNamespace()).account_for(_identity())

        kwargs = self.provision.await_args.kwargs
        assert kwargs["provider"] == "unione"
        assert kwargs["provider_id"] == "7:42"
        assert kwargs["email"] == "zhangsan@unione.internal"
        assert kwargs["full_name"] == "张三"

    async def test_an_address_a_local_account_holds_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Taking it over on the strength of an address would hand one deployment's
        account to whoever controls that name on the platform."""
        monkeypatch.setattr(
            user_repo, "get_by_email", AsyncMock(return_value=SimpleNamespace(is_active=True))
        )

        with pytest.raises(ConflictError):
            await UnioneSignInService(SimpleNamespace()).account_for(_identity())

        self.provision.assert_not_awaited()

    async def test_a_deployment_that_does_not_provision_says_so(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "UNIONE_AUTO_PROVISION", False)

        with pytest.raises(AuthorizationError):
            await UnioneSignInService(SimpleNamespace()).account_for(_identity())

        self.provision.assert_not_awaited()

    async def test_a_deactivated_local_account_refuses_the_platforms_vouch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Offboarding happens in both places; the local half is the one enforced here."""
        monkeypatch.setattr(
            user_repo,
            "get_by_oauth",
            AsyncMock(return_value=SimpleNamespace(is_active=False)),
        )

        with pytest.raises(AuthenticationError):
            await UnioneSignInService(SimpleNamespace()).account_for(_identity())

    async def test_a_newly_created_account_is_still_checked_for_being_usable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The factory can hand back an existing account too - a race, or a policy path -
        so the deactivated check is not only on the branch that read one."""
        self.provision.return_value = SimpleNamespace(id="local-1", is_active=False)

        with pytest.raises(AuthenticationError):
            await UnioneSignInService(SimpleNamespace()).account_for(_identity())


def test_the_package_exports_the_bridge_it_advertises() -> None:
    """`deps` imports from the package root; a rename that misses it is an import error."""
    assert unione_pkg.PROVIDER == "unione"
    assert unione_pkg.UnioneSignInService is UnioneSignInService
    assert unione_pkg.email_for is email_for
