"""Handing a signed-in person to the drama app (#2302).

Two doors, and only one of them needs a session:

* `POST /drama/tickets` mints a ticket for whoever is signed in. It is the
  console asking, on the person's behalf, for a way into an app on the same
  computer - so it is behind the session, and the ticket names that session's
  person and nobody else.
* `GET /drama/access` spends it. Public by necessity: the app redeeming it has
  no session here yet, and the ticket *is* the credential. It is single use,
  which is what keeps a public endpoint from being a way in.

The answer is the platform's envelope, `success`/`code`/`message`/`token`/
`principal`, in HTTP 200 even when it refuses - the app already reads the
platform's tickets that way, and the person has already seen this shape's
refusals in the portal's own words.
"""

import secrets
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DramaTicketSvc, enforce_auth_limit
from app.schemas.drama import DramaTicketRead
from app.services.drama_ticket import REDEEM_PATH, platform_identity, username_for

router = APIRouter()

#: The platform's sentence for a ticket that was never valid or is already spent.
#: Mirrored rather than rewritten: it is what the person has seen before, and a
#: second wording for one situation is a second thing to translate.
_REFUSED = "票据无效或已使用"


@router.post("/tickets", response_model=DramaTicketRead)
async def mint_ticket(current_user: CurrentUser, tickets: DramaTicketSvc) -> Any:
    """A one-time ticket for the signed-in person, to hand to the drama app."""
    ticket, expires_at = await tickets.issue(current_user.id)
    return DramaTicketRead(
        ticket=ticket,
        expires_at=expires_at.isoformat(),
        redeem_path=REDEEM_PATH,
    )


@router.get("/access")
async def redeem_ticket(
    request: Request,
    tickets: DramaTicketSvc,
    access_token: str = Query(
        alias="accessToken",
        min_length=1,
        max_length=256,
        description="The `tk-` ticket the console minted",
    ),
) -> JSONResponse:
    """Spend a ticket and answer who it was minted for.

    Rate-limited by address, like the platform's own redemption: the ticket is
    the secret, and an address is what bounds somebody guessing at one.
    """
    await enforce_auth_limit(request, surface="auth_drama_access")
    user = await tickets.redeem(access_token)
    if user is None:
        return JSONResponse(
            status_code=200,
            content={"success": False, "code": 401, "message": _REFUSED},
        )

    tenant_id, user_id = platform_identity(user)
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "code": 0,
            "message": "",
            # The app stores this as the token of the session it now holds. It is
            # not a credential for anything here - the app's session is local to
            # it - so it is born, stored and never presented.
            "token": f"ds-{secrets.token_urlsafe(24)}",
            "principal": {
                "id": user_id,
                "tenantId": tenant_id,
                "username": username_for(user),
                "realName": user.full_name,
                "email": user.email,
            },
        },
    )
