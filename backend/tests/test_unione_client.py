"""The bridge's half of the platform's protocol (#2101).

Each test stands a `MockTransport` where the portal would be and asserts what
actually crossed the wire: the password as SM2 ciphertext the platform can decrypt,
the captcha answer identified by header rather than cookie, the ticket as the query
parameter `SessionController#access` reads, and a refusal arriving as the platform's
own sentence rather than a status code we invented.

That no token, password or ticket reaches the log is asserted too - a bridge is a
place credentials pass through, and the log is the one place they must not stop.
"""

import json
import logging

import httpx
import pytest

from app.core import sm2
from app.core.config import settings
from app.core.exceptions import AuthenticationError, ExternalServiceError
from app.services.unione.client import UnioneClient

pytestmark = pytest.mark.anyio

BASE = "http://unione.test/portal"
PRIVATE_KEY = "4389c4329a001df855d14ec204d25bf12d4d631bf61bb02d0586a653e199e0da"
#: The shipped pair's public half: what the bridge encrypts to by default.
_PUBLIC_KEY = (
    "04a684d7140aebfc83515a2312dac7ab836f3adc484627971b6936ad7d5575048c"
    "aecf9c7eb07ed4dcee9d04cb3a7b1c514477b646b1ebe8245de215733baa5133"
)

_PRINCIPAL = {
    "id": 42,
    "tenantId": 7,
    "orgId": 3,
    "orgName": "总部",
    "username": "zhangsan",
    "tel": "13800000000",
    "realName": "张三",
}
_LOGIN_OK = {"success": True, "code": 200, "message": "登录成功", "token": "tok-1", "principal": _PRINCIPAL}


def _ciphertext(plaintext: str) -> str:
    """What the login form sends: the platform's ciphertext, made in the browser."""
    return sm2.encrypt(plaintext.encode(), _PUBLIC_KEY)


def _client(handler: object) -> UnioneClient:
    return UnioneClient(base_url=BASE, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _platform_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "UNIONE_BASE_URL", BASE)
    monkeypatch.setattr(settings, "UNIONE_SM2_PUB_KEY", _PUBLIC_KEY)


class TestLoginUname:
    async def test_sends_the_password_the_platform_can_decrypt(self) -> None:
        """The ciphertext, not the password - and C1C3C2 hex, which is what Hutool reads."""
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.read().decode()
            seen["captcha_id"] = request.headers.get("CAPTCHAID")
            return httpx.Response(200, json=_LOGIN_OK)

        client = _client(handler)
        identity = await client.login_uname(
            username="zhangsan",
            password_ciphertext=_ciphertext("Uni@12345"),
            captcha="8J2K",
            captcha_id="captcha-1",
        )

        assert seen["url"] == f"{BASE}/api/security/login/uname"
        assert seen["captcha_id"] == "captcha-1"
        body = json.loads(seen["body"])
        assert body["username"] == "zhangsan"
        assert body["captcha"] == "8J2K"
        assert "Uni@12345" not in seen["body"]
        assert sm2.decrypt(body["password"], PRIVATE_KEY) == b"Uni@12345"

        assert identity.subject == "7:42"
        assert identity.username == "zhangsan"
        assert identity.full_name == "张三"
        assert identity.token == "tok-1"

    async def test_a_refusal_keeps_the_platforms_sentence(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": False, "code": 500, "message": "密码错误"})

        with pytest.raises(AuthenticationError) as raised:
            await _client(handler).login_uname(username="zhangsan", password_ciphertext=_ciphertext("wrong"))

        assert raised.value.message == "密码错误"

    async def test_a_platform_fault_is_not_a_credentials_answer(self) -> None:
        """A 500 must not read as "wrong password" - it is the platform being broken,
        which the person cannot fix by trying a different password."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="bad gateway")

        with pytest.raises(ExternalServiceError):
            await _client(handler).login_uname(username="zhangsan", password_ciphertext=_ciphertext("x"))

    async def test_an_unreachable_platform_is_reported_as_such(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        with pytest.raises(ExternalServiceError):
            await _client(handler).login_uname(username="zhangsan", password_ciphertext=_ciphertext("x"))

    async def test_an_answer_without_a_session_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "code": 200})

        with pytest.raises(ExternalServiceError):
            await _client(handler).login_uname(username="zhangsan", password_ciphertext=_ciphertext("x"))



class TestCaptcha:
    async def test_reads_the_id_from_the_header(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == f"{BASE}/api/security/captcha/image"
            return httpx.Response(
                200, content=b"\xff\xd8jpeg", headers={"CAPTCHAID": "cid-9", "Content-Type": "image/jpeg"}
            )

        captcha_id, image, media_type = await _client(handler).captcha_image()

        assert (captcha_id, image, media_type) == ("cid-9", b"\xff\xd8jpeg", "image/jpeg")

    async def test_an_image_without_an_id_is_an_error(self) -> None:
        """The id is what the answer is filed under; an image without one cannot be used."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"\xff\xd8jpeg")

        with pytest.raises(ExternalServiceError):
            await _client(handler).captcha_image()


class TestTicket:
    async def test_asks_access_with_the_parameter_name_the_portal_reads(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            return httpx.Response(200, json=_LOGIN_OK)

        identity = await _client(handler).consume_ticket("tk-abc")

        assert seen["method"] == "GET"
        assert seen["url"] == f"{BASE}/api/session/access?accessToken=tk-abc"
        assert identity.subject == "7:42"

    async def test_a_spent_ticket_surfaces_the_portals_message(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"success": False, "code": 500, "message": "票据无效或已使用"}
            )

        with pytest.raises(AuthenticationError) as raised:
            await _client(handler).consume_ticket("tk-spent")

        assert raised.value.message == "票据无效或已使用"

    async def test_the_ticket_path_is_configurable(self, monkeypatch) -> None:
        """A deployment that mounted the portal elsewhere is not stuck with the default."""
        monkeypatch.setattr(settings, "UNIONE_TICKET_PATH", "/api/session/access2")
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=_LOGIN_OK)

        await _client(handler).consume_ticket("tk-abc")

        assert "/api/session/access2?" in seen["url"]


class TestSms:
    async def test_send_uses_the_platforms_scene_vocabulary(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.read().decode()
            return httpx.Response(200, json={"success": True, "code": 200})

        await _client(handler).send_sms_captcha(phone="13800000000")

        body = json.loads(seen["body"])
        assert body == {"tel": "13800000000", "scene": "login"}

    async def test_sign_in_forwards_the_phone_and_the_code(self) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.read().decode()
            return httpx.Response(200, json=_LOGIN_OK)

        identity = await _client(handler).login_sms(phone="13800000000", captcha="123456")

        assert seen["url"] == f"{BASE}/api/security/login/sms"
        assert json.loads(seen["body"]) == {
            "userphone": "13800000000",
            "captcha": "123456",
        }
        assert identity.subject == "7:42"


class TestWhatIsWrittenDown:
    async def test_no_secret_reaches_the_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """Neither the password, the platform token, nor the ticket: a bridge is where
        they pass through, and the log is where they must not stop."""

        def refused(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": False, "code": 500, "message": "密码错误"})

        def broken(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        caplog.set_level(logging.DEBUG)
        with pytest.raises(AuthenticationError):
            await _client(refused).login_uname(username="zhangsan", password_ciphertext=_ciphertext("Sup3rSecret!"))
        with pytest.raises(ExternalServiceError):
            await _client(broken).consume_ticket("tk-verysecret")

        assert "Sup3rSecret!" not in caplog.text
        assert "tk-verysecret" not in caplog.text
        assert "tok-1" not in caplog.text
