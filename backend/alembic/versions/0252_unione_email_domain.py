"""Platform accounts get an address that survives our own validation (#2101).

Accounts created by the Unione bridge had no address of their own - the platform's
`UserPrincipal` carries none - so the sign-in minted `{username}@unione.local`. The
domain was chosen to be unroutable (RFC 6762 reserves `.local` for link-local
names), which it is, but `email-validator` also refuses it: RFC 6761 special-use
names (`local`, `localhost`, `invalid`, `test`, `onion`, `arpa`) are rejected as
addresses. Every platform account therefore signed in successfully and then got a
500 from `GET /auth/me`, which validates the column through `EmailStr`.

`.internal` is reserved for private use and not on that list, so it keeps both
properties: nothing can mail it, and it is a syntactically valid address.

The rewrite is idempotent and scoped to the bridge's own accounts - an address a
person typed is never touched, even if it happens to end in `.local`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0252_unione_email_domain"
down_revision: str | Sequence[str] | None = "0251_kb_reasoning_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY = "@unione.local"
_CURRENT = "@unione.internal"


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE users SET email = replace(email, :legacy, :current) "
            "WHERE oauth_provider = 'unione' AND email LIKE :pattern"
        ).bindparams(legacy=_LEGACY, current=_CURRENT, pattern=f"%{_LEGACY}")
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE users SET email = replace(email, :current, :legacy) "
            "WHERE oauth_provider = 'unione' AND email LIKE :pattern"
        ).bindparams(legacy=_LEGACY, current=_CURRENT, pattern=f"%{_CURRENT}")
    )
