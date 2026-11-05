"""The last two steps of the flow: publishing, and taking it back.

`release/all` publishes *every* unpublished resource of a site, not the ones
this session wrote. The console cannot narrow that: the CMS has no per-session
notion of "mine". So the button is not "publish my change", and a well-meaning
one-click would put a colleague's half-finished page live along with yours.

This module is the guard between the button and that call. It produces a plan
first - what would go live, what else would go live with it, what is wrong -
and publishing is refused unless every check passes and somebody confirmed. The
checks are ordered by what a person can do about them: a template that does not
parse is fixed in the editor, a foreign draft is discussed with a colleague,
and a drifted file is re-pulled.

Rollback is the same shape from the other end: the workspace's git history is
the record of what was published, so a release is a tag, and rolling back is
re-publishing the files at that tag.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.cms.errors import CmsError
from app.cms.sync import SyncPlan

#: Why a release is not allowed yet. Codes rather than sentences, because the
#: console maps them to a button ("re-verify", "pull again") and the agent gets
#: the sentence alongside.
VERIFY_FAILED = "verify_failed"
NOTHING_TO_PUBLISH = "nothing_to_publish"
DRIFTED = "drifted"
NOT_CONFIRMED = "not_confirmed"
NO_SUCH_RELEASE = "no_such_release"
DRAFTS_PENDING = "drafts_pending"


@dataclass(frozen=True)
class Blocker:
    """One reason to stop, and what would clear it."""

    code: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass
class ReleasePlan:
    """What publishing now would do - shown to the person before it happens."""

    site_id: int
    files: list[str] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    foreign_drafts: list[str] = field(default_factory=list)
    verify_failures: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not self.blockers

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.allowed,
            "siteId": self.site_id,
            "files": self.files,
            "blockers": [blocker.to_json() for blocker in self.blockers],
            "warnings": self.warnings,
            "foreignDrafts": self.foreign_drafts,
            "verifyFailures": self.verify_failures,
        }


def build_release_plan(
    *,
    site_id: int,
    plan: SyncPlan,
    verified: bool,
    verify_failures: list[str] | None = None,
    drifted: list[str] | None = None,
    foreign_drafts: list[str] | None = None,
    pending: list[str] | None = None,
    confirmed: bool = False,
) -> ReleasePlan:
    """Assemble the release plan, refusing what must not ship.

    `confirmed` is the person's own click, passed in rather than assumed: an
    agent that could publish without it would be an agent that publishes when
    it thinks it is done, which is not the same moment as when the person does.
    """
    failures = list(verify_failures or [])
    drift = list(drifted or [])
    foreign = list(foreign_drafts or [])
    unpushed = list(pending or [])

    release = ReleasePlan(
        site_id=site_id,
        files=[change.path for change in plan.changes],
        foreign_drafts=foreign,
        verify_failures=failures,
    )

    if plan.empty:
        release.blockers.append(
            Blocker(NOTHING_TO_PUBLISH, "工作区没有需要发布的改动")
        )
    if not verified:
        release.blockers.append(
            Blocker(VERIFY_FAILED, "改动尚未通过模版校验，先跑校验")
        )
    elif failures:
        release.blockers.append(
            Blocker(VERIFY_FAILED, "有模版未通过校验：" + "，".join(failures[:5]))
        )
    if unpushed:
        release.blockers.append(
            Blocker(
                DRAFTS_PENDING,
                "有改动还没保存成草稿，先保存再发布：" + "，".join(unpushed[:5]),
            )
        )
    if drift:
        release.blockers.append(
            Blocker(
                DRIFTED,
                "有文件在拉取后被别人改过，先重新拉取：" + "，".join(drift[:5]),
            )
        )
    if not confirmed:
        release.blockers.append(
            Blocker(NOT_CONFIRMED, "发布前需要人工确认，确认单里列出的是会连带发布的草稿")
        )
    if foreign:
        release.warnings.append(
            f"本次发布会连带发布 {len(foreign)} 个其他人保存的草稿：" + "，".join(foreign[:5])
        )
    return release


@dataclass(frozen=True)
class Release:
    """A published state of a site, as the workspace recorded it."""

    tag: str
    version: int | None = None
    files: int = 0


def plan_rollback(
    releases: list[Release], *, requested: str | None = None
) -> Release:
    """Which release to go back to, refusing a tag that does not exist.

    `requested=None` means "the one before the current", which is what a person
    means by "undo that". It is resolved here rather than by the caller so an
    empty history produces one clear refusal instead of an index error.
    """
    if not releases:
        raise CmsError(NO_SUCH_RELEASE, "这个站点还没有发布记录，没有可以回滚的版本")
    if requested is None:
        if len(releases) < 2:
            raise CmsError(NO_SUCH_RELEASE, "只有一次发布记录，没有上一个版本可回滚")
        return releases[-2]
    for release in releases:
        if release.tag == requested:
            return release
    raise CmsError(NO_SUCH_RELEASE, f"找不到发布记录 {requested}")
