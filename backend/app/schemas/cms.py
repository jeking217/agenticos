"""What the console and the agent exchange about a CMS site.

The shapes are flat and explicit rather than nested mirrors of the CMS's own
objects. The console needs to render a card, a file tree, a diff and a
confirmation sheet; sending it a `CmsResource` row with twenty columns it will
never read makes the front end a decoder for somebody else's schema.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CmsStatusRead(BaseModel):
    """Whether the module is usable at all, and for whom."""

    configured: bool = Field(description="A CMS address is set on this deployment")
    platform_linked: bool = Field(description="This person signed in through the platform")
    can_use: bool = Field(description="Both of the above, so the module may be opened")
    has_token: bool = Field(
        default=False,
        description="A token worth trying is on file, so the console need not ask for one",
    )
    reason: str | None = Field(default=None, description="Why not, in the person's words")

    model_config = {"extra": "forbid"}


class CmsTokenRequest(BaseModel):
    """A platform token, handed over once by the person who owns it."""

    token: str = Field(min_length=1, max_length=4000)

    model_config = {"extra": "forbid"}


class SiteCardRead(BaseModel):
    """One site in the picker."""

    id: int
    title: str
    ctx: str
    domain: str | None = None
    cover_url: str | None = None
    space_id: int | None = None
    space_vers: int | None = None
    editable: bool = Field(description="This person may change the site's files")
    reason: str | None = Field(default=None, description="Why the edit door is shut")


class SiteListRead(BaseModel):
    sites: list[SiteCardRead]
    stale_token: bool = Field(
        default=False,
        description="The stored platform token no longer works; sign in again",
    )


class BindSiteRequest(BaseModel):
    """Remember that this person opened this site."""

    site_id: int = Field(ge=1)
    title: str = Field(default="", max_length=200)
    ctx: str = Field(default="", max_length=200)
    domain: str | None = Field(default=None, max_length=200)
    space_id: int | None = None
    space_vers: int | None = None


class FileChangeRead(BaseModel):
    path: str
    status: str = Field(description="A, M or D - the letter the file tree shows")
    size: int = 0
    resource_id: int | None = None


class FileRead(BaseModel):
    """One file's text, as the editor shows it."""

    path: str
    text: str


class DiffRead(BaseModel):
    changes: list[FileChangeRead]
    skipped: list[dict[str, str]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class PullRead(BaseModel):
    ok: bool
    files: int
    skipped: list[dict[str, str]] = Field(default_factory=list)
    baseline: str | None = None
    created: bool = False


class VerifyRead(BaseModel):
    ok: bool
    failures: list[str] = Field(default_factory=list)
    detail: str | None = None
    checked: int = 0


class PushRead(BaseModel):
    ok: bool
    saved: list[str] = Field(default_factory=list)
    upgraded: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    failed: list[dict[str, str]] = Field(default_factory=list)


class DiscardRequest(BaseModel):
    """Which files to undo, by resource path.

    A list rather than one path per call, because the diff card is a list: a
    person who discards three rows should produce one round trip, and one
    partial commit, not three.
    """

    paths: list[str] = Field(min_length=1, max_length=200)


class DiscardRead(BaseModel):
    ok: bool
    discarded: list[str] = Field(default_factory=list)
    recalled: list[str] = Field(
        default_factory=list,
        description="Files whose CMS draft was taken back to what is live",
    )
    failed: list[dict[str, str]] = Field(default_factory=list)


class BlockerRead(BaseModel):
    code: str
    message: str


class ReleasePlanRead(BaseModel):
    ok: bool
    site_id: int = Field(serialization_alias="siteId")
    files: list[str] = Field(default_factory=list)
    blockers: list[BlockerRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    foreign_drafts: list[str] = Field(default_factory=list, serialization_alias="foreignDrafts")
    verify_failures: list[str] = Field(default_factory=list, serialization_alias="verifyFailures")


class ReleaseRequest(BaseModel):
    """The person's own confirmation, and what they were told would go with it.

    `confirmed` is required rather than defaulted: a client that forgets it gets
    a refusal, which is the right outcome for "publish my site" sent by
    something that never showed anybody the sheet.
    """

    confirmed: bool
    foreign_drafts: list[str] = Field(default_factory=list)


class ReleaseRead(BaseModel):
    ok: bool
    released: int = 0
    files: list[str] = Field(default_factory=list)
    tag: str | None = None
    warnings: list[str] = Field(default_factory=list)
    blockers: list[BlockerRead] = Field(default_factory=list)


class RollbackRequest(BaseModel):
    to: str | None = Field(default=None, description="A release tag; empty means the previous one")


class RollbackRead(BaseModel):
    ok: bool
    restored: list[str] = Field(default_factory=list)
    tag: str | None = None
    released: int = 0


class SuggestionRead(BaseModel):
    action: str
    label: str
    detail: str
    payload: dict[str, object] = Field(default_factory=dict)


class PreviewRead(BaseModel):
    url: str
    expires_at: datetime | None = None


class WorkspaceRead(BaseModel):
    """What the editor needs to draw its tree before anything has changed."""

    files: list[str] = Field(default_factory=list)
    branch: str = "main"
    releases: list[str] = Field(default_factory=list)
