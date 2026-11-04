"""Schemas for the one-time handoff to the drama app (#2302).

The redemption answers in the shape the Unione portal uses for its own tickets -
`success`/`code`/`message`/`token`/`principal` - because the app on the other
end already speaks it: it redeems a platform ticket exactly this way, and a
second dialect for the same exchange would be a second parser to keep honest.

`camelCase` on the wire is the platform's spelling, not this codebase's: the
fields are named as Python names here and aliased only when they leave.
"""

from pydantic import Field

from app.schemas.base import BaseSchema


class DramaTicketRead(BaseSchema):
    """A freshly minted ticket: what to hand the app, and how long it lives."""

    ticket: str = Field(description="`tk-` and 32 hex digits; single use, two minutes")
    expires_at: str = Field(description="When a redemption stops being accepted")
    redeem_path: str = Field(
        default="/api/drama/access",
        description="Where the app redeems it, relative to this deployment's address",
    )


class DramaPrincipal(BaseSchema):
    """Who the ticket was minted for, in the platform's `UserPrincipal` spelling."""

    id: int = Field(description="Platform user id, or a stable stand-in for one")
    tenant_id: int = Field(serialization_alias="tenantId")
    username: str
    real_name: str | None = Field(default=None, serialization_alias="realName")
    org_name: str | None = Field(default=None, serialization_alias="orgName")
    email: str | None = None
