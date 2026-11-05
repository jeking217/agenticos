"""Tests for the Unione CMS capability's configuration and registration.

The capability is the only part of the Web-site programming feature that a
person configures per agent, so this file is about the boundary: which settings
are accepted, which are refused before an agent is ever published, and what the
Builder is told the capability contributes.

Nothing here starts an agent or reaches a CMS. The tools are exercised through
their runtime seam, which is the whole point of putting the CMS work in
`app.cms` and leaving the capability as an adapter - it can be tested without a
database, a network or a model.
"""

import json

import pytest
from pydantic import ValidationError

from app.agents.capabilities import get, load_builtins
from app.agents.capabilities.unione_cms import UnioneCmsConfig
from app.agents.capabilities.unione_cms._capability import UnioneCms

#: The nine tools the plan promises. Named here so a rename is a failing test
#: rather than a silently different prompt for every agent that enables this.
TOOL_IDS = (
    "cms_site_list",
    "cms_pull",
    "cms_diff",
    "cms_discard",
    "cms_verify",
    "cms_push",
    "cms_release",
    "cms_rollback",
    "cms_preview_url",
)


def test_config_defaults_bound_the_blast_radius():
    config = UnioneCmsConfig(site_id=7)

    assert config.site_id == 7
    assert "html" in config.allowed_ext
    assert "java" not in config.allowed_ext, "server-side source is out of scope"
    assert 0 < config.max_file_bytes <= 32 * 1024 * 1024
    assert config.auto_sync_drafts is True


def test_allowed_ext_is_normalised_so_a_dotted_entry_is_not_a_silent_no_op():
    config = UnioneCmsConfig(site_id=1, allowed_ext=[".HTML", " CSS "])

    assert config.allowed_ext == ["html", "css"]


@pytest.mark.parametrize("bad", ["../etc/passwd", "ht/ml", "", "a.b.c"])
def test_allowed_ext_refuses_anything_that_is_not_an_extension(bad):
    with pytest.raises(ValidationError):
        UnioneCmsConfig(site_id=1, allowed_ext=[bad])


def test_site_id_is_required_and_positive():
    with pytest.raises(ValidationError):
        UnioneCmsConfig()
    with pytest.raises(ValidationError):
        UnioneCmsConfig(site_id=0)


def test_capability_is_registered_with_every_tool_declared():
    load_builtins()
    entry = get("unione_cms_web")

    assert entry.name
    assert entry.side_effecting is True, "it writes to a live CMS"
    declared = tuple(tool.id for tool in entry.tools)
    assert declared == TOOL_IDS


def test_config_schema_is_the_one_the_builder_validates_against():
    load_builtins()
    entry = get("unione_cms_web")

    assert entry.config_schema is UnioneCmsConfig


class _FakeRuntime:
    """Records what the tools asked for, and answers with something checkable."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def site_list(self):
        self.calls.append(("site_list", None))
        return [{"id": 7, "title": "Main", "ctx": "/cms/main", "can_edit": True}]

    async def pull(self):
        self.calls.append(("pull", None))
        return {"files": 12, "baseline": "cms-release-3"}

    async def diff(self):
        self.calls.append(("diff", None))
        return [{"path": "/views/index.html", "status": "M"}]

    async def verify(self):
        self.calls.append(("verify", None))
        return {"ok": True, "failures": []}

    async def discard(self, paths):
        self.calls.append(("discard", list(paths)))
        return {"ok": True, "discarded": list(paths), "recalled": [], "failed": []}

    async def push(self):
        self.calls.append(("push", None))
        return {"saved": ["/views/index.html"]}

    async def release(self):
        self.calls.append(("release", None))
        return {"released": 3, "version": 4}

    async def rollback(self, to=None):
        self.calls.append(("rollback", to))
        return {"restored": to or "previous"}

    def preview_url(self, path="/"):
        self.calls.append(("preview_url", path))
        return f"http://localhost/preview/{path.lstrip('/')}"


@pytest.fixture
def toolset():
    runtime = _FakeRuntime()
    capability = UnioneCms(site_id=7, runtime=runtime)
    return capability.get_toolset(), runtime


def test_every_tool_is_reachable_by_the_name_the_model_sees(toolset):
    tools, _ = toolset

    for tool_id in TOOL_IDS:
        assert tool_id in tools.tools, tool_id


@pytest.mark.anyio
async def test_site_list_answers_with_json_the_model_can_read(toolset):
    tools, runtime = toolset

    result = await tools.cms_site_list()

    assert json.loads(result) == [
        {"id": 7, "title": "Main", "ctx": "/cms/main", "can_edit": True}
    ]
    assert runtime.calls == [("site_list", None)]


@pytest.mark.anyio
async def test_diff_reports_the_paths_that_changed(toolset):
    tools, _ = toolset

    result = await tools.cms_diff()

    assert json.loads(result)["changes"][0]["path"] == "/views/index.html"


@pytest.mark.anyio
async def test_discard_names_the_files_the_model_asked_to_undo(toolset):
    tools, runtime = toolset

    result = await tools.cms_discard(["/views/index.html"])

    assert json.loads(result)["discarded"] == ["/views/index.html"]
    assert runtime.calls == [("discard", ["/views/index.html"])]


@pytest.mark.anyio
async def test_rollback_defaults_to_the_previous_release(toolset):
    tools, runtime = toolset

    await tools.cms_rollback()
    await tools.cms_rollback(to="cms-release-2")

    assert runtime.calls == [("rollback", None), ("rollback", "cms-release-2")]


@pytest.mark.anyio
async def test_preview_url_is_relative_to_the_site(toolset):
    tools, _ = toolset

    result = tools.cms_preview_url(path="/about.html")

    assert json.loads(result)["url"].endswith("/about.html")


class _FlowStub:
    """The six steps, answered the way the service answers them."""

    def __init__(self, site) -> None:
        self.site = site
        self.pulled = 0

    async def pull(self):
        from app.cms.service import PullResult

        self.pulled += 1
        return PullResult(files=3, baseline="cms-release-1", created=True)

    async def diff(self):
        from app.cms.sync import FileChange, SyncPlan

        return SyncPlan(changes=[FileChange(path="/views/index.html", status="M", resource_id=5)])

    async def verify(self):
        from app.cms.service import VerifyReport

        return VerifyReport(ok=True, checked=1)

    async def push(self):
        from app.cms.service import PushReport

        return PushReport(saved=["/views/index.html"])

    async def release(self, *, confirmed: bool, foreign_drafts=None):
        assert confirmed is True, "the approval gate is the confirmation"
        return {"ok": True, "released": 2, "files": ["/views/index.html"], "tag": "cms-release-2"}

    async def rollback(self, to=None):
        return {"ok": True, "tag": to or "cms-release-1", "restored": []}

    async def discard(self, paths):
        from app.cms.service import DiscardReport

        return DiscardReport(discarded=list(paths))

    def preview_url(self, path="/"):
        return f"http://localhost/preview/{path.lstrip('/')}"


@pytest.mark.anyio
async def test_the_agent_drives_the_same_flow_the_console_does():
    """The adapter is one line per step, and this is the assertion that it is."""
    from app.cms.client import CmsSiteRecord
    from app.cms.runtime_impl import SiteRuntime

    site = CmsSiteRecord(id=7, title="Main", ctx="/cms/main")
    runtime = SiteRuntime(flow=_FlowStub(site))
    tools = UnioneCms(site_id=7, runtime=runtime).get_toolset()

    assert json.loads(await tools.cms_pull())["files"] == 3
    assert json.loads(await tools.cms_diff())["changes"][0]["resourceId"] == 5
    assert json.loads(await tools.cms_verify())["ok"] is True
    assert json.loads(await tools.cms_push())["saved"] == ["/views/index.html"]
    released = json.loads(await tools.cms_release())
    assert released["ok"] is True and released["tag"] == "cms-release-2"
    assert json.loads(await tools.cms_site_list())[0]["id"] == 7
    assert json.loads(await tools.cms_discard(["/views/index.html"]))["discarded"] == [
        "/views/index.html"
    ]
