"""The capability's runtime, over the console's flow.

`CmsRuntime` is the protocol the agent's tools speak; `CmsSiteService` is the
service the console uses. They are the same six steps, so this adapter is one
line each - and it exists at all so the agent can never do something the
console cannot. A second implementation for the agent would be a second set of
rules, and the one nobody reviews.

The runtime is built per run and handed to the capability through
`CapabilityBuildContext.resources["cms_runtime"]`. It holds the person's token
(indirectly, through the client) and their workspace, which is exactly why it is
built here and not stored in an agent's spec.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.cms.service import CmsSiteService
from app.cms.sync import SyncPlan


@dataclass
class SiteRuntime:
    """One site's flow, as the agent's tools see it."""

    flow: CmsSiteService

    async def site_list(self) -> list[dict[str, object]]:
        """This runtime is bound to one site, so the list is that site.

        The console's picker uses `CmsSitesService.list_sites`; a bound run has
        already made that choice, and listing every site would invite the model
        to ask for a different one - which it cannot have.
        """
        site = self.flow.site
        return [
            {
                "id": site.id,
                "title": site.title,
                "ctx": site.ctx,
                "domain": site.domain,
                "can_edit": True,
            }
        ]

    async def pull(self) -> dict[str, object]:
        result = await self.flow.pull()
        return result.to_json()

    async def diff(self) -> list[dict[str, object]]:
        plan = await self.flow.diff()
        return [change.to_json() for change in plan.changes]

    async def verify(self) -> dict[str, object]:
        report = await self.flow.verify()
        return report.to_json()

    async def push(self) -> dict[str, object]:
        report = await self.flow.push()
        return report.to_json()

    async def release(self) -> dict[str, object]:
        """Publish, having first established that the person asked for it.

        An agent reaches this tool only after an approval gate has fired on
        `cms_release` (see the capability's `side_effecting` declaration). The
        gate *is* the confirmation, so the service is called with `confirmed`
        true - and the plan's other blockers (unverified, un-pushed, drifted)
        still refuse.
        """
        return await self.flow.release(confirmed=True)

    async def rollback(self, to: str | None = None) -> dict[str, object]:
        return await self.flow.rollback(to)

    async def discard(self, paths: list[str]) -> dict[str, object]:
        report = await self.flow.discard(paths)
        return report.to_json()

    def preview_url(self, path: str = "/") -> str:
        return self.flow.preview_url(path)


def empty_plan() -> SyncPlan:
    """An empty plan, for callers that need the type without a workspace."""
    return SyncPlan()
