"""Signing in through the Unione platform: three doors, one session (#2101).

The platform is where the accounts live, so all three of these end the same way -
a local account resolved from the platform's identity, and this deployment's own
access and refresh tokens - and differ only in how the identity was obtained: the
account and password relayed through the platform's form, a phone code the platform
texted, or the `tk-` ticket its own portal hands over so an already signed-in person
arrives here already signed in.

Every one of them is a public route: there is no `CurrentUser` on any of them, and
by the shape of this API that is what makes them reachable before a session exists.
An installation that configured no bridge answers 404 on all of them, as an
unconfigured OIDC issuer does, so which sign-in methods a deployment runs is not
something a stranger can enumerate.
"""

import base64
import logging
from typing import Any

from fastapi import APIRouter, Request, status

from app.api.deps import (
    DBSession,
    SessionSvc,
    UnioneApi,
    UnioneSignInSvc,
    enforce_auth_limit,
)
from app.core.audit import record_audit
from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.security import create_access_token, create_refresh_token
from app.db.models.user import User
from app.schemas.token import Token
from app.schemas.unione import (
    UnioneCaptchaRead,
    UnioneConfigRead,
    UnioneLogin,
    UnioneSmsCaptchaRequest,
    UnioneSmsLogin,
    UnioneTicketLogin,
)
from app.services.cms_sites import CmsSitesService
from app.services.unione.client import UnioneIdentity

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_enabled() -> None:
    """Refuse like an unconfigured provider does, rather than half-working."""
    if not settings.unione_sign_in_enabled:
        raise NotFoundError(message="Unknown sign-in provider")


def _local_login_visible() -> bool:
    """Whether the login page should also draw the local form.

    Two ways into one account is a question every person has to answer, and the
    platform is the answer - so the form is hidden by default. It stays for a
    deployment that never switched the bridge on (there would be nothing else to
    show), and for one that asked for it as a break-glass way in when the platform
    is down.
    """
    return settings.UNIONE_ALLOW_LOCAL_LOGIN or not settings.unione_sign_in_enabled


def _client_address(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _issue_session(
    request: Request,
    *,
    db: DBSession,
    session_service: SessionSvc,
    user: User,
    action: str,
    identity: UnioneIdentity,
) -> Token:
    """Mint this deployment's tokens for an account the platform vouched for.

    What is issued here is an ordinary local session, which is what makes pairing,
    API keys, sockets and the OAuth server work unchanged - none of them learn that
    a platform was involved.

    The platform's token is a different thing kept alongside it. The CMS knows a
    caller by that token and by nothing else, and the platform hands it out once,
    at sign-in - so this is the only moment in the whole session when it exists.
    Sealing it into `cms_session_tokens` (see `app.cms.tokens`) is what lets the
    Web apps module call the CMS as this person without asking them to copy a
    token out of a browser tab by hand; signing out forgets it.
    """
    subject = identity.subject
    refresh_token = create_refresh_token(
        subject=str(user.id), credential_version=user.credential_version
    )
    session = await session_service.create_session(
        user_id=user.id,
        refresh_token=refresh_token,
        ip_address=_client_address(request),
        user_agent=request.headers.get("User-Agent"),
    )
    access_token = create_access_token(subject=str(user.id), sid=str(session.id))
    await _remember_platform_token(db, user, identity.token)
    # The trail records who signed in and how; the platform's subject is the stable
    # half of that identity, and the token that carried it is not written anywhere.
    await record_audit(
        db,
        actor_user_id=user.id,
        action=action,
        target_type="user",
        target_id=str(user.id),
        details={"provider": "unione", "unione_subject": subject},
        ip_address=_client_address(request),
    )
    return Token(access_token=access_token, refresh_token=refresh_token)


async def _remember_platform_token(db: DBSession, user: User, token: str) -> None:
    """Keep the platform token for the modules that speak to the CMS for this person.

    Only when there is a CMS to speak to: a secret nobody will present is a secret
    stored for no reason, and a deployment that never configured one should not be
    accumulating them.
    """
    if not token or not settings.CMS_BASE_URL:
        return
    await CmsSitesService(db).remember_token(user, None, token)


@router.get("/unione/config", response_model=UnioneConfigRead)
async def unione_config() -> Any:
    """What the login page needs before it can draw a form.

    Answered even when the bridge is off, with `enabled=false`: the page asks this
    deployment rather than assuming, and a 404 would leave it guessing.
    """
    enabled = settings.unione_sign_in_enabled
    return UnioneConfigRead(
        enabled=enabled,
        captcha_enabled=settings.UNIONE_CAPTCHA_ENABLED,
        local_login_visible=_local_login_visible(),
        # The key the form encrypts the password with. Published only when the bridge
        # is on: an installation that never configured one should not be advertising
        # the shape of an integration it does not have.
        sm2_public_key=settings.UNIONE_SM2_PUB_KEY if enabled else "",
    )


@router.get("/unione/captcha", response_model=UnioneCaptchaRead)
async def unione_captcha(request: Request, platform: UnioneApi) -> Any:
    """The platform's captcha image, as a data URL, with the id to quote back.

    Proxied rather than linked from the page: the platform's image endpoint files the
    answer against an id it returns in a header and a cookie, and only the header is
    readable by the backend that will have to send the answer. Handing the page a
    URL to load directly would leave the id on the wrong side of the browser.
    """
    _require_enabled()
    await enforce_auth_limit(request, surface="auth_unione_captcha")
    captcha_id, image, media_type = await platform.captcha_image()
    encoded = base64.b64encode(image).decode("ascii")
    return UnioneCaptchaRead(captcha_id=captcha_id, image=f"data:{media_type};base64,{encoded}")


@router.post("/unione/login", response_model=Token)
async def unione_login(
    request: Request,
    body: UnioneLogin,
    platform: UnioneApi,
    sign_in: UnioneSignInSvc,
    session_service: SessionSvc,
    db: DBSession,
) -> Any:
    """Sign in with the platform's own account and password, relayed.

    Rate-limited per address and per account, on its own bucket: a bridge that shared
    a bucket with the local login would let one limit a person is nowhere near
    exhaust the other. `body.password` is the platform's ciphertext, encrypted in the
    browser with the key `/auth/unione/config` publishes: the plaintext never reaches
    this process, and what leaves it for the platform is the same ciphertext.
    """
    _require_enabled()
    await enforce_auth_limit(request, surface="auth_unione_login", identifier=body.username)
    identity = await platform.login_uname(
        username=body.username,
        password_ciphertext=body.password,
        captcha=body.captcha,
        captcha_id=body.captcha_id,
    )
    user = await sign_in.account_for(identity)
    return await _issue_session(
        request,
        db=db,
        session_service=session_service,
        user=user,
        action="unione.login",
        identity=identity,
    )


@router.post("/unione/captcha/sms", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def unione_send_sms_captcha(
    request: Request, body: UnioneSmsCaptchaRequest, platform: UnioneApi
) -> None:
    """Ask the platform to text a sign-in code.

    Limited per address and per number like a sign-in is, because this is the half of
    the phone flow that costs money and rings somebody's phone: an unthrottled relay
    is an SMS bill and a way to page a stranger at 3am.
    """
    _require_enabled()
    await enforce_auth_limit(request, surface="auth_unione_captcha_sms", identifier=body.phone)
    await platform.send_sms_captcha(phone=body.phone)


@router.post("/unione/login-sms", response_model=Token)
async def unione_login_sms(
    request: Request,
    body: UnioneSmsLogin,
    platform: UnioneApi,
    sign_in: UnioneSignInSvc,
    session_service: SessionSvc,
    db: DBSession,
) -> Any:
    """Sign in with a code the platform texted to the platform's account phone."""
    _require_enabled()
    await enforce_auth_limit(request, surface="auth_unione_sms_login", identifier=body.phone)
    identity = await platform.login_sms(phone=body.phone, captcha=body.captcha)
    user = await sign_in.account_for(identity)
    return await _issue_session(
        request,
        db=db,
        session_service=session_service,
        user=user,
        action="unione.login-sms",
        identity=identity,
    )


@router.post("/unione/once", response_model=Token)
async def unione_once(
    request: Request,
    body: UnioneTicketLogin,
    platform: UnioneApi,
    sign_in: UnioneSignInSvc,
    session_service: SessionSvc,
    db: DBSession,
) -> Any:
    """Sign in with the one-time ticket the platform's portal redirected here with.

    The ticket is single-use by construction - the platform removes it whether or not
    it was still valid - so a refresh of this page, or a replayed URL, fails with the
    platform's own "ticket already used" and the person lands back on the form. No
    rate-limit identifier beyond the address: the ticket is the secret, and the
    address is what bounds a caller guessing at one.
    """
    _require_enabled()
    await enforce_auth_limit(request, surface="auth_unione_once")
    identity = await platform.consume_ticket(body.access_token)
    user = await sign_in.account_for(identity)
    return await _issue_session(
        request,
        db=db,
        session_service=session_service,
        user=user,
        action="unione.once",
        identity=identity,
    )
