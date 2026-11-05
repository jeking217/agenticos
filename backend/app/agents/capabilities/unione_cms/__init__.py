"""Unione CMS capability - edit a site's web code and publish it."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app.agents.capabilities._registry import (
    CapabilityBuildContext,
    CapabilityToolInfo,
    register,
)
from app.agents.capabilities.unione_cms._capability import UnioneCms
from app.agents.capabilities.unione_cms._toolset import UnioneCmsToolset

__all__ = ["UnioneCms", "UnioneCmsConfig", "UnioneCmsToolset"]

#: What a site is made of, as far as this capability is concerned: rendered
#: templates, layouts, client code and the assets they reference. Java source is
#: not on the list - it ships with the CMS, not with the site.
DEFAULT_ALLOWED_EXT: tuple[str, ...] = (
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

#: An extension, and only that. Not a path, not a glob, not a dotted suffix -
#: a value that contains a separator would let the "extension" carry a directory
#: into a destination path.
_EXTENSION = re.compile(r"^[a-z0-9]{1,10}$")


class UnioneCmsConfig(BaseModel):
    """Which site this agent may change, and how far it may go."""

    site_id: int = Field(
        title="CMS site",
        ge=1,
        description="The site this agent edits. Fixed here rather than chosen per call.",
    )
    allowed_ext: list[str] = Field(
        title="File types it may write",
        default_factory=lambda: list(DEFAULT_ALLOWED_EXT),
        description=(
            "Extensions allowed into the workspace, without the dot. Anything else in "
            "the site's archive is skipped rather than written, so a mis-scoped pull "
            "cannot drop server-side source into the editor."
        ),
    )
    max_file_bytes: int = Field(
        title="Largest file to write (bytes)",
        default=2 * 1024 * 1024,
        ge=1024,
        le=32 * 1024 * 1024,
        description="Per-file ceiling. A larger file is reported and skipped, not truncated.",
    )
    auto_sync_drafts: bool = Field(
        title="Write drafts while editing",
        default=True,
        description=(
            "Save each change to the CMS as an unpublished draft, which is what makes "
            "the debug preview live. Off means the preview shows the last push instead."
        ),
    )
    preview_ttl_seconds: int = Field(
        title="Preview link lifetime (seconds)",
        default=600,
        ge=60,
        le=3600,
        description="How long a preview link stays valid. They are short because they bypass the cache.",
    )

    @field_validator("allowed_ext")
    @classmethod
    def _normalise(cls, value: list[str]) -> list[str]:
        """Accept `.HTML` as `html`, and refuse anything that is not an extension.

        A person typing the list by hand writes a leading dot; refusing that
        would be pedantry with a bad failure mode, because the list would then
        match nothing and every file would be skipped silently. A separator or a
        second dot is a different matter - that is a path wearing an extension's
        name, and it is refused here rather than three layers down.
        """
        cleaned: list[str] = []
        for entry in value:
            candidate = entry.strip().lower().lstrip(".")
            if not _EXTENSION.match(candidate):
                raise ValueError(f"{entry!r} is not a file extension")
            if candidate not in cleaned:
                cleaned.append(candidate)
        if not cleaned:
            raise ValueError("at least one file extension must be allowed")
        return cleaned


@register(
    id="unione_cms_web",
    name="Unione CMS site",
    category="build",
    description="Edit a Unione CMS site's web code, preview the drafts, and publish a release.",
    tools=(
        CapabilityToolInfo(
            id="cms_site_list",
            description="List the CMS sites this person may open, with whether each can be edited.",
        ),
        CapabilityToolInfo(
            id="cms_pull",
            description="Download the site's web code into the workspace and start a git baseline.",
        ),
        CapabilityToolInfo(
            id="cms_diff",
            description="Show which workspace files changed, as added, modified or deleted.",
        ),
        CapabilityToolInfo(
            id="cms_discard",
            description="Undo the edits to named files, putting them back to what is live.",
        ),
        CapabilityToolInfo(
            id="cms_verify",
            description="Check changed templates with the CMS's Beetl parser, without saving.",
        ),
        CapabilityToolInfo(
            id="cms_push",
            description="Save the changed files to the CMS as drafts; not visible to visitors.",
        ),
        CapabilityToolInfo(
            id="cms_release",
            description="Publish the site's drafts, making the change live for visitors.",
        ),
        CapabilityToolInfo(
            id="cms_rollback",
            description="Restore a previous release of the site, by tag or the one before this.",
        ),
        CapabilityToolInfo(
            id="cms_preview_url",
            description="A URL that renders the site's drafts without publishing them.",
        ),
    ),
    config_schema=UnioneCmsConfig,
    side_effecting=True,
)
def _build(ctx: CapabilityBuildContext) -> UnioneCms | None:
    """Built only where there is a runtime, which means only inside the console.

    A runtime carries the person's platform token and their workspace, so it is
    bound per run by the runner and never stored in a spec. An agent that
    enables this capability from a channel or a schedule therefore gets no CMS
    tools rather than tools that would fail on every call - the honest outcome,
    since there is nobody's site to edit.
    """
    runtime = ctx.resources.get("cms_runtime")
    if not isinstance(ctx.config, UnioneCmsConfig) or runtime is None:
        return None
    return UnioneCms(site_id=ctx.config.site_id, runtime=runtime)
