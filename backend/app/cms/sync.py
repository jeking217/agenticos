"""Working out what changed, and what pushing it would mean.

The workspace is a git repository (that is what D4 bought), so *what changed* is
`git status` - not a content comparison against a CMS list endpoint that does
not return bodies anyway. This module's job is the step after that: turning
paths into resources. A modified path needs the resource id to update; an added
path needs none and will be created; a deleted path needs the id to remove.

Two refusals live here rather than in the publisher, because they are facts
about the plan and the publisher should not have to rediscover them:

* a path the CMS holds as a binary or a directory cannot be overwritten with
  text - the CMS would accept the write and render a broken page;
* a deleted directory is not a deleted file. Deleting a resource row with
  children orphans them, so the plan refuses and says which directory.

Skipped paths are reported too. "36 files, 2 changed" and "36 files, 34
silently ignored" look identical in a success message, and only one of them is
true.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.cms.client import CmsResourceRecord

#: git's porcelain letters, plus the two it uses for files that are both staged
#: and modified. Only the first letter matters for "what does publishing do".
ADDED = "A"
MODIFIED = "M"
DELETED = "D"


@dataclass(frozen=True)
class FileChange:
    """One path the workspace changed, and what the CMS has at that path."""

    path: str
    status: str
    size: int = 0
    resource_id: int | None = None
    space_id: int | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "status": self.status,
            "size": self.size,
            "resourceId": self.resource_id,
        }


@dataclass
class SyncPlan:
    """What a push would do, and what it would leave alone."""

    changes: list[FileChange] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def added(self) -> list[FileChange]:
        return [change for change in self.changes if change.status == ADDED]

    @property
    def modified(self) -> list[FileChange]:
        return [change for change in self.changes if change.status == MODIFIED]

    @property
    def deleted(self) -> list[FileChange]:
        return [change for change in self.changes if change.status == DELETED]

    @property
    def empty(self) -> bool:
        return not self.changes

    def to_json(self) -> dict[str, object]:
        return {
            "changes": [change.to_json() for change in self.changes],
            "skipped": self.skipped,
            "counts": {
                "added": len(self.added),
                "modified": len(self.modified),
                "deleted": len(self.deleted),
            },
        }


def resource_index(remote: list[CmsResourceRecord]) -> dict[str, CmsResourceRecord]:
    """The CMS's resources by path, with the tree's own prefixes trimmed.

    Resource names arrive as the CMS stores them, and the export writes them
    without a leading slash; both shapes name the same file. Normalising here
    means every comparison downstream is between like and like.
    """
    index: dict[str, CmsResourceRecord] = {}
    for record in remote:
        name = record.name if record.name.startswith("/") else f"/{record.name}"
        index[name] = record
    return index


def plan_sync(
    changed: list[tuple[str, str]],
    remote: list[CmsResourceRecord],
    *,
    sizes: dict[str, int] | None = None,
) -> SyncPlan:
    """Turn git's report into the plan a push follows.

    `changed` is `(path, status)` as the workspace produced it. Anything the
    plan cannot act on lands in `skipped` with a sentence - never silently.
    """
    index = resource_index(remote)
    sizes = sizes or {}
    plan = SyncPlan()

    for raw_path, raw_status in changed:
        path = raw_path if raw_path.startswith("/") else f"/{raw_path}"
        status = raw_status[:1].upper()
        if status not in (ADDED, MODIFIED, DELETED):
            plan.skipped.append({"path": path, "reason": f"{raw_status} is not a change a push can make"})
            continue
        existing = index.get(path)
        if status == DELETED:
            if existing is None:
                plan.skipped.append({"path": path, "reason": "the CMS does not have this file"})
                continue
            if existing.is_dir:
                plan.skipped.append(
                    {"path": path, "reason": "a directory is not deleted from here; remove its files first"}
                )
                continue
            plan.changes.append(
                FileChange(path=path, status=DELETED, resource_id=existing.id, space_id=existing.space_id)
            )
            continue
        if existing is not None and not existing.is_text:
            plan.skipped.append(
                {"path": path, "reason": f"the CMS holds this as {existing.types}, not text"}
            )
            continue
        plan.changes.append(
            FileChange(
                path=path,
                status=MODIFIED if existing is not None else ADDED,
                size=sizes.get(path, 0),
                resource_id=existing.id if existing is not None else None,
                space_id=existing.space_id if existing is not None else None,
            )
        )
    return plan
