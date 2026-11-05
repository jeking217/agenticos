"""The tools the CMS capability gives the model.

Nine tools, and they are deliberately narrow. There is no `cms_request(path,
body)` here: a general HTTP door would let the model publish anything, delete a
resource, or write a path outside the workspace, and no amount of prompt text
would make that safe. Each tool is one step of the flow the console performs,
so the model can do exactly what a person clicking through the console can do -
and the console's own guards apply to both.

They answer with JSON text because that is what the model reads. A failure is
JSON too: `{"ok": false, "code": "cms_resource_published", "message": ...}`
tells the model what happened and what to do next, where an exception would end
the run and a bare sentence would be guessed at.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import FunctionToolset

from app.cms.errors import CmsError
from app.cms.runtime import CmsRuntime


def _dumps(payload: Any) -> str:
    """One JSON encoding, so every tool answers in the same shape."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _failure(exc: CmsError) -> str:
    """A refusal as the model should see it: a code, and what to do about it."""
    return _dumps({"ok": False, "code": exc.code, "message": exc.message})


class UnioneCmsToolset(FunctionToolset[AgentDepsT]):
    """The CMS flow, exposed one step at a time."""

    def __init__(self, runtime: CmsRuntime) -> None:
        super().__init__()
        self._runtime = runtime
        self.add_function(self.cms_site_list, name="cms_site_list")
        self.add_function(self.cms_pull, name="cms_pull")
        self.add_function(self.cms_diff, name="cms_diff")
        self.add_function(self.cms_discard, name="cms_discard")
        self.add_function(self.cms_verify, name="cms_verify")
        self.add_function(self.cms_push, name="cms_push")
        self.add_function(self.cms_release, name="cms_release")
        self.add_function(self.cms_rollback, name="cms_rollback")
        self.add_function(self.cms_preview_url, name="cms_preview_url")

    async def cms_site_list(self) -> str:
        """List the CMS sites this person may open.

        Each entry carries `can_edit`. A site where it is false can be read but
        not changed - the person's platform role does not cover it - so do not
        offer to edit one; say which role is missing instead.
        """
        try:
            return _dumps(await self._runtime.site_list())
        except CmsError as exc:
            return _failure(exc)

    async def cms_pull(self) -> str:
        """Download this site's web code into the workspace and start a git baseline.

        Run this before reading or changing anything: until it has run there are
        no files, and the workspace's last commit is what `cms_diff` compares
        against. Pulling twice is safe - unchanged files are not rewritten.
        """
        try:
            return _dumps(await self._runtime.pull())
        except CmsError as exc:
            return _failure(exc)

    async def cms_diff(self) -> str:
        """Show which files changed in the workspace, as M / A / D against the CMS.

        This is the answer to "what would publishing do". Read it before
        pushing: a file you did not mean to touch is cheaper to revert now than
        after a release.
        """
        try:
            return _dumps({"ok": True, "changes": await self._runtime.diff()})
        except CmsError as exc:
            return _failure(exc)

    async def cms_discard(self, paths: list[str]) -> str:
        """Undo the edits to these files, putting them back to what is live.

        Narrower than `cms_rollback`: this moves files, not the site, so every
        other change stays. Use it when the person says "not that one" about a
        row of the diff - it is cheaper and safer than reverting a release. A
        file already pushed has its CMS draft taken back too, so a later
        release will not ship the edit that was just undone.
        """
        try:
            return _dumps(await self._runtime.discard(paths))
        except CmsError as exc:
            return _failure(exc)

    async def cms_verify(self) -> str:
        """Check the changed templates with the CMS's own Beetl parser.

        Nothing is saved by this. A failure names the file and the line the
        parser stopped at; fix that file and call again. Files that do not
        parse must not be pushed - a published broken template takes the page
        down for real visitors.
        """
        try:
            return _dumps(await self._runtime.verify())
        except CmsError as exc:
            return _failure(exc)

    async def cms_push(self) -> str:
        """Save the changed files to the CMS as drafts, without publishing them.

        Drafts are visible to the site's editors and, through the debug
        preview, to the person reviewing - not to visitors. Ask before running
        this if the person has not already agreed to write to their site.
        """
        try:
            return _dumps(await self._runtime.push())
        except CmsError as exc:
            return _failure(exc)

    async def cms_release(self) -> str:
        """Publish this site's unpublished drafts, making them live.

        This is the step visitors see, and it cannot be undone by editing -
        only by rolling back. Only call it when the person has explicitly asked
        for the change to go live, and only after `cms_verify` passed.
        """
        try:
            return _dumps(await self._runtime.release())
        except CmsError as exc:
            return _failure(exc)

    async def cms_rollback(self, to: str | None = None) -> str:
        """Restore a previous release of this site.

        `to` names a release tag such as `cms-release-3`; leave it out to go
        back one release. Either way the site is republished from that state,
        so confirm with the person before running it.
        """
        try:
            return _dumps(await self._runtime.rollback(to))
        except CmsError as exc:
            return _failure(exc)

    def cms_preview_url(self, path: str = "/") -> str:
        """A URL that shows this site's drafts, without publishing them.

        Hand it to the person as a link: it renders the CMS's edit-time state,
        so what they see is what a release would make live. `path` is a page
        path such as `/about.html`.
        """
        return _dumps({"ok": True, "url": self._runtime.preview_url(path)})
