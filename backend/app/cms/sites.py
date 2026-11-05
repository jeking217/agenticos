"""What each site card says, and why the edit door is sometimes shut.

The plan's first screen is "the sites this person may open". The CMS answers a
weaker question - every site in the tenant - and its real permission is a role:
`/api/site/find` has no role gate at all, while `resource/{save,verify,release}`
requires super-admin, ops, `CMS-ADMIN` or `CMS-SITEDEV`. So "can see" and "can
change" are different answers, and a console that showed one door for both would
open a page of disabled buttons and call it a permission error.

Hence a card carries `editable` and, when it is false, the reason in the same
words the role gate would use. The probe runs in the list request, so the
console never has to guess and the agent never offers an edit that cannot land.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.cms.client import CmsSiteRecord

#: The roles that may change a site's resources, mirroring the `@Action(roles=)`
#: on `CmsResourceController`. Kept in one place because three callers - the
#: card, the capability and the publish guard - must agree.
EDIT_ROLES = frozenset({"SUPPERADMIN", "SYSOPSUSER", "CMS-ADMIN", "CMS-SITEDEV"})

#: Spellings the platform uses for the two built-in administrators. The CMS
#: annotates `SUPPERADMIN`/`SYSOPSUSER`; `SessionService.hasRole` normalises the
#: underscore, and a token minted by an older build may carry either.
_ADMIN_ALIASES = frozenset({"SUPPERADMIN", "SUPERADMIN", "SYSOPSUSER", "SYSOPS"})

_NOT_YOURS = "这个站点由其他人负责，你没有编辑权限"
_NO_ROLE = "你的平台角色不含站点编辑权限（需要 CMS-ADMIN 或 CMS-SITEDEV）"


@dataclass(frozen=True)
class SiteCard:
    """One site, as the console's list shows it."""

    id: int
    title: str
    ctx: str
    domain: str | None
    cover_url: str | None
    space_id: int | None
    space_vers: int | None
    editable: bool
    reason: str | None

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "ctx": self.ctx,
            "domain": self.domain,
            "coverUrl": self.cover_url,
            "spaceId": self.space_id,
            "spaceVers": self.space_vers,
            "editable": self.editable,
            "reason": self.reason,
        }


def normalise_roles(roles: list[str] | set[str] | tuple[str, ...]) -> frozenset[str]:
    """Upper-case and unify the separators a role can arrive with.

    `CMS_ADMIN`, `CMS-ADMIN` and `cms-admin` are one role; a set that treats
    them as three would grant the wrong thing on a Tuesday when the platform
    changes its spelling.
    """
    return frozenset(role.strip().upper().replace("_", "-") for role in roles if role and role.strip())


def editable_by(site: CmsSiteRecord, *, user_id: int, roles: list[str] | set[str] | tuple[str, ...]) -> tuple[bool, str | None]:
    """Whether this person may change this site, and if not, why.

    Two ways in, and the order matters for the message: being the site's own
    administrator or creator is a fact about the site, while the role is a fact
    about the person. Somebody who is neither is told about the role they are
    missing - which is the thing they can ask an administrator for - rather than
    about an owner they cannot change.
    """
    held = normalise_roles(roles)
    if held & _ADMIN_ALIASES or held & EDIT_ROLES:
        return True, None
    if site.admin_userid is not None and site.admin_userid == user_id:
        return True, None
    if site.created_by is not None and site.created_by == user_id:
        return True, None
    if site.admin_userid is not None and site.admin_userid != user_id:
        return False, _NOT_YOURS
    return False, _NO_ROLE


def site_cards(
    sites: list[CmsSiteRecord],
    *,
    user_id: int,
    roles: list[str] | set[str] | tuple[str, ...],
    cover_base: str = "",
) -> list[SiteCard]:
    """The list the console renders, with the edit door already decided."""
    cards: list[SiteCard] = []
    for site in sites:
        editable, reason = editable_by(site, user_id=user_id, roles=roles)
        cards.append(
            SiteCard(
                id=site.id,
                title=site.title,
                ctx=site.ctx,
                domain=site.domain,
                cover_url=f"{cover_base.rstrip('/')}{site.cover_pic}" if site.cover_pic else None,
                space_id=site.space_id,
                space_vers=site.space_vers,
                editable=editable,
                reason=reason,
            )
        )
    return cards
