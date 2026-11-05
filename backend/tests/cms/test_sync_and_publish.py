"""What a push would do, and why a release is usually the interesting answer.

The publish guard is the one place in this feature where being helpful is
dangerous: `release/all` cannot be narrowed to "my files", so every check here
exists to make sure the person knows exactly what else is about to go live.
"""

from __future__ import annotations

import pytest

from app.cms.client import CmsResourceRecord
from app.cms.errors import CmsError
from app.cms.publish import (
    DRIFTED,
    NO_SUCH_RELEASE,
    NOTHING_TO_PUBLISH,
    NOT_CONFIRMED,
    VERIFY_FAILED,
    Release,
    build_release_plan,
    plan_rollback,
)
from app.cms.sync import plan_sync


def resource(name: str, *, id: int = 1, types: str = "text", space_id: int | None = 11):
    return CmsResourceRecord(id=id, name=name, types=types, site_id=7, space_id=space_id)


def test_only_changes_that_can_actually_be_written_become_part_of_the_plan():
    plan = plan_sync(
        [
            ("/views/index.html", "M"),
            ("/static/logo.png", "A"),
            ("/views/old.html", "D"),
        ],
        [
            resource("/views/index.html", id=1),
            resource("/static/logo.png", id=2, types="binary"),
            resource("/views/old.html", id=3),
        ],
    )

    assert [c.status for c in plan.changes] == ["M", "D"]
    assert plan.changes[0].resource_id == 1
    assert "binary" in plan.skipped[0]["reason"]


def test_a_deleted_directory_is_refused_with_the_reason_a_person_needs():
    plan = plan_sync([("/views/archive", "D")], [resource("/views/archive", types="dir")])

    assert plan.empty
    assert "directory" in plan.skipped[0]["reason"]


def test_an_unknown_path_cannot_be_deleted_from_the_cms():
    plan = plan_sync([("/views/never.html", "D")], [])

    assert plan.empty
    assert "does not have" in plan.skipped[0]["reason"]


def test_a_rename_is_an_addition_and_a_deletion_not_a_third_kind():
    plan = plan_sync([("/views/a.html", "A"), ("/views/b.html", "D")], [resource("/views/b.html", id=5)])

    assert {c.status for c in plan.changes} == {"A", "D"}


def test_a_path_without_a_leading_slash_still_matches_its_resource():
    plan = plan_sync([("views/index.html", "M")], [resource("views/index.html", id=1)])

    assert plan.changes[0].path == "/views/index.html"
    assert plan.changes[0].resource_id == 1


def test_an_unparsable_git_letter_is_skipped_not_guessed():
    plan = plan_sync([("/views/index.html", "R")], [resource("/views/index.html")])

    assert plan.empty
    assert "not a change a push can make" in plan.skipped[0]["reason"]


def ready_plan(**overrides):
    plan = plan_sync([("/views/index.html", "M")], [resource("/views/index.html", id=1)])
    arguments = dict(site_id=7, plan=plan, verified=True, confirmed=True)
    arguments.update(overrides)
    return build_release_plan(**arguments)


def test_a_verified_confirmed_change_may_be_released():
    release = ready_plan()

    assert release.allowed
    assert release.files == ["/views/index.html"]


def test_nothing_to_publish_is_a_refusal_rather_than_a_no_op_release():
    empty = plan_sync([], [])

    release = build_release_plan(site_id=7, plan=empty, verified=True, confirmed=True)

    assert not release.allowed
    assert [b.code for b in release.blockers] == [NOTHING_TO_PUBLISH]


def test_unverified_changes_do_not_ship():
    release = ready_plan(verified=False)

    assert [b.code for b in release.blockers] == [VERIFY_FAILED]


def test_a_template_that_failed_the_parser_is_named_in_the_refusal():
    release = ready_plan(verify_failures=["/views/index.html"])

    assert release.blockers[0].code == VERIFY_FAILED
    assert "/views/index.html" in release.blockers[0].message


def test_a_release_nobody_confirmed_is_refused():
    release = ready_plan(confirmed=False)

    assert [b.code for b in release.blockers] == [NOT_CONFIRMED]


def test_a_file_somebody_else_changed_since_the_pull_blocks_the_release():
    release = ready_plan(drifted=["/layout/base.html"])

    assert release.blockers[0].code == DRIFTED
    assert "/layout/base.html" in release.blockers[0].message


def test_other_peoples_drafts_are_a_warning_because_the_cms_publishes_them_too():
    release = ready_plan(foreign_drafts=["/views/about.html", "/views/contact.html"])

    assert release.allowed, "the person's own change is still publishable"
    assert release.warnings and "2" in release.warnings[0]
    assert release.to_json()["foreignDrafts"] == ["/views/about.html", "/views/contact.html"]


def test_rolling_back_defaults_to_the_release_before_this_one():
    releases = [Release("cms-release-1"), Release("cms-release-2"), Release("cms-release-3")]

    assert plan_rollback(releases).tag == "cms-release-2"


def test_rolling_back_to_a_named_release_finds_it():
    releases = [Release("cms-release-1"), Release("cms-release-2")]

    assert plan_rollback(releases, requested="cms-release-1").tag == "cms-release-1"


def test_an_unknown_tag_is_refused_with_the_tag_in_the_message():
    with pytest.raises(CmsError) as caught:
        plan_rollback([Release("cms-release-1")], requested="cms-release-9")

    assert caught.value.code == NO_SUCH_RELEASE
    assert "cms-release-9" in caught.value.message


def test_a_site_with_one_release_cannot_go_back():
    with pytest.raises(CmsError) as caught:
        plan_rollback([Release("cms-release-1")])

    assert caught.value.code == NO_SUCH_RELEASE


def test_a_site_with_no_releases_says_so():
    with pytest.raises(CmsError):
        plan_rollback([])
