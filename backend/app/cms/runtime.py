"""The seam between the capability and the CMS work.

A capability runs inside an agent, with a model, a database session and a
budget. None of that belongs in a test of "does diff report the files that
changed", and none of it belongs in the prompt either. So the eight tools the
model sees are one-line adapters over this protocol, and everything that
decides *what* happens - which files, which order, which refusal - lives in
`app.cms`, where it can be read and tested on its own.

The shapes below are the contract both sides agree on. They are plain
dictionaries rather than models because they are what the model reads: JSON is
the whole answer, and a second schema between the service and the tool would
only be a third place to rename a field.
"""

from typing import Any, Protocol, runtime_checkable

#: What each operation answers with, as the model sees it.
OperationResult = dict[str, Any]


@runtime_checkable
class CmsRuntime(Protocol):
    """One site, bound to one person, with a workspace to edit it in.

    Every method is already authorised: the runtime was built for a signed-in
    person and a site they are allowed to change, so a tool never has to ask
    "may I" and never has to carry a credential the model could read.
    """

    async def site_list(self) -> list[OperationResult]:
        """The sites this person may open, with `can_edit` on each."""

    async def pull(self) -> OperationResult:
        """Download the site's code and lay down a git baseline."""

    async def diff(self) -> list[OperationResult]:
        """Files that differ from the CMS's published state."""

    async def verify(self) -> OperationResult:
        """Beetl-check every changed template, without saving anything."""

    async def push(self) -> OperationResult:
        """Save changed files as CMS drafts."""

    async def release(self) -> OperationResult:
        """Publish the drafts that `push` wrote."""

    async def rollback(self, to: str | None = None) -> OperationResult:
        """Restore a previous release - named, or the one before this."""

    async def discard(self, paths: "list[str]") -> OperationResult:
        """Undo the edits to these files, back to what is live."""

    def preview_url(self, path: str = "/") -> str:
        """A URL that renders the drafts, without publishing them."""
