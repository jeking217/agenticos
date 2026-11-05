"""Git is the workspace's memory, so its edge cases are the feature's edge cases.

These run against a real repository in a temporary directory rather than a mock:
the behaviours that matter (a rename reporting two paths, an empty commit, a tag
that already exists) are git's, and a mock would assert my guesses about them.
"""

from __future__ import annotations

import pytest

from app.cms.errors import CmsError
from app.cms.workspace import SiteWorkspace


@pytest.fixture
def workspace(tmp_path) -> SiteWorkspace:
    return SiteWorkspace(tmp_path / "site")


async def pull(workspace: SiteWorkspace, files: dict[str, str], *, tag: str | None = None) -> None:
    await workspace.ensure_repository()
    for path, text in files.items():
        target = workspace.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    await workspace.commit("pull")
    if tag:
        await workspace.tag(tag)


@pytest.mark.anyio
async def test_a_first_pull_is_the_baseline_commit(workspace):
    await pull(workspace, {"/views/index.html": "v1"}, tag="cms-release-1")

    assert await workspace.has_commits()
    assert await workspace.status() == []
    assert await workspace.releases() == ["cms-release-1"]
    assert await workspace.current_branch() == "main"


@pytest.mark.anyio
async def test_a_changed_file_reads_as_modified_an_added_one_as_added(workspace):
    await pull(workspace, {"/views/index.html": "v1", "/views/about.html": "about"})
    (workspace.root / "views/index.html").write_text("v2")
    (workspace.root / "views/contact.html").write_text("new")
    (workspace.root / "views/about.html").unlink()

    status = {change.path: change.status for change in await workspace.status()}

    assert status == {
        "/views/index.html": "M",
        "/views/contact.html": "A",
        "/views/about.html": "D",
    }


@pytest.mark.anyio
async def test_a_new_file_inside_an_existing_directory_is_reported_by_name(workspace):
    await pull(workspace, {"/views/index.html": "v1"})
    (workspace.root / "views/deep/nested.html").parent.mkdir(parents=True)
    (workspace.root / "views/deep/nested.html").write_text("x")

    assert [change.path for change in await workspace.status()] == ["/views/deep/nested.html"]


@pytest.mark.anyio
async def test_committing_nothing_does_not_add_an_empty_commit(workspace):
    await pull(workspace, {"/views/index.html": "v1"})

    assert await workspace.commit("no change") is False


@pytest.mark.anyio
async def test_releases_are_listed_oldest_first(workspace):
    await pull(workspace, {"/views/index.html": "v1"}, tag="cms-release-1")
    (workspace.root / "views/index.html").write_text("v2")
    await workspace.commit("edit")
    await workspace.tag("cms-release-2")

    assert await workspace.releases() == ["cms-release-1", "cms-release-2"]


@pytest.mark.anyio
async def test_tagging_the_same_release_twice_is_a_no_op(workspace):
    await pull(workspace, {"/views/index.html": "v1"}, tag="cms-release-1")

    await workspace.tag("cms-release-1")

    assert await workspace.releases() == ["cms-release-1"]


@pytest.mark.anyio
async def test_rolling_back_restores_the_content_and_keeps_the_history(workspace):
    await pull(workspace, {"/views/index.html": "v1", "/views/about.html": "about"}, tag="cms-release-1")
    (workspace.root / "views/index.html").write_text("v2")
    await workspace.commit("edit")
    await workspace.tag("cms-release-2")

    await workspace.restore("cms-release-1")

    assert (workspace.root / "views/index.html").read_text() == "v1"
    assert await workspace.status() == [], "the restore is committed, not left dirty"
    assert await workspace.releases() == ["cms-release-1", "cms-release-2"], "history is not rewritten"


@pytest.mark.anyio
async def test_a_branch_leaves_the_site_state_alone(workspace):
    await pull(workspace, {"/views/index.html": "v1"})

    await workspace.branch("feat/hero")

    assert await workspace.current_branch() == "feat/hero"
    assert await workspace.status() == []


@pytest.mark.anyio
async def test_a_workspace_that_was_never_initialised_reports_nothing_rather_than_failing(tmp_path):
    workspace = SiteWorkspace(tmp_path / "never")

    assert await workspace.status() == []
    assert await workspace.releases() == []
    assert not await workspace.has_commits()


# -- discarding one file's edits ---------------------------------------------
#
# "Undo this file" is the diff card's per-row action, and it must be narrower
# than a rollback: the person keeps every other edit they have made.


@pytest.mark.anyio
async def test_discarding_one_file_restores_it_and_leaves_the_others_alone(workspace):
    await pull(workspace, {"/views/index.html": "v1", "/views/about.html": "about"})
    (workspace.root / "views/index.html").write_text("v2")
    (workspace.root / "views/about.html").write_text("about2")

    discarded = await workspace.revert(["/views/index.html"])

    assert discarded == ["/views/index.html"]
    assert (workspace.root / "views/index.html").read_text() == "v1"
    assert (workspace.root / "views/about.html").read_text() == "about2"


@pytest.mark.anyio
async def test_discarding_a_file_that_was_only_added_removes_it(workspace):
    await pull(workspace, {"/views/index.html": "v1"})
    (workspace.root / "views/new.html").write_text("new")

    discarded = await workspace.revert(["/views/new.html"])

    assert discarded == ["/views/new.html"]
    assert not (workspace.root / "views/new.html").exists()
    assert await workspace.status() == []


@pytest.mark.anyio
async def test_discarding_a_deleted_file_brings_it_back(workspace):
    await pull(workspace, {"/views/about.html": "about"})
    (workspace.root / "views/about.html").unlink()

    assert await workspace.revert(["/views/about.html"]) == ["/views/about.html"]
    assert (workspace.root / "views/about.html").read_text() == "about"


@pytest.mark.anyio
async def test_discarding_an_untouched_file_reports_nothing(workspace):
    await pull(workspace, {"/views/index.html": "v1"})

    assert await workspace.revert(["/views/index.html"]) == []


@pytest.mark.anyio
async def test_discarding_a_path_that_climbs_out_of_the_workspace_is_refused(workspace):
    await pull(workspace, {"/views/index.html": "v1"})

    with pytest.raises(CmsError) as refused:
        await workspace.revert(["../../secrets.html"])

    assert refused.value.code == "cms_path_rejected"


@pytest.mark.anyio
async def test_discarding_in_a_workspace_with_no_commits_reports_nothing(tmp_path):
    workspace = SiteWorkspace(tmp_path / "never")

    assert await workspace.revert(["/views/index.html"]) == []


@pytest.mark.anyio
async def test_discarding_goes_back_to_the_release_rather_than_the_unreleased_draft(workspace):
    await pull(workspace, {"/views/index.html": "v1"}, tag="cms-release-1")
    (workspace.root / "views/index.html").write_text("v2")
    await workspace.commit("draft")  # a push commits what it saved as a draft

    assert await workspace.revert(["/views/index.html"], since="cms-release-1") == ["/views/index.html"]
    assert (workspace.root / "views/index.html").read_text() == "v1"


@pytest.mark.anyio
async def test_committing_a_discard_leaves_the_other_edits_pending(workspace):
    await pull(workspace, {"/views/index.html": "v1", "/views/about.html": "about"})
    (workspace.root / "views/index.html").write_text("v2")
    (workspace.root / "views/about.html").write_text("about2")

    await workspace.revert(["/views/index.html"])
    committed = await workspace.commit_paths(["/views/index.html"], "discard")

    # Nothing to record: the file went back to what HEAD already holds, so the
    # only thing that must survive is the other edit - still uncommitted.
    assert committed is False
    assert [change.path for change in await workspace.status()] == ["/views/about.html"], (
        "an undo must not commit somebody's unsaved work"
    )


@pytest.mark.anyio
async def test_committing_a_discarded_draft_clears_it_from_the_next_diff(workspace):
    await pull(workspace, {"/views/index.html": "v1"}, tag="cms-release-1")
    (workspace.root / "views/index.html").write_text("v2")
    await workspace.commit("draft")

    await workspace.revert(["/views/index.html"], since="cms-release-1")

    assert await workspace.commit_paths(["/views/index.html"], "discard") is True
    assert await workspace.status() == []
