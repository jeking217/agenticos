"""The routes that bridge to the Unione platform (#2101).

What these hold the implementation to: the shape of the answer the login page reads,
that a refused sign-in carries the platform's own sentence rather than a generic 401,
that all three doors end at one local session, and that the audit trail names which
door was used. The platform itself is stood in for - these are about our side of the
bridge; `test_sm2.py` covers the encryption and `test_unione_client.py` the protocol.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.api.routes.v1 import unione_auth
from app.core import sm2
from app.core.config import settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.main import app
from app.services import rate_limit

pytestmark = [pytest.mark.anyio, pytest.mark.security]

_LOGIN = f"{settings.API_V1_STR}/auth/unione/login"
_ONCE = f"{settings.API_V1_STR}/auth/unione/once"
_SMS = f"{settings.API_V1_STR}/auth/unione/login-sms"


class FakePlatform:
    """The portal's login endpoints, recording what they were asked."""

    def __init__(self, identity: object | None = None) -> None:
        self.identity = identity or _identity()
        self.uname_calls: list[dict[str, object]] = []
        self.ticket_calls: list[str] = []
        self.sms_calls: list[dict[str, object]] = []
        self.captcha_calls = 0

    async def captcha_image(self) -> tuple[str, bytes, str]:
        self.captcha_calls += 1
        return "captcha-1", b"\xff\xd8jpegbytes", "image/jpeg"

    async def login_uname(self, **kwargs: object) -> object:
        self.uname_calls.append(kwargs)
        return self.identity

    async def login_sms(self, **kwargs: object) -> object:
        self.sms_calls.append(kwargs)
        return self.identity

    async def send_sms_captcha(self, **kwargs: object) -> None:
        self.sms_calls.append(kwargs)

    async def consume_ticket(self, ticket: str) -> object:
        self.ticket_calls.append(ticket)
        return self.identity


def _identity(subject: str = "7:42") -> SimpleNamespace:
    """What `UnioneClient` hands back: the platform's identity, already normalized."""
    return SimpleNamespace(
        subject=subject,
        user_id=42,
        tenant_id=7,
        username="zhangsan",
        email=None,
        full_name="张三",
        token="platform-token",
    )


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), credential_version=3, is_active=True, email="a@b.test")


def _ciphertext(plaintext: str = "Uni@12345") -> str:
    """What the login form posts: the platform's ciphertext, made in the browser."""
    return sm2.encrypt(plaintext.encode(), settings.UNIONE_SM2_PUB_KEY)


@pytest.fixture
def unione_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "UNIONE_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "UNIONE_BASE_URL", "http://unione.test/portal")


@pytest.fixture
def platform() -> FakePlatform:
    return FakePlatform()


@pytest.fixture
def sign_in(user: SimpleNamespace) -> MagicMock:
    service = MagicMock()
    service.account_for = AsyncMock(return_value=user)
    return service


@pytest.fixture
def user() -> SimpleNamespace:
    return _user()


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The audit write, stood in for: these tests assert the action name, not the chain."""
    recorder = AsyncMock()
    monkeypatch.setattr(unione_auth, "record_audit", recorder)
    return recorder


@pytest.fixture
async def unione_client(
    platform: FakePlatform,
    sign_in: MagicMock,
    audit: AsyncMock,
    mock_db_session: MagicMock,
    mock_redis: MagicMock,
) -> AsyncClient:
    session_service = MagicMock()
    session_service.create_session = AsyncMock(return_value=SimpleNamespace(id=uuid4()))

    app.dependency_overrides[deps.get_unione_client] = lambda: platform
    app.dependency_overrides[deps.get_unione_sign_in_service] = lambda: sign_in
    app.dependency_overrides[deps.get_session_service] = lambda: session_service
    app.dependency_overrides[deps.get_db_session] = lambda: mock_db_session
    app.dependency_overrides[deps.get_redis] = lambda: mock_redis
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


class TestConfig:
    async def test_off_by_default_and_the_local_form_stays(self, client: AsyncClient) -> None:
        """A deployment that configured no bridge keeps the sign-in it had."""
        response = await client.get(f"{settings.API_V1_STR}/auth/unione/config")

        assert response.status_code == 200
        assert response.json() == {
            "enabled": False,
            "captcha_enabled": True,
            "local_login_visible": True,
            # No key either: an installation that configured no bridge has nothing to
            # encrypt to, and advertising one would describe an integration it lacks.
            "sm2_public_key": "",
        }

    async def test_on_hides_the_local_form(
        self, client: AsyncClient, unione_enabled: None
    ) -> None:
        response = await client.get(f"{settings.API_V1_STR}/auth/unione/config")

        assert response.json()["enabled"] is True
        assert response.json()["local_login_visible"] is False
        # What the form encrypts the password with. Public by design - the private
        # half never leaves the platform - and the reason a plaintext password has
        # nowhere to go.
        assert response.json()["sm2_public_key"] == settings.UNIONE_SM2_PUB_KEY

    async def test_break_glass_keeps_the_local_form(
        self, client: AsyncClient, unione_enabled: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "UNIONE_ALLOW_LOCAL_LOGIN", True)

        response = await client.get(f"{settings.API_V1_STR}/auth/unione/config")

        assert response.json()["local_login_visible"] is True

    async def test_the_captcha_switch_follows_the_setting(
        self, client: AsyncClient, unione_enabled: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "UNIONE_CAPTCHA_ENABLED", False)

        response = await client.get(f"{settings.API_V1_STR}/auth/unione/config")

        assert response.json()["captcha_enabled"] is False


class TestCaptcha:
    async def test_returns_a_data_url_and_the_id_to_quote(
        self, unione_client: AsyncClient, platform: FakePlatform, unione_enabled: None
    ) -> None:
        """One response carrying both halves: the image the page draws and the id the
        answer has to be filed under."""
        response = await unione_client.get(f"{settings.API_V1_STR}/auth/unione/captcha")

        assert response.status_code == 200
        body = response.json()
        assert body["captcha_id"] == "captcha-1"
        assert body["image"].startswith("data:image/jpeg;base64,")
        assert platform.captcha_calls == 1

    async def test_is_not_found_when_the_bridge_is_off(
        self, unione_client: AsyncClient, platform: FakePlatform
    ) -> None:
        response = await unione_client.get(f"{settings.API_V1_STR}/auth/unione/captcha")

        assert response.status_code == 404
        assert platform.captcha_calls == 0


class TestLogin:
    async def test_issues_this_deployments_own_session(
        self,
        unione_client: AsyncClient,
        platform: FakePlatform,
        sign_in: MagicMock,
        audit: AsyncMock,
        unione_enabled: None,
    ) -> None:
        """The platform's token is spent on the identity; what the caller leaves with
        is an ordinary local token pair."""
        ciphertext = _ciphertext()
        response = await unione_client.post(
            _LOGIN,
            json={
                "username": "zhangsan",
                "password": ciphertext,
                "captcha": "8J2K",
                "captcha_id": "captcha-1",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["access_token"] and body["refresh_token"]
        assert body["token_type"] == "bearer"
        assert platform.uname_calls == [
            {
                "username": "zhangsan",
                # Relayed unchanged: what the browser encrypted is what the platform
                # decrypts, and this service never holds the plaintext.
                "password_ciphertext": ciphertext,
                "captcha": "8J2K",
                "captcha_id": "captcha-1",
            }
        ]
        assert sign_in.account_for.await_count == 1
        assert audit.await_args.kwargs["action"] == "unione.login"
        assert audit.await_args.kwargs["details"]["unione_subject"] == "7:42"

    async def test_passes_the_platforms_refusal_on(
        self, unione_client: AsyncClient, platform: FakePlatform, unione_enabled: None
    ) -> None:
        """The sentence a person needs is the platform's - wrong password, locked
        until 14:05 - and only the platform knows which one it was."""
        platform.login_uname = AsyncMock(
            side_effect=AuthenticationError(message="密码错误，您还可以尝试4次")
        )

        response = await unione_client.post(
            _LOGIN, json={"username": "zhangsan", "password": _ciphertext("wrong")}
        )

        assert response.status_code == 401
        assert response.json()["error"]["message"] == "密码错误，您还可以尝试4次"

    async def test_refuses_to_take_over_an_existing_address(
        self, unione_client: AsyncClient, sign_in: MagicMock, unione_enabled: None
    ) -> None:
        """A local account already at the synthesized address is an administrator's
        decision, not a sign-in's."""
        sign_in.account_for = AsyncMock(
            side_effect=ConflictError(message="Ask an administrator to link the two.")
        )

        response = await unione_client.post(
            _LOGIN, json={"username": "zhangsan", "password": _ciphertext()}
        )

        assert response.status_code == 409

    async def test_is_not_found_when_the_bridge_is_off(
        self, unione_client: AsyncClient, platform: FakePlatform
    ) -> None:
        response = await unione_client.post(
            _LOGIN, json={"username": "zhangsan", "password": _ciphertext()}
        )

        assert response.status_code == 404
        assert platform.uname_calls == []

    async def test_a_plaintext_password_never_reaches_the_platform(
        self, unione_client: AsyncClient, platform: FakePlatform, unione_enabled: None
    ) -> None:
        """The form encrypts before it posts. A body that skipped that step is refused
        here rather than relayed as something the platform cannot decrypt - and a
        bridge that accepted both would be one whose traffic is sometimes readable."""
        response = await unione_client.post(
            _LOGIN, json={"username": "zhangsan", "password": "Uni@12345"}
        )

        assert response.status_code == 422
        assert platform.uname_calls == []


class TestOnce:
    async def test_trades_a_ticket_for_a_session(
        self, unione_client: AsyncClient, platform: FakePlatform, audit: AsyncMock, unione_enabled: None
    ) -> None:
        response = await unione_client.post(_ONCE, json={"accessToken": "tk-abc123"})

        assert response.status_code == 200
        assert response.json()["access_token"]
        assert platform.ticket_calls == ["tk-abc123"]
        assert audit.await_args.kwargs["action"] == "unione.once"

    async def test_a_spent_ticket_is_a_401_not_a_retry(
        self, unione_client: AsyncClient, platform: FakePlatform, unione_enabled: None
    ) -> None:
        platform.consume_ticket = AsyncMock(
            side_effect=AuthenticationError(message="票据无效或已使用")
        )

        response = await unione_client.post(_ONCE, json={"accessToken": "tk-spent"})

        assert response.status_code == 401
        assert response.json()["error"]["message"] == "票据无效或已使用"

    async def test_a_value_that_is_not_a_ticket_never_reaches_the_platform(
        self, unione_client: AsyncClient, platform: FakePlatform, unione_enabled: None
    ) -> None:
        """Refused by the schema, so a forged or truncated value costs no round trip."""
        response = await unione_client.post(_ONCE, json={"accessToken": "bearer-token"})

        assert response.status_code == 422
        assert platform.ticket_calls == []


class TestSms:
    async def test_sends_a_code_through_the_platform(
        self, unione_client: AsyncClient, platform: FakePlatform, unione_enabled: None
    ) -> None:
        response = await unione_client.post(
            f"{settings.API_V1_STR}/auth/unione/captcha/sms", json={"phone": "13800000000"}
        )

        assert response.status_code == 204
        assert platform.sms_calls == [{"phone": "13800000000"}]

    async def test_signs_in_with_the_code(
        self, unione_client: AsyncClient, platform: FakePlatform, audit: AsyncMock, unione_enabled: None
    ) -> None:
        response = await unione_client.post(_SMS, json={"phone": "13800000000", "captcha": "123456"})

        assert response.status_code == 200
        assert response.json()["access_token"]
        assert platform.sms_calls == [{"phone": "13800000000", "captcha": "123456"}]
        assert audit.await_args.kwargs["action"] == "unione.login-sms"


class TestRateLimit:
    @pytest.fixture(autouse=True)
    def _reset_limiter(self):
        yield
        rate_limit.configure(None)

    async def test_a_flood_is_refused_before_the_platform_is_asked(
        self, unione_client: AsyncClient, platform: FakePlatform, unione_enabled: None, monkeypatch
    ) -> None:
        """The platform is the one place a password attempt must not cost anything, so
        the refusal has to land on this side of it."""
        monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_PER_MINUTE", 3)
        counting = MagicMock()
        counting.count_in_window = AsyncMock(return_value=4)
        rate_limit.configure(counting)

        response = await unione_client.post(
            _LOGIN, json={"username": "victim", "password": _ciphertext("guess")}
        )

        assert response.status_code == 429
        assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert platform.uname_calls == []
