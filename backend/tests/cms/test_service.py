"""The flow, end to end, against a fake CMS and a real repository.

The fake client records the calls, so the tests can assert the *order* - which
is the part of this service that matters. Pulling before diffing, verifying
before pushing, upgrading before overwriting something published: none of that
is visible in a returned value, and all of it is what makes the flow safe.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.cms.client import CmsResourceRecord, CmsSiteRecord
from app.cms.errors import PUBLISHED, VERIFY_FAILED, CmsError
from app.cms.preview import PreviewSigner
from app.cms.service import CmsSiteService
from app.cms.workspace import SiteWorkspace

SITE = CmsSiteRecord(
    id=7, title="Main", ctx="/cms/main", space_id=11, space_vers=3, admin_userid=42
)


def zip_of(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeClient:
    """A CMS that records what it was asked and refuses what the real one refuses."""

    def __init__(self, *, resources=None, fail_verify: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.resources = list(resources or [])
        self.fail_verify = fail_verify or set()
        self.published_names: set[str] = set()
        self.upgraded: set[int] = set()
        self.archive = zip_of({"/views/index.html": b"<h1>v1</h1>"})

    async def export_site(self, site_id: int) -> bytes:
        self.calls.append(("export_site", {"site_id": site_id}))
        return self.archive

    async def find_resources(self, site_id: int):
        self.calls.append(("find_resources", {"site_id": site_id}))
        return self.resources

    async def verify(self, *, site_id, name, body_text, resource_id=None):
        self.calls.append(("verify", {"name": name, "resource_id": resource_id}))
        if name in self.fail_verify:
            raise CmsError(VERIFY_FAILED, f"{name}: line 3 expect END")

    async def save_resource(self, *, site_id, space_id, name, types, body_text=None, title=None, resource_id=None):
        self.calls.append(("save_resource", {"name": name, "resource_id": resource_id, "space_id": space_id}))
        published = {r.id for r in self.resources if r.name in self.published_names}
        if resource_id is not None and resource_id in published and resource_id not in self.upgraded:
            raise CmsError(PUBLISHED, "资源已发布，请先升级资源！")
        if resource_id is None:
            new_id = 900 + len(self.resources)
            self.resources.append(
                CmsResourceRecord(id=new_id, name=name, types=types, space_id=space_id)
            )
            return new_id
        return resource_id

    async def upgrade(self, resource_id: int):
        self.calls.append(("upgrade", {"resource_id": resource_id}))
        self.upgraded.add(resource_id)
        return 1

    async def delete_resources(self, ids: list[int]):
        self.calls.append(("delete_resources", {"ids": ids}))
        return len(ids)

    async def release_all(self, site_id: int):
        self.calls.append(("release_all", {"site_id": site_id}))
        return 4

    async def build_html(self, site_ids: list[int]):
        self.calls.append(("build_html", {"site_ids": site_ids}))


def service(tmp_path, client: FakeClient, *, user_id: int = 42) -> CmsSiteService:
    return CmsSiteService(
        client=client,
        workspace=SiteWorkspace(tmp_path / "site"),
        site=SITE,
        user_id=user_id,
        allowed_ext={"html", "css", "js"},
        max_file_bytes=1024 * 1024,
        signer=PreviewSigner("secret"),
        preview_origin="https://console.example.com",
    )


@pytest.mark.anyio
async def test_a_pull_unpacks_the_site_and_marks_the_baseline(tmp_path):
    client = FakeClient()

    result = await service(tmp_path, client).pull()

    assert result.files == 1
    assert result.baseline == "cms-release-3"
    assert result.created is True
    assert (tmp_path / "site/views/index.html").read_text() == "<h1>v1</h1>"


@pytest.mark.anyio
async def test_diff_reports_a_changed_file_with_the_resource_it_belongs_to(tmp_path):
    client = FakeClient(resources=[CmsResourceRecord(id=5, name="/views/index.html", types="text", space_id=11)])
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")

    plan = await flow.diff()

    assert [(c.path, c.status, c.resource_id) for c in plan.changes] == [("/views/index.html", "M", 5)]


@pytest.mark.anyio
async def test_verify_checks_the_changed_file_through_the_save_less_path(tmp_path):
    client = FakeClient(resources=[CmsResourceRecord(id=5, name="/views/index.html", types="text")])
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")

    report = await flow.verify()

    assert report.ok and report.checked == 1
    assert client.calls[-1] == ("verify", {"name": "/views/index.html", "resource_id": 5})


@pytest.mark.anyio
async def test_a_template_that_does_not_parse_is_named_without_being_saved(tmp_path):
    client = FakeClient(fail_verify={"/views/index.html"})
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<% if %>")

    report = await flow.verify()

    assert report.ok is False
    assert report.failures == ["/views/index.html"]
    assert "line 3" in report.detail
    assert not [call for call in client.calls if call[0] == "save_resource"]


@pytest.mark.anyio
async def test_pushing_a_published_file_upgrades_it_first(tmp_path):
    client = FakeClient(resources=[CmsResourceRecord(id=5, name="/views/index.html", types="text", space_id=11)])
    client.published_names = {"/views/index.html"}
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")

    report = await flow.push()

    assert report.ok and report.saved == ["/views/index.html"]
    kinds = [name for name, _ in client.calls]
    assert kinds.index("upgrade") < len(kinds) - 1
    assert ("upgrade", {"resource_id": 5}) in client.calls


@pytest.mark.anyio
async def test_a_new_file_is_created_rather_than_updated(tmp_path):
    client = FakeClient()
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/about.html").write_text("about")

    await flow.push()

    saved = [payload for name, payload in client.calls if name == "save_resource"]
    assert saved[-1]["resource_id"] is None
    assert saved[-1]["name"] == "/views/about.html"


@pytest.mark.anyio
async def test_a_deleted_file_is_deleted_by_its_resource_id(tmp_path):
    client = FakeClient(
        resources=[
            CmsResourceRecord(id=5, name="/views/index.html", types="text"),
            CmsResourceRecord(id=6, name="/views/about.html", types="text"),
        ]
    )
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/about.html").write_text("about")
    await flow.push()
    (tmp_path / "site/views/about.html").unlink()

    report = await flow.push()

    assert report.deleted == ["/views/about.html"]
    assert ("delete_resources", {"ids": [6]}) in client.calls


@pytest.mark.anyio
async def test_release_is_refused_until_the_change_was_verified(tmp_path):
    client = FakeClient(resources=[CmsResourceRecord(id=5, name="/views/index.html", types="text")])
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")

    result = await flow.release(confirmed=True)

    assert result["ok"] is False
    codes = [b["code"] for b in result["blockers"]]
    assert "verify_failed" in codes and "nothing_to_publish" in codes
    assert not [call for call in client.calls if call[0] == "release_all"]


@pytest.mark.anyio
async def test_an_edit_made_after_the_draft_blocks_the_release(tmp_path):
    client = FakeClient(resources=[CmsResourceRecord(id=5, name="/views/index.html", types="text")])
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")
    await flow.verify()
    await flow.push()
    (tmp_path / "site/views/index.html").write_text("<h1>v3</h1>")

    result = await flow.release(confirmed=True)

    assert result["ok"] is False
    assert [b["code"] for b in result["blockers"]] == ["drafts_pending"]
    assert "/views/index.html" in result["blockers"][0]["message"]


@pytest.mark.anyio
async def test_a_verified_confirmed_release_names_what_went_live(tmp_path):
    client = FakeClient(resources=[CmsResourceRecord(id=5, name="/views/index.html", types="text")])
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")
    await flow.verify()
    await flow.push()

    result = await flow.release(confirmed=True)

    assert result["ok"] is True
    assert result["tag"] == "cms-release-4"
    assert result["files"] == ["/views/index.html"]
    assert ("build_html", {"site_ids": [7]}) in client.calls


@pytest.mark.anyio
async def test_a_release_that_was_not_confirmed_does_not_reach_the_cms(tmp_path):
    client = FakeClient(resources=[CmsResourceRecord(id=5, name="/views/index.html", types="text")])
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")
    await flow.verify()
    await flow.push()

    result = await flow.release(confirmed=False)

    assert result["ok"] is False
    assert [b["code"] for b in result["blockers"]] == ["not_confirmed"]
    assert not [call for call in client.calls if call[0] == "release_all"]


@pytest.mark.anyio
async def test_rolling_back_restores_the_files_and_publishes_them_again(tmp_path):
    client = FakeClient(resources=[CmsResourceRecord(id=5, name="/views/index.html", types="text")])
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")
    await flow.verify()
    await flow.push()
    await flow.release(confirmed=True)

    result = await flow.rollback()

    assert result["ok"] is True
    assert result["tag"] == "cms-release-3"
    assert (tmp_path / "site/views/index.html").read_text() == "<h1>v1</h1>"
    assert len([call for call in client.calls if call[0] == "release_all"]) == 2


@pytest.mark.anyio
async def test_discarding_a_file_restores_it_and_shrinks_the_diff(tmp_path):
    client = FakeClient(
        resources=[
            CmsResourceRecord(id=5, name="/views/index.html", types="text", space_id=11),
            CmsResourceRecord(id=6, name="/views/about.html", types="text", space_id=11),
        ]
    )
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")
    (tmp_path / "site/views/about.html").write_text("about")

    discarded = await flow.discard(["/views/index.html"])

    assert discarded.discarded == ["/views/index.html"]
    assert discarded.recalled == [], "it was never pushed, so the CMS holds nothing to take back"
    assert (tmp_path / "site/views/index.html").read_text() == "<h1>v1</h1>"
    assert [c.path for c in (await flow.diff()).changes] == ["/views/about.html"]


@pytest.mark.anyio
async def test_discarding_a_pushed_draft_takes_it_back_from_the_cms(tmp_path):
    client = FakeClient(resources=[CmsResourceRecord(id=5, name="/views/index.html", types="text", space_id=11)])
    flow = service(tmp_path, client)
    await flow.pull()
    (tmp_path / "site/views/index.html").write_text("<h1>v2</h1>")
    await flow.verify()
    await flow.push()

    report = await flow.discard(["/views/index.html"])

    assert report.recalled == ["/views/index.html"]
    saved = [payload for name, payload in client.calls if name == "save_resource"]
    assert saved[-1]["resource_id"] == 5
    assert [call for call in client.calls if call[0] == "save_resource"][-1][1]["name"] == "/views/index.html"
    plan = await flow.plan_release(confirmed=True)
    assert plan.files == []
    assert [b.code for b in plan.blockers] == ["nothing_to_publish"]


@pytest.mark.anyio
async def test_reading_a_file_returns_the_text_the_editor_shows(tmp_path):
    client = FakeClient()
    flow = service(tmp_path, client)
    await flow.pull()

    assert flow.read_file("/views/index.html") == "<h1>v1</h1>"


@pytest.mark.anyio
async def test_reading_a_file_outside_the_workspace_is_refused(tmp_path):
    flow = service(tmp_path, FakeClient())
    await flow.pull()

    with pytest.raises(CmsError) as refused:
        flow.read_file("../../.env")

    assert refused.value.code == "cms_path_rejected"


@pytest.mark.anyio
async def test_reading_a_file_that_is_not_there_is_a_clear_refusal(tmp_path):
    flow = service(tmp_path, FakeClient())
    await flow.pull()

    with pytest.raises(CmsError) as refused:
        flow.read_file("/views/missing.html")

    assert refused.value.code == "cms_not_found"


@pytest.mark.anyio
async def test_the_preview_link_is_signed_for_this_person_and_site(tmp_path):
    flow = service(tmp_path, FakeClient(), user_id=42)

    url = flow.preview_url("/about.html")

    assert url.startswith("https://console.example.com/api/cms/preview/")
    ticket = url.split("/api/cms/preview/")[1].split("/about.html")[0]
    assert PreviewSigner("secret").verify(ticket, site_id=7) == 42


def test_a_console_mounted_under_a_sub_path_builds_links_that_carry_it(tmp_path):
    """The link is pasted into an iframe, so it must name the path the browser knows.

    This console is deployed at `/agent/web`, and its preview lives behind the
    BFF at `/agent/web/api/cms/...`. A link built from the host root resolves to
    a different application - one that answers with a page, so the frame looks
    loaded and is showing the wrong thing.
    """
    flow = service(tmp_path, FakeClient())

    url = flow.preview_url("/about.html", prefix="/agent/web")

    assert url.startswith("/agent/web/api/cms/preview/")
    assert url.endswith("/about.html")


def test_the_debug_cookie_is_named_per_site(tmp_path):
    name, value = service(tmp_path, FakeClient()).debug_cookie()

    assert name == "unione-cms-debug-7"
    assert value.startswith("42:")
