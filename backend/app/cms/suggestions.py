"""The reminder a person gets when the agent thinks the change is ready.

An agent that has just edited a site knows something the person does not: that
the templates parse, that the diff is three files, that publishing is one click
away. The console already renders suggestion chips after an answer, so the
reminder is a payload of the shape it already understands - `action`,
`label`, `detail` - and no new event type. A second mechanism for "a button
under the answer" would be a second thing to keep in step with the first.

The action names are namespaced (`cms.`) because these chips reach a generic
renderer that must not mistake them for another feature's.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.cms.sync import SyncPlan

#: What the console does when a chip is clicked. Stable, because the front end
#: switches on them and a rename is a silently dead button.
ACTION_PUBLISH = "cms.publish"
ACTION_DIFF = "cms.diff"
ACTION_PREVIEW = "cms.preview"
ACTION_FIX = "cms.fix"
ACTION_PULL = "cms.pull"


@dataclass(frozen=True)
class Suggestion:
    """One chip: what it says, and what clicking it does."""

    action: str
    label: str
    detail: str
    payload: dict[str, object]

    def to_json(self) -> dict[str, object]:
        return {
            "action": self.action,
            "label": self.label,
            "detail": self.detail,
            "payload": self.payload,
        }


def after_verify(
    *,
    site_id: int,
    plan: SyncPlan,
    verified: bool,
    failures: list[str] | None = None,
    preview_url: str | None = None,
) -> list[Suggestion]:
    """What to offer after a verification run, in the order a person needs it.

    A failure offers the fix first and publishing not at all: a chip that can
    only be refused is worse than no chip. A pass offers the diff before the
    publish, because the diff is the thing that makes the publish a decision
    rather than a leap.
    """
    failures = failures or []
    site_payload = {"siteId": site_id}

    if not verified or failures:
        return [
            Suggestion(
                action=ACTION_FIX,
                label="看看哪里报错",
                detail=("，".join(failures[:3]) if failures else "校验没有通过"),
                payload={**site_payload, "files": failures},
            )
        ]

    if plan.empty:
        return []

    chips = [
        Suggestion(
            action=ACTION_DIFF,
            label=f"查看变更（{len(plan.changes)} 个文件）",
            detail="发布前先确认改了什么",
            payload={**site_payload, "files": [change.path for change in plan.changes]},
        ),
        Suggestion(
            action=ACTION_PUBLISH,
            label="确认并发布",
            detail="发布会连带发布其他人保存的草稿，确认单里会列出来",
            payload={**site_payload, "files": [change.path for change in plan.changes]},
        ),
    ]
    if preview_url:
        chips.insert(
            0,
            Suggestion(
                action=ACTION_PREVIEW,
                label="先在预览里看效果",
                detail="预览是实时编辑态，不用发布",
                payload={**site_payload, "url": preview_url},
            ),
        )
    return chips


def after_pull(*, site_id: int, files: int, preview_url: str | None = None) -> list[Suggestion]:
    """What to offer once the site's code is in the workspace."""
    chips = [
        Suggestion(
            action=ACTION_DIFF,
            label="看看站点里有什么",
            detail=f"{files} 个文件已就绪",
            payload={"siteId": site_id},
        )
    ]
    if preview_url:
        chips.append(
            Suggestion(
                action=ACTION_PREVIEW,
                label="打开实时预览",
                detail="编辑态，发布前也能看",
                payload={"siteId": site_id, "url": preview_url},
            )
        )
    return chips


def after_release(*, site_id: int, files: int, tag: str | None) -> list[Suggestion]:
    """What to offer after a release - the undo, and where to look."""
    chips = [
        Suggestion(
            action=ACTION_PREVIEW,
            label="看看线上效果",
            detail=f"{files} 个文件已发布",
            payload={"siteId": site_id, "released": files},
        )
    ]
    if tag:
        chips.append(
            Suggestion(
                action=ACTION_PULL,
                label="回滚到上一个版本",
                detail=f"发布记录 {tag}",
                payload={"siteId": site_id, "rollbackTo": tag},
            )
        )
    return chips
