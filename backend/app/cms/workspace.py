"""The workspace's git layer: the record of what was published.

D4 chose a git mirror inside the project workspace over standing up a git
server. That is what makes "what changed" a `git status`, "undo" a `git
revert`, and "try an idea" a branch - without a second service to run, and
without a network round trip for a question this deployment can answer alone.

Two conventions matter, and both are load-bearing:

* **A release is a tag.** `cms-release-{vers}` is created when a release
  succeeds, so rollback has something stable to name and the workspace has a
  record of what actually went live (which is not always what was committed:
  a push can be saved as a draft and never released).
* **The CMS's published state is the remote.** The first pull is the baseline
  commit; every later commit is a local change waiting to be pushed. So
  `status` answers "what would a push send", which is the only question the
  console's file tree needs.

All git access goes through one helper, with timeouts, because a hung git
process inside a request is a request that never returns.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.cms.archive import resolve_inside
from app.cms.errors import UNEXPECTED, CmsError

#: How long a git call may take. Generous for a real repository, short enough
#: that a stuck call surfaces as an error rather than a spinning console.
_GIT_TIMEOUT_SECONDS = 60.0

#: Committer identity for the workspace's own commits. Fixed rather than
#: inherited: a container has no `user.name`, and `git commit` fails without
#: one - which would make every pull look like a broken workspace.
_COMMITTER_NAME = "AgenticOS CMS"
_COMMITTER_EMAIL = "cms@agenticos.local"


@dataclass(frozen=True)
class WorkspaceChange:
    """One entry of `git status`, as porcelain reports it."""

    path: str
    status: str


class SiteWorkspace:
    """A site's code on disk, versioned locally.

    `root` is the site's directory inside the project workspace. It is created
    on first use and never deleted by this class: a workspace that disappears
    when a pull fails is a workspace that loses the person's uncommitted work.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # -- plumbing ---------------------------------------------------------

    async def _git(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            f"user.name={_COMMITTER_NAME}",
            "-c",
            f"user.email={_COMMITTER_EMAIL}",
            *args,
            cwd=str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=_GIT_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:  # pragma: no cover - a hung git is an environment fault
            process.kill()
            raise CmsError(UNEXPECTED, f"git {args[0]} timed out") from exc
        if process.returncode != 0:
            message = stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed"
            raise CmsError(UNEXPECTED, message)
        return stdout.decode("utf-8", "replace")

    # -- state ------------------------------------------------------------

    def is_repository(self) -> bool:
        return (self.root / ".git").exists()

    async def ensure_repository(self) -> None:
        """Make this a repository, on the first pull only.

        `git init -b main` rather than a default branch name the host happens to
        choose: rollback and release tags are read by a person, and "the main
        branch" is what they will look for.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        if self.is_repository():
            return
        await self._git("init", "-b", "main")

    async def status(self) -> list[WorkspaceChange]:
        """What differs from the last commit, as the file tree's M/A/D marks.

        `--untracked-files=all` is deliberate: a new file inside an existing
        directory is untracked, and the default `normal` mode would report the
        directory once instead of the file the console has to open.
        """
        if not self.is_repository():
            return []
        raw = await self._git("status", "--porcelain=v1", "--untracked-files=all")
        changes: list[WorkspaceChange] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            marker = line[:2]
            path = line[3:].strip()
            if " -> " in path:  # a rename reports both ends
                path = path.split(" -> ", 1)[1]
            status = _status_letter(marker)
            if status is None:
                continue
            changes.append(WorkspaceChange(path=f"/{path}", status=status))
        return changes

    async def has_commits(self) -> bool:
        if not self.is_repository():
            return False
        try:
            await self._git("rev-parse", "--verify", "HEAD")
        except CmsError:
            return False
        return True

    async def commit(self, message: str) -> bool:
        """Commit everything, or nothing when there is nothing to commit.

        Returns whether a commit happened: a pull that finds the site unchanged
        should not add an empty commit to the history for every visit.
        """
        await self._git("add", "-A")
        if not await self.has_commits():
            await self._git("commit", "--allow-empty", "-m", message)
            return True
        staged = await self._git("diff", "--cached", "--name-only")
        if not staged.strip():
            return False
        await self._git("commit", "-m", message)
        return True

    async def tag(self, name: str) -> None:
        """Mark the current commit as a release. Re-tagging the same name is a no-op."""
        existing = await self.releases()
        if name in existing:
            return
        await self._git("tag", name)

    async def releases(self) -> list[str]:
        """Release tags, oldest first - the order rollback walks backwards in."""
        if not self.is_repository():
            return []
        raw = await self._git("tag", "--list", "cms-release-*", "--sort=creatordate")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    async def restore(self, tag: str) -> list[str]:
        """Put the workspace back to `tag`'s content, as a new commit.

        Not a `reset`: the history is the audit trail, and rewriting it would
        erase the release that is being undone. `checkout <tag> -- .` restores
        the files, the commit records the act, and `git revert` semantics are
        kept for anyone reading it later.
        """
        before = await self.status()
        await self._git("checkout", tag, "--", ".")
        await self._git("add", "-A")
        changed = await self.status()
        await self.commit(f"rollback to {tag}")
        return [change.path for change in before] or [change.path for change in changed]

    async def revert(self, paths: Sequence[str], *, since: str = "HEAD") -> list[str]:
        """Put these files back to their content at `since`, and report which moved.

        The diff card's per-row "discard", and deliberately narrower than
        `restore`: a person who has changed four files and regrets one should
        lose one.

        `since` is a caller's decision, not a default worth hiding: a push
        commits the drafts it saved, so `HEAD` is "what was last pushed", while
        "what is live" is the newest release tag. The console means the latter -
        undoing an edit undoes it back to the published page, not back to an
        unreleased draft.

        Only a file whose bytes actually change is reported, so asking to
        discard an untouched file is a no-op rather than a checkout that
        rewrites an mtime and tells the person something happened.
        """
        if not self.is_repository() or not await self.has_commits():
            return []
        reverted: list[str] = []
        for raw in paths:
            if not raw or not raw.strip():
                continue
            # Refuses traversal, a drive path and a backslash before anything
            # is read or unlinked - the same guard the pull uses.
            target = resolve_inside(self.root, raw)
            relative = target.relative_to(self.root.resolve()).as_posix()
            before = target.read_bytes() if target.is_file() else None
            if await self._known_at(relative, since):
                await self._git("checkout", since, "--", relative)
            elif target.exists():
                # A file added locally has no committed version to go back to,
                # so discarding it means removing it - which is what "undo my
                # new page" means to the person who asked.
                target.unlink()
            after = target.read_bytes() if target.is_file() else None
            if before != after:
                reverted.append(f"/{relative}")
        return reverted

    async def commit_paths(self, paths: Sequence[str], message: str) -> bool:
        """Commit just these files, leaving every other local edit alone.

        A partial commit, because that is what a per-file discard needs:
        `commit` would fold whatever else the person has open into the record of
        an undo, and their other edits would disappear from `status` - they were
        never pushed, but the tree would stop showing them.

        Returns whether a commit happened; a discarded file that only ever
        existed locally has nothing to record.
        """
        if not await self.has_commits():
            return False
        relatives: list[str] = []
        for raw in paths:
            if not raw or not raw.strip():
                continue
            target = resolve_inside(self.root, raw)
            relative = target.relative_to(self.root.resolve()).as_posix()
            if await self._known_at(relative, "HEAD"):
                relatives.append(relative)
        if not relatives:
            return False
        await self._git("add", "-A", "--", *relatives)
        staged = await self._git("diff", "--cached", "--name-only", "--", *relatives)
        if not staged.strip():
            return False
        await self._git("commit", "-m", message, "--", *relatives)
        return True

    async def _known_at(self, relative: str, revision: str) -> bool:
        """Whether `revision` holds this path, i.e. whether there is a version to restore."""
        try:
            await self._git("cat-file", "-e", f"{revision}:{relative}")
        except CmsError:
            return False
        return True

    async def branch(self, name: str) -> None:
        """Start a branch, so an experiment does not become the site's state."""
        await self._git("checkout", "-b", name)

    async def current_branch(self) -> str:
        if not self.is_repository() or not await self.has_commits():
            return "main"
        return (await self._git("rev-parse", "--abbrev-ref", "HEAD")).strip()


def _status_letter(marker: str) -> str | None:
    """Porcelain's two columns, as one letter the file tree can colour.

    `M ` (staged) and ` M` (unstaged) are both "modified"; `??` is "added";
    `D` in either column is "deleted". Anything else - conflicts, submodules -
    is left out rather than mislabelled, because a wrong letter is worse than
    an absent one.
    """
    if "D" in marker:
        return "D"
    if "??" in marker or "A" in marker:
        return "A"
    if "M" in marker or "R" in marker:
        return "M"
    return None
