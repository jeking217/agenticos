"""Assembling a site's flow from a request: who is asking, and with what token.

The domain modules below (`app.cms.*`) know nothing about sessions, settings or
the database. This service is the join: it finds the person's platform token,
reads the site from the CMS, picks the workspace directory, and hands back a
`CmsSiteService` that is already authorised and already bound.

Two rules it enforces, and they are the reason it exists rather than the route
doing it inline:

* **No token, no module.** A person who signed in with this deployment's own
  password has no Unione identity, and therefore no CMS token. They are told to
  sign in with the platform account - not quietly given a service account,
  which would put every change under one name in the CMS's audit trail.
* **The edit door is decided before the editor opens.** `can_edit` is computed
  from the site's owner and the person's role, so the console disables the door
  instead of opening a page that fails on its first save.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.cms.client import CmsClient, CmsSiteRecord
from app.cms.errors import FORBIDDEN, NOT_FOUND, TOKEN_EXPIRED, UNREACHABLE, CmsError
from app.cms.preview import PreviewSigner
from app.cms.service import CmsSiteService
from app.cms.sites import SiteCard, editable_by, site_cards
from app.cms.suggestions import Suggestion, after_pull, after_release, after_verify
from app.cms.tokens import TokenCipher, is_expired
from app.cms.workspace import SiteWorkspace
from app.core.config import settings
from app.db.models.cms import CmsSessionToken, CmsSiteBinding
from app.db.models.user import User

@dataclass(frozen=True)
class CmsAccess:
    """Whether the module can be used, and why not."""

    configured: bool
    platform_linked: bool

    @property
    def can_use(self) -> bool:
        return self.configured and self.platform_linked

    @property
    def reason(self) -> str | None:
        if not self.configured:
            return "这个部署还没有配置 CMS 地址（CMS_BASE_URL）"
        if not self.platform_linked:
            return "请用 Unione 平台账号登录后再使用 Web 应用"
        return None


def platform_identity(user: User) -> tuple[int, int] | None:
    """`(tenantId, userId)` as the platform knows this person, or None.

    Read from the Unione sign-in the same way the drama ticket does: the bridge
    stores `{tenant}:{id}` in `oauth_id`. A local account has nothing here, and
    that is the honest reason it cannot edit a CMS site.
    """
    if user.oauth_provider == "unione" and user.oauth_id:
        tenant, _, subject = user.oauth_id.partition(":")
        try:
            return int(tenant), int(subject)
        except ValueError:
            return None
    return None


class CmsSitesService:
    """The console's view of the CMS, over the request's session."""

    def __init__(self, db) -> None:  # AsyncSession, typed loosely to keep imports light
        self._db = db

    # -- configuration ----------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(settings.CMS_BASE_URL)

    def access_for(self, user: User) -> CmsAccess:
        return CmsAccess(configured=self.configured, platform_linked=platform_identity(user) is not None)

    def _cipher(self) -> TokenCipher:
        return TokenCipher(settings.SECRET_KEY)

    def _signer(self) -> PreviewSigner:
        return PreviewSigner(settings.CMS_PREVIEW_SECRET or settings.SECRET_KEY)

    def _workspace_root(self) -> Path:
        base = settings.CMS_WORKSPACE_DIR or (settings.MEDIA_DIR / "cms")
        return Path(base)

    def workspace_for(self, user: User, site_id: int) -> SiteWorkspace:
        """One directory per person per site.

        Per person, not per site: two editors of the same site must not share a
        working tree, or each would see the other's half-finished edits as
        their own diff.
        """
        return SiteWorkspace(self._workspace_root() / str(user.id) / str(site_id))

    # -- tokens -----------------------------------------------------------

    async def remember_token(
        self,
        user: User,
        organization_id: uuid.UUID | None,
        token: str,
        *,
        expires_at: datetime | None = None,
    ) -> None:
        """Store the person's platform token, replacing whatever was there.

        Called from the Unione sign-in, which is the one place the platform's token
        exists: it is handed out once, alongside the identity it names, and there is
        no endpoint to ask for another one later. Nothing here checks it against the
        CMS first - the token came from the platform's own `LoginResult`, and a
        round trip at sign-in would only delay the login to learn something the
        first real call already answers.
        """
        if not token:
            raise ValueError("there is no empty token to remember")
        identity = platform_identity(user)
        row = await self._token_row(user.id)
        cipher = self._cipher().seal(token)
        if row is None:
            row = CmsSessionToken(
                user_id=user.id,
                organization_id=organization_id,
                platform="unione",
                tenant_id=identity[0] if identity else None,
                token_cipher=cipher,
                expires_at=expires_at,
            )
            self._db.add(row)
        else:
            row.token_cipher = cipher
            row.expires_at = expires_at
            row.revoked_at = None
            row.organization_id = organization_id or row.organization_id
        await self._db.flush()

    async def has_token(self, user: User) -> bool:
        """Whether there is a token worth trying, so the console knows what to say.

        An expired one counts as absent: the only thing the console does with this
        answer is decide whether to go straight to the site list or ask for a fresh
        sign-in, and it would be asking for one that a token on file cannot avoid.
        """
        row = await self._token_row(user.id)
        if row is None or row.revoked_at is not None:
            return False
        return not is_expired(row.expires_at)

    async def forget_token(self, user: User) -> None:
        row = await self._token_row(user.id)
        if row is not None:
            row.revoked_at = datetime.now(UTC)
            await self._db.flush()

    async def _token_row(self, user_id: uuid.UUID) -> CmsSessionToken | None:
        result = await self._db.execute(
            select(CmsSessionToken).where(
                CmsSessionToken.user_id == user_id, CmsSessionToken.platform == "unione"
            )
        )
        return result.scalar_one_or_none()

    async def token_for(self, user: User) -> str:
        """The person's token, or `cms_token_expired`.

        The distinction between "never stored" and "expired" is not shown to the
        person: both mean "sign in again", and the console says that once.
        """
        row = await self._token_row(user.id)
        if row is None or row.revoked_at is not None:
            raise CmsError(TOKEN_EXPIRED, "还没有可用的平台令牌，请重新登录")
        if is_expired(row.expires_at):
            raise CmsError(TOKEN_EXPIRED, "平台令牌即将过期，请重新登录")
        return self._cipher().unseal(row.token_cipher)

    # -- sites ------------------------------------------------------------

    async def list_sites(self, user: User, *, roles: list[str] | None = None) -> list[SiteCard]:
        """The sites this person may open, with the edit door already decided."""
        token = await self.token_for(user)
        held = _roles_for(user, roles)
        identity = platform_identity(user)
        platform_user_id = identity[1] if identity else 0

        client = self._client(token)
        try:
            sites = await client.find_sites()
        finally:
            await client.aclose()
        return site_cards(sites, user_id=platform_user_id, roles=held)

    async def site(
        self, user: User, site_id: int, *, roles: list[str] | None = None
    ) -> CmsSiteRecord:
        """One site, refusing one this person may not change.

        The refusal is `cms_forbidden` rather than `cms_not_found`: the site
        exists, and telling somebody it does not would send them looking for a
        typo in a URL that is correct.
        """
        token = await self.token_for(user)
        client = self._client(token)
        try:
            site = await client.site_detail(site_id)
        finally:
            await client.aclose()
        identity = platform_identity(user)
        editable, reason = editable_by(
            site,
            user_id=identity[1] if identity else 0,
            roles=_roles_for(user, roles),
        )
        if not editable:
            raise CmsError(FORBIDDEN, reason or "你没有这个站点的编辑权限")
        return site

    async def flow(self, user: User, site_id: int, *, roles: list[str] | None = None) -> CmsSiteService:
        """A bound, authorised flow for one site."""
        token = await self.token_for(user)
        client = self._client(token)
        site = await self.site(user, site_id, roles=roles)
        return CmsSiteService(
            client=client,
            workspace=self.workspace_for(user, site_id),
            site=site,
            user_id=(platform_identity(user) or (0, 0))[1],
            allowed_ext=frozenset(_DEFAULT_EXT),
            max_file_bytes=2 * 1024 * 1024,
            signer=self._signer(),
            # Relative, so the browser resolves it against the console's own
            # origin - which is where the forwarder that keeps the debug cookie
            # server-side lives. An absolute CMS URL here would mean handing the
            # cookie to the browser, which is the one thing this design refuses.
            preview_origin="",
        )

    def _client(self, token: str) -> CmsClient:
        if not self.configured:
            raise CmsError(UNREACHABLE, "这个部署还没有配置 CMS 地址")
        return CmsClient(base_url=settings.CMS_BASE_URL, token=token)

    async def bind(
        self,
        user: User,
        organization_id: uuid.UUID | None,
        site: SiteCard,
    ) -> None:
        """Record that this person opened this site."""
        result = await self._db.execute(
            select(CmsSiteBinding).where(
                CmsSiteBinding.user_id == user.id, CmsSiteBinding.site_id == site.id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = CmsSiteBinding(user_id=user.id, organization_id=organization_id, site_id=site.id)
            self._db.add(row)
        row.title = site.title
        row.ctx = site.ctx
        row.domain = site.domain
        row.space_id = site.space_id
        row.space_vers = site.space_vers
        await self._db.flush()

    # -- suggestions ------------------------------------------------------

    def suggestions_after_pull(self, site_id: int, files: int, preview_url: str | None) -> list[Suggestion]:
        return after_pull(site_id=site_id, files=files, preview_url=preview_url)

    def suggestions_after_verify(self, site_id: int, *, plan, report, preview_url) -> list[Suggestion]:
        return after_verify(
            site_id=site_id,
            plan=plan,
            verified=report.ok,
            failures=report.failures,
            preview_url=preview_url,
        )

    def suggestions_after_release(self, site_id: int, files: int, tag: str | None) -> list[Suggestion]:
        return after_release(site_id=site_id, files=files, tag=tag)


def _roles_for(user: User, roles: list[str] | None) -> list[str]:
    """The roles the CMS will accept for this person, as far as we can know them.

    Only a deployment administrator maps across, because that is the one role
    whose meaning is the same on both sides. An AgenticOS `admin` is an
    administrator *here*, not necessarily a `CMS-ADMIN` there, and inventing
    that equivalence would light up an edit door the CMS then refuses - which
    reads to the person as a broken console rather than as a missing role.

    Being wrong in the other direction is harmless: the probe says "no" for
    somebody the CMS would have allowed, and they simply use the CMS's own
    console for that site.
    """
    held = list(roles or [])
    if getattr(user, "is_superuser", False):
        held.append("SUPPERADMIN")
    return held


#: The extension allow-list the console uses. Kept beside the capability's
#: default so the two cannot disagree about what a site is made of.
_DEFAULT_EXT = (
    "html",
    "htm",
    "css",
    "js",
    "mjs",
    "json",
    "svg",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "ico",
    "woff",
    "woff2",
    "ttf",
    "txt",
    "xml",
)
