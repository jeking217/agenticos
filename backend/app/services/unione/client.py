"""Signing in through the Unione platform, bridged to a local account (#2101).

Unione owns the accounts. This deployment is one more thing a person reaches from
it, so the platform's own authentication is the one that has to happen - either
the platform says who they are (a one-time `tk-` ticket for the session it already
gave them), or they type its account and password into our form and we relay them.

What reaches the platform is the platform's own protocol, not ours:

* The password is SM2-encrypted in the browser, with the key this deployment
  publishes, and what arrives here is already ciphertext: this process cannot read
  it, does not need to, and relays it unchanged. Nothing on our side sees a
  plaintext password, which is the point of doing it there rather than here.
* The captcha answer the platform checked is identified by a `CAPTCHAID` header,
  because the cookie the platform also sets belongs to a browser that is not the
  one making this call.
* A rejected sign-in answers HTTP 200 with `success=false` and a sentence written
  for the person reading it. That sentence is the one we pass on - it is the only
  place that knows whether it was the password, the code, or a lockout.

The platform token obtained on the way is used once, for the identity it carries,
and then dropped: it is never stored, never logged, and never used to authenticate
a later request. Everything after the sign-in runs on this deployment's own session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import AuthenticationError, ExternalServiceError

logger = logging.getLogger(__name__)

#: The platform names its own token cookie and header `token`; we send the ticket
#: in the query string instead, which is what `SessionController#access` reads.
_TICKET_PARAM = "accessToken"


@dataclass(frozen=True, slots=True)
class UnioneIdentity:
    """Who the platform says the person is, and the token it said so with.

    `subject` is `{tenantId}:{id}`, which is what a local account is keyed on: a
    Unione user id is unique within its tenant and nowhere else, so an id alone
    would let one tenant's user 42 be another tenant's user 42.
    """

    subject: str
    user_id: int | None
    tenant_id: int | None
    username: str
    email: str | None
    full_name: str | None
    token: str


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _identity_from(payload: dict[str, Any]) -> UnioneIdentity:
    """Read a `LoginResult` into the three things we keep, refusing an unusable one.

    The platform's `UserPrincipal` carries no address, so `email` is normally
    `None` and the local address is synthesized from the username. It is read
    anyway: a platform that starts reporting one should not need this changed,
    and a deployment that patches it in should be honoured.
    """
    principal = payload.get("principal")
    token = payload.get("token")
    if not isinstance(principal, dict) or not isinstance(token, str) or not token:
        logger.warning("unione_login_result_incomplete")
        raise ExternalServiceError(message="The account platform answered without a session")

    username = str(principal.get("username") or "").strip()
    subject_id = _as_int(principal.get("id"))
    tenant_id = _as_int(principal.get("tenantId"))
    if not username or subject_id is None or tenant_id is None:
        logger.warning("unione_principal_incomplete")
        raise ExternalServiceError(message="The account platform answered without an identity")

    email = principal.get("email")
    return UnioneIdentity(
        subject=f"{tenant_id}:{subject_id}",
        user_id=subject_id,
        tenant_id=tenant_id,
        username=username,
        email=str(email).strip() if isinstance(email, str) and email.strip() else None,
        full_name=str(principal.get("realName") or principal.get("aliasName") or "").strip()
        or None,
        token=token,
    )


class UnioneClient:
    """The platform's login endpoints, over the deployment's configuration.

    One instance per request, holding no state: the settings it reads are process
    configuration, and the HTTP client it opens per call is the pattern this
    repository uses for a portal integration (`services/portals/github.py`).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or settings.UNIONE_BASE_URL).rstrip("/")
        self._timeout = timeout if timeout is not None else settings.UNIONE_TIMEOUT_SECONDS
        # The seam the protocol tests use: an `httpx.MockTransport` answering as the
        # portal does, so the wire format is checked without a portal to check it
        # against. `None` in every real call.
        self._transport = transport

    # -- transport ---------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One call to the portal, with every transport failure turned into ours.

        The URL is joined by hand rather than through `httpx`'s `base_url`: that
        merger *replaces* the base path when the request path starts with `/`, so
        `http://host:8080/portal` plus `/api/security/...` would quietly address
        the host root and 404 - and the portal lives under a context path.
        """
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            # The URL and the failure kind, never the body: a rejected sign-in's
            # body echoes what was tried.
            logger.warning(
                "unione_unreachable",
                extra={"path": path, "error": type(exc).__name__},
            )
            raise ExternalServiceError(
                message="The account platform is unavailable. Try again in a moment."
            ) from exc
        if response.status_code >= 500:
            logger.warning(
                "unione_failed", extra={"path": path, "status": response.status_code}
            )
            raise ExternalServiceError(
                message="The account platform is unavailable. Try again in a moment."
            )
        return response

    @staticmethod
    def _envelope(response: httpx.Response, *, action: str) -> dict[str, Any]:
        """The `Results`/`LoginResult` body, with a refusal re-raised as that refusal.

        A platform that answers `success=false` is telling the person something
        only it knows - the password is wrong, the code expired, the account is
        locked until a quarter past - so its sentence is the message this
        deployment returns. Anything unparseable is a platform fault, not theirs.
        """
        try:
            body = response.json()
        except ValueError as exc:
            logger.warning("unione_response_not_json", extra={"action": action})
            raise ExternalServiceError(
                message="The account platform answered with something unreadable"
            ) from exc
        if not isinstance(body, dict):
            logger.warning("unione_response_not_object", extra={"action": action})
            raise ExternalServiceError(
                message="The account platform answered with something unreadable"
            )
        if not body.get("success"):
            message = str(body.get("message") or "").strip()
            logger.info("unione_refused", extra={"action": action})
            raise AuthenticationError(message=message or "The account platform refused the sign-in")
        return body

    # -- endpoints ---------------------------------------------------------

    async def captcha_image(self) -> tuple[str, bytes, str]:
        """A captcha image, the id it is filed under, and its media type."""
        response = await self._request("GET", "/api/security/captcha/image")
        if response.status_code != 200 or not response.content:
            logger.warning("unione_captcha_failed", extra={"status": response.status_code})
            raise ExternalServiceError(message="The account platform did not draw a captcha")
        # The id is a header, and the cookie beside it is for a browser that is
        # not the one asking - so the header is the only copy that reaches us.
        captcha_id = response.headers.get("CAPTCHAID") or ""
        if not captcha_id:
            logger.warning("unione_captcha_without_id")
            raise ExternalServiceError(message="The account platform did not draw a captcha")
        media_type = response.headers.get("Content-Type") or "image/jpeg"
        return captcha_id, response.content, media_type.split(";")[0]

    async def login_uname(
        self,
        *,
        username: str,
        password_ciphertext: str,
        captcha: str | None = None,
        captcha_id: str | None = None,
    ) -> UnioneIdentity:
        """The platform's account-and-password sign-in, relayed.

        `password_ciphertext` is forwarded as it arrived: the browser encrypted it
        with the platform's public key, and re-encrypting here would need a second
        copy of the arithmetic to encrypt what is already encrypted.
        """
        payload: dict[str, Any] = {
            "username": username,
            "password": password_ciphertext,
        }
        if captcha:
            payload["captcha"] = captcha
        headers = {"CAPTCHAID": captcha_id} if captcha_id else {}
        response = await self._request(
            "POST", "/api/security/login/uname", json=payload, headers=headers
        )
        return _identity_from(self._envelope(response, action="login"))

    async def login_sms(self, *, phone: str, captcha: str) -> UnioneIdentity:
        """The platform's phone-code sign-in, relayed."""
        response = await self._request(
            "POST",
            "/api/security/login/sms",
            json={"userphone": phone, "captcha": captcha},
        )
        return _identity_from(self._envelope(response, action="login-sms"))

    async def send_sms_captcha(self, *, phone: str) -> None:
        """Ask the platform to text a sign-in code to this number.

        The scene is the platform's own vocabulary - `login` - and it is what
        decides which template is sent and which code the sign-in accepts.
        """
        response = await self._request(
            "POST", "/api/security/captcha/sms", json={"tel": phone, "scene": "login"}
        )
        self._envelope(response, action="send-captcha")

    async def consume_ticket(self, ticket: str) -> UnioneIdentity:
        """Exchange a `tk-` ticket for the platform session it stands for.

        The ticket is spent by the platform whether or not it was still valid, so a
        retry after a failure is not a second attempt at the same sign-in: it is a
        refusal, and the person is back at the form.
        """
        response = await self._request(
            "GET",
            settings.UNIONE_TICKET_PATH,
            params={_TICKET_PARAM: ticket},
        )
        body = self._envelope(response, action="once")
        return _identity_from(body)
