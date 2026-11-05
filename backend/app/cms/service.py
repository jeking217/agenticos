"""One site's flow, from the archive to a release.

This is where the pieces meet: the client talks to the CMS, the workspace holds
the files, `sync` decides what changed, `publish` decides whether it may go
out. The service owns the *order*, and the order is the design:

1. `pull` - fetch, unpack, commit. The baseline.
2. `diff` - what the workspace changed, as the file tree shows it.
3. `verify` - Beetl, on the changed templates only, saving nothing.
4. `push` - save the changed files as drafts, upgrading a published one first.
5. `release` - the guarded publish, then a tag naming what went live.
6. `rollback` - restore a tag and republish it.

Splitting them is what lets the console show a diff before anything is
written, and what lets the agent stop between "saved" and "live" - which is the
only place an approval gate is worth having.

Everything is injected: the client, the workspace, and the site. That is what
makes the whole flow testable against a fake CMS and a real temporary
repository, with no database and no network in the way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from app.cms import archive
from app.cms.client import CmsClient, CmsResourceRecord, CmsSiteRecord
from app.cms.errors import NOT_FOUND, PATH_REJECTED, PUBLISHED, VERIFY_FAILED, CmsError
from app.cms.preview import PreviewSigner, debug_cookie_name, debug_cookie_value
from app.cms.publish import Release, ReleasePlan, build_release_plan, plan_rollback
from app.cms.sync import FileChange, SyncPlan, plan_sync
from app.cms.workspace import SiteWorkspace

#: Where the console's own forwarder exposes the preview proxy - the path the
#: *browser* loads. The route itself is mounted at `/api/v1/cms/preview`, and the
#: console's `/api/*` forwarder maps one to the other; both the signed link and
#: the asset URLs the HTML is rewritten with have to name the browser-visible
#: one, or the frame renders without its stylesheet.
PREVIEW_PATH = "/api/cms/preview"


@dataclass
class PullResult:
    files: int
    skipped: list[dict[str, str]] = field(default_factory=list)
    baseline: str | None = None
    created: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "ok": True,
            "files": self.files,
            "skipped": self.skipped,
            "baseline": self.baseline,
            "created": self.created,
        }


@dataclass
class VerifyReport:
    ok: bool
    failures: list[str] = field(default_factory=list)
    detail: str | None = None
    checked: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "failures": self.failures,
            "detail": self.detail,
            "checked": self.checked,
        }


@dataclass
class PushReport:
    saved: list[str] = field(default_factory=list)
    upgraded: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "saved": self.saved,
            "upgraded": self.upgraded,
            "deleted": self.deleted,
            "failed": self.failed,
        }


@dataclass
class DiscardReport:
    """What a discard moved, and what it had to take back from the CMS."""

    discarded: list[str] = field(default_factory=list)
    recalled: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_json(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "discarded": self.discarded,
            "recalled": self.recalled,
            "failed": self.failed,
        }


class CmsSiteService:
    """The six steps, for one site and one person."""

    def __init__(
        self,
        *,
        client: CmsClient,
        workspace: SiteWorkspace,
        site: CmsSiteRecord,
        user_id: int,
        allowed_ext: set[str] | frozenset[str],
        max_file_bytes: int,
        signer: PreviewSigner | None = None,
        preview_ttl_seconds: int = 600,
        preview_origin: str = "",
    ) -> None:
        self._client = client
        self._workspace = workspace
        self._site = site
        self._user_id = user_id
        self._allowed_ext = frozenset(allowed_ext)
        self._max_file_bytes = max_file_bytes
        self._signer = signer
        self._preview_ttl = preview_ttl_seconds
        self._preview_origin = preview_origin.rstrip("/")
        self._resources: list[CmsResourceRecord] | None = None
        self._verified: VerifyReport | None = None
        #: What the last push wrote. A release publishes drafts, not files, so
        #: this - not the workspace diff - is what "would go live" means once
        #: the drafts are saved.
        self._pushed: list[FileChange] = []

    @property
    def site(self) -> CmsSiteRecord:
        """The site this flow was bound to. Public because the capability's
        adapter reports it to the model, and reaching into `_site` would make
        that dependency invisible."""
        return self._site

    # -- 1. pull ----------------------------------------------------------

    async def pull(self) -> PullResult:
        """Fetch the site's code and lay down the baseline.

        The tag is the site's current `spaceVers`, so a workspace pulled at
        version 3 says so - that is what rollback names, and it is what the CMS
        will call the release this baseline corresponds to.
        """
        payload = await self._client.export_site(self._site.id)
        created = not self._workspace.is_repository()
        await self._workspace.ensure_repository()
        report = archive.extract_site_zip(
            payload,
            self._workspace.root,
            allowed_ext=self._allowed_ext,
            max_file_bytes=self._max_file_bytes,
        )
        await self._workspace.commit(f"pull site {self._site.id}")
        baseline = f"cms-release-{self._site.space_vers}" if self._site.space_vers else None
        if baseline and not await self._workspace.releases():
            await self._workspace.tag(baseline)
        self._resources = None
        return PullResult(
            files=len(report.files),
            skipped=[{"path": s.path, "reason": s.reason} for s in report.skipped],
            baseline=baseline,
            created=created,
        )

    # -- 2. diff ----------------------------------------------------------

    async def diff(self) -> SyncPlan:
        changes = await self._workspace.status()
        plan = plan_sync(
            [(change.path, change.status) for change in changes],
            await self._remote_resources(),
            sizes=self._local_sizes(changes),
        )
        return plan

    async def _remote_resources(self) -> list[CmsResourceRecord]:
        if self._resources is None:
            self._resources = await self._client.find_resources(self._site.id)
        return self._resources

    def _local_sizes(self, changes: list) -> dict[str, int]:
        sizes: dict[str, int] = {}
        for change in changes:
            path = Path(self._workspace.root) / change.path.lstrip("/")
            if path.is_file():
                sizes[change.path] = path.stat().st_size
        return sizes

    # -- 3. verify --------------------------------------------------------

    async def verify(self) -> VerifyReport:
        """Check the changed templates, saving nothing.

        A file the CMS has never seen is verified through the save-less path in
        `CmsClient.verify` - which is exactly the case the CMS's `verify` had to
        be extended for, since it used to require a resource id.
        """
        plan = await self.diff()
        checked = 0
        failures: list[str] = []
        detail: str | None = None
        for change in plan.changes:
            if change.status == "D":
                continue
            text = self._read_text(change.path)
            if text is None:
                continue
            checked += 1
            try:
                await self._client.verify(
                    site_id=self._site.id,
                    name=change.path,
                    body_text=text,
                    resource_id=change.resource_id,
                )
            except CmsError as exc:
                if exc.code != VERIFY_FAILED:
                    raise
                failures.append(change.path)
                detail = exc.message
        self._verified = VerifyReport(
            ok=not failures, failures=failures, detail=detail, checked=checked
        )
        return self._verified

    # -- 4. push ----------------------------------------------------------

    async def push(self) -> PushReport:
        """Save every change as a draft, upgrading published resources first.

        A published resource cannot be written to - the CMS refuses with
        "资源已发布，请先升级资源！". Upgrading is not optional and not a retry:
        it is the CMS's own two-step model for editing something live, and the
        service performs it because the person in the editor meant "change this
        file", not "understand my CMS's versioning".
        """
        plan = await self.diff()
        report = PushReport()
        for change in plan.changes:
            if change.status == "D":
                if change.resource_id is not None:
                    await self._client.delete_resources([change.resource_id])
                    report.deleted.append(change.path)
                continue
            text = self._read_text(change.path)
            if text is None:
                report.failed.append({"path": change.path, "reason": "not a readable text file"})
                continue
            try:
                await self._save(change, text)
                report.saved.append(change.path)
            except CmsError as exc:
                report.failed.append({"path": change.path, "reason": exc.message})
        # Committed here rather than at release time: from this moment the CMS
        # holds these files as drafts, so a later local deletion is a real
        # deletion and not the disappearance of an untracked file.
        await self._workspace.commit(f"draft site {self._site.id}")
        self._pushed = list(plan.changes)
        self._resources = None
        return report

    async def _save(self, change: FileChange, text: str) -> None:
        try:
            await self._client.save_resource(
                site_id=self._site.id,
                space_id=change.space_id or self._site.space_id,
                name=change.path,
                types="text",
                body_text=text,
                resource_id=change.resource_id,
            )
        except CmsError as exc:
            if exc.code != PUBLISHED or change.resource_id is None:
                raise
            await self._client.upgrade(change.resource_id)
            await self._client.save_resource(
                site_id=self._site.id,
                space_id=change.space_id or self._site.space_id,
                name=change.path,
                types="text",
                body_text=text,
                resource_id=change.resource_id,
            )

    # -- 4b. discard ------------------------------------------------------

    async def discard(self, paths: Sequence[str]) -> DiscardReport:
        """Throw away this session's edits to specific files.

        The diff card's per-row undo, and deliberately narrower than
        `rollback`: it moves files, not the site.

        A file that was already pushed needs a second half. The draft it left in
        the CMS is not private to this session - `release/all` publishes every
        unpublished resource of the site - so leaving it behind would mean the
        next publish ships the very content the person just undid. So the
        restored text is saved back as the draft, which makes the CMS hold what
        is live and the next release harmless. A file that was pushed and is now
        discarded entirely has nothing left to hold, so its resource goes.

        Undoing goes back to the newest release tag, not to `HEAD`: a push
        commits, so `HEAD` is the draft, and "undo this edit" means "back to the
        published page".
        """
        releases = await self._workspace.releases()
        baseline = releases[-1] if releases else "HEAD"
        discarded = await self._workspace.revert(list(paths), since=baseline)
        if not discarded:
            return DiscardReport()

        undone = set(discarded)
        pushed = {change.path: change for change in self._pushed if change.path in undone}
        report = DiscardReport()
        for path in discarded:
            change = pushed.get(path)
            if change is None:
                report.discarded.append(path)
                continue
            try:
                await self._recall(change)
                report.discarded.append(path)
                report.recalled.append(path)
            except CmsError as exc:
                report.failed.append({"path": path, "reason": exc.message})

        self._pushed = [change for change in self._pushed if change.path not in undone]
        # Only the discarded files: the rest of the tree may hold edits this
        # person has not saved yet, and those must keep showing as changes.
        await self._workspace.commit_paths(
            report.discarded, f"discard {len(report.discarded)} file(s) on site {self._site.id}"
        )
        self._resources = None
        return report

    async def _recall(self, change: FileChange) -> None:
        """Make the CMS's draft match what is live again, or drop it."""
        text = self._read_text(change.path)
        if text is not None:
            await self._save(change, text)
        elif change.resource_id is not None:
            await self._client.delete_resources([change.resource_id])

    # -- 5. release -------------------------------------------------------

    async def plan_release(self, *, confirmed: bool, foreign_drafts: list[str] | None = None) -> ReleasePlan:
        """The confirmation sheet, assembled from what the service already knows.

        The files are the drafts the last push wrote, and the workspace is
        consulted only for what is *not* among them: an edit made after the push
        is a blocker, because publishing would ship the draft and leave the
        person's newest change behind while telling them it went out.
        """
        drafts = SyncPlan(changes=list(self._pushed))
        pending = [change.path for change in (await self.diff()).changes]
        verified = self._verified is not None and self._verified.ok
        return build_release_plan(
            site_id=self._site.id,
            plan=drafts,
            verified=verified,
            verify_failures=(self._verified.failures if self._verified else []),
            pending=pending,
            foreign_drafts=foreign_drafts,
            confirmed=confirmed,
        )

    async def release(self, *, confirmed: bool, foreign_drafts: list[str] | None = None) -> dict[str, object]:
        """Publish, then record what went live as a tag.

        The tag is written after the CMS accepted the release, and it names the
        CMS's own version counter: a rollback has to point at something the CMS
        agrees with, not at this workspace's opinion.
        """
        plan = await self.plan_release(confirmed=confirmed, foreign_drafts=foreign_drafts)
        if not plan.allowed:
            return {"ok": False, "blockers": [b.to_json() for b in plan.blockers], **plan.to_json()}

        released = await self._client.release_all(self._site.id)
        try:
            await self._client.build_html([self._site.id])
        except CmsError:
            # Static regeneration is a cache warm-up, not part of the release.
            # A site without static generation answers this with an error, and
            # reporting that as "your release failed" would be a lie.
            pass
        await self._workspace.commit(f"release site {self._site.id}")
        tag = await self._next_tag()
        await self._workspace.tag(tag)
        self._verified = None
        self._pushed = []
        return {
            "ok": True,
            "released": released,
            "files": plan.files,
            "tag": tag,
            "warnings": plan.warnings,
        }

    async def _next_tag(self) -> str:
        """The next release number, after the highest one that exists.

        Counted by the numeric suffix rather than by how many tags there are:
        a baseline tag carries the CMS's own `spaceVers` (3), so a workspace
        pulled at version 3 has one tag numbered 3 and would otherwise name its
        first release "2" - a number already in the past.
        """
        numbers = [
            int(tag.rsplit("-", 1)[-1])
            for tag in await self._workspace.releases()
            if tag.rsplit("-", 1)[-1].isdigit()
        ]
        return f"cms-release-{max(numbers, default=0) + 1}"

    # -- 6. rollback ------------------------------------------------------

    async def rollback(self, to: str | None = None) -> dict[str, object]:
        """Restore a release and publish it again.

        Restoring the files is only half of it: a rollback that leaves the CMS
        serving the new version is a workspace that disagrees with production.
        So the files are restored, verified (they parsed when they shipped) and
        pushed before the release.
        """
        history = await self._workspace.releases()
        target = plan_rollback([Release(tag=tag) for tag in history], requested=to)
        restored = await self._workspace.restore(target.tag)
        self._resources = None
        report = await self.push()
        if not report.ok:
            return {"ok": False, "restored": restored, "push": report.to_json()}
        released = await self._client.release_all(self._site.id)
        return {
            "ok": True,
            "restored": restored,
            "tag": target.tag,
            "released": released,
        }

    # -- preview ----------------------------------------------------------

    def preview_url(self, path: str = "/", *, prefix: str | None = None) -> str:
        """A signed link to the CMS's edit-time rendering of this site.

        `prefix` is where the console is mounted, as the browser sees it
        (`/agent/web`), and comes from the BFF's `X-Forwarded-Prefix`. The
        configured `preview_origin` is the fallback for callers that have no
        request - an agent tool, a CLI - where the deployment root is the best
        guess available. Passing the prefix rather than configuring it keeps one
        console's links right on any number of hosts.
        """
        if self._signer is None:
            raise CmsError(VERIFY_FAILED, "preview is not configured on this deployment")
        ticket = self._signer.mint(
            user_id=self._user_id, site_id=self._site.id, ttl_seconds=self._preview_ttl
        )
        origin = self._preview_origin if prefix is None else prefix.rstrip("/")
        return f"{origin}{PREVIEW_PATH}/{ticket}{path}"

    def debug_cookie(self) -> tuple[str, str]:
        """The cookie the proxy attaches - never the browser."""
        return debug_cookie_name(self._site.id), debug_cookie_value(self._user_id, 0)

    # -- helpers ----------------------------------------------------------

    def read_file(self, path: str) -> str:
        """One workspace file's text, for the editor to show.

        The path arrives from the browser, so it goes through the same
        containment check a pull does - `../../.env` is a file the console must
        not read back to anybody, however it was spelled.
        """
        target = archive.resolve_inside(self._workspace.root, path)
        if not target.is_file():
            raise CmsError(NOT_FOUND, f"{path} 不在工作区里")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise CmsError(PATH_REJECTED, f"{path} 不是文本文件，编辑器打不开") from exc

    def _read_text(self, path: str) -> str | None:
        candidate = Path(self._workspace.root) / path.lstrip("/")
        try:
            return candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
