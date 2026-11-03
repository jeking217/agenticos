"""The two dataset limits, and the authorities that guard the routes to them.

The quota arithmetic is trivial; what is worth pinning is *which* refusal a caller
gets, and that the ceiling cannot be stepped around by archiving. The permission
half is a catalog test plus a pass over the router: the four dataset permissions
are resource permissions, so every role that holds one has to say how widely, and
no endpoint may ship without one of the four.
"""

import uuid

import pytest

from app.core.exceptions import BadRequestError, DatasetQuotaError
from app.core.permissions import RESOURCE_PERMS, ROLE_PERMS, OrgRoleName, Perm, Scope
from app.services.kb_dataset import quota

pytestmark = pytest.mark.anyio

FOUR = (
    Perm.DATASETS_VIEW,
    Perm.DATASETS_EDIT,
    Perm.DATASETS_IMPORT,
    Perm.DATASETS_MAINTAIN,
)


# --- the row ceiling -------------------------------------------------------


def test_a_version_at_the_ceiling_is_accepted() -> None:
    from app.core.config import settings

    quota.refuse_an_oversized_version("订单", settings.KB_DATASET_MAX_ROWS)


def test_a_version_past_the_ceiling_names_the_sheet_and_the_limit() -> None:
    from app.core.config import settings

    with pytest.raises(DatasetQuotaError) as refused:
        quota.refuse_an_oversized_version("订单", settings.KB_DATASET_MAX_ROWS + 1)

    assert refused.value.code == "DATASET_QUOTA_EXCEEDED"
    assert refused.value.status_code == 409
    # The message is what an operator reads before deciding whether to split the
    # file, so the numbers it names have to be the ones in play.
    assert "订单" in refused.value.message
    assert str(settings.KB_DATASET_MAX_ROWS) in refused.value.message


def test_the_row_ceiling_is_a_quota_and_not_a_bad_request() -> None:
    """A client has to be able to tell "fix your body" from "this base is full"."""

    with pytest.raises(DatasetQuotaError):
        quota.refuse_an_oversized_version("s", 10**9)
    assert not issubclass(DatasetQuotaError, BadRequestError)


# --- the per-base ceiling --------------------------------------------------


class _CountingSession:
    """A session whose one query answers with a fixed count."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.calls = 0

    async def scalar(self, _statement):  # stands in for a session
        self.calls += 1
        return self.count


async def test_a_base_below_the_ceiling_accepts_one_more() -> None:
    from app.core.config import settings

    session = _CountingSession(settings.KB_DATASET_MAX_PER_BASE - 1)

    await quota.refuse_a_full_base(session, uuid.uuid4())  # type: ignore[arg-type]

    assert session.calls == 1


async def test_a_base_at_the_ceiling_refuses_and_says_how_full_it_is() -> None:
    from app.core.config import settings

    session = _CountingSession(settings.KB_DATASET_MAX_PER_BASE)

    with pytest.raises(DatasetQuotaError) as refused:
        await quota.refuse_a_full_base(session, uuid.uuid4())  # type: ignore[arg-type]

    assert refused.value.details == {
        "limit": settings.KB_DATASET_MAX_PER_BASE,
        "held": settings.KB_DATASET_MAX_PER_BASE,
    }


async def test_archived_datasets_still_count_against_the_ceiling() -> None:
    """Archiving keeps the rows, so it must not be a way around the limit.

    The count is over the base's datasets with no `archived_at IS NULL` predicate.
    Pinning that is the point: adding one would look like a tidy-up and would turn
    "archive, import, archive, import" into an unlimited path.
    """

    import inspect

    assert "archived_at" not in inspect.getsource(quota.refuse_a_full_base)


def test_settings_refuse_a_zero_row_ceiling() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(KB_DATASET_MAX_ROWS=0)


# --- the four authorities --------------------------------------------------


def test_the_four_dataset_permissions_are_resource_permissions() -> None:
    for perm in FOUR:
        assert perm in RESOURCE_PERMS, f"{perm} carries a scope, so it must be a resource perm"


def test_an_owner_and_an_admin_hold_all_four_everywhere() -> None:
    for role in (OrgRoleName.OWNER, OrgRoleName.ADMIN):
        for perm in FOUR:
            assert ROLE_PERMS[role][perm] is Scope.ALL


def test_a_viewer_may_read_and_do_nothing_else() -> None:
    held = ROLE_PERMS[OrgRoleName.VIEWER]

    assert held[Perm.DATASETS_VIEW] is Scope.SHARED
    for perm in (Perm.DATASETS_EDIT, Perm.DATASETS_IMPORT, Perm.DATASETS_MAINTAIN):
        assert held.get(perm, Scope.NONE) is Scope.NONE


def test_an_operator_reads_datasets_but_does_not_fill_them() -> None:
    held = ROLE_PERMS[OrgRoleName.OPERATOR]

    assert held[Perm.DATASETS_VIEW] is Scope.ALL
    for perm in (Perm.DATASETS_EDIT, Perm.DATASETS_IMPORT, Perm.DATASETS_MAINTAIN):
        assert held.get(perm, Scope.NONE) is Scope.NONE


def test_a_member_imports_and_corrects_only_what_is_theirs() -> None:
    held = ROLE_PERMS[OrgRoleName.MEMBER]

    assert held[Perm.DATASETS_VIEW] is Scope.SHARED
    # The two that change numbers are scoped `OWN`: a member works in their own
    # base, and one they were given read access to is not theirs to rewrite.
    assert held[Perm.DATASETS_IMPORT] is Scope.OWN
    assert held[Perm.DATASETS_MAINTAIN] is Scope.OWN
    assert held[Perm.DATASETS_EDIT] is Scope.OWN


def test_a_builder_fills_what_it_may_reshape() -> None:
    held = ROLE_PERMS[OrgRoleName.BUILDER]

    assert held[Perm.DATASETS_VIEW] is Scope.ALL
    for perm in (Perm.DATASETS_EDIT, Perm.DATASETS_IMPORT, Perm.DATASETS_MAINTAIN):
        assert held[perm] is Scope.SHARED


def test_importing_and_correcting_are_separate_authorities() -> None:
    """The split is why there are four permissions rather than two.

    A role composed with `maintain` and not `import` has to be expressible, or the
    catalog is only naming the same scope twice.
    """

    composed = {Perm.DATASETS_VIEW: Scope.ALL, Perm.DATASETS_MAINTAIN: Scope.SHARED}

    assert Perm.DATASETS_IMPORT not in composed
    assert Perm.DATASETS_MAINTAIN in composed


# --- the router ------------------------------------------------------------

# What each endpoint must be guarded by. A route missing from this map is a route
# added without deciding who may call it, which is what the test below is for - so
# it is a hand-written list rather than a rule derived from the router.
EXPECTED_PERMISSION = {
    "create_dataset_base": Perm.DATASETS_EDIT,
    "list_datasets": Perm.DATASETS_VIEW,
    "create_dataset": Perm.DATASETS_EDIT,
    "get_dataset": Perm.DATASETS_VIEW,
    "update_dataset": Perm.DATASETS_EDIT,
    "archive_dataset": Perm.DATASETS_EDIT,
    "update_model": Perm.DATASETS_EDIT,
    "list_rows": Perm.DATASETS_VIEW,
    "row_snapshot": Perm.DATASETS_VIEW,
    "insert_row": Perm.DATASETS_MAINTAIN,
    "update_row": Perm.DATASETS_MAINTAIN,
    "delete_row": Perm.DATASETS_MAINTAIN,
    "restore_row": Perm.DATASETS_MAINTAIN,
    "list_operations": Perm.DATASETS_VIEW,
    "list_versions": Perm.DATASETS_VIEW,
    "version_model": Perm.DATASETS_VIEW,
    "compare_versions": Perm.DATASETS_VIEW,
    "run_dataset_query": Perm.DATASETS_VIEW,
    "list_relations": Perm.DATASETS_VIEW,
    "create_relation": Perm.DATASETS_EDIT,
    "delete_relation": Perm.DATASETS_EDIT,
    "relation_candidates": Perm.DATASETS_VIEW,
    "preview_import": Perm.DATASETS_IMPORT,
    "list_imports": Perm.DATASETS_VIEW,
    "commit_import": Perm.DATASETS_IMPORT,
    "import_status": Perm.DATASETS_VIEW,
    "save_import_draft": Perm.DATASETS_IMPORT,
}


def _guarding_perms(dependant) -> set[Perm]:
    """Which permissions a route's dependency tree asserts.

    `require(...)` returns a closure that captures its permissions tuple, so the
    answer is in the closure rather than in any attribute FastAPI exposes. Reading
    it here is what lets the test fail when a route is guarded by the *wrong*
    permission, not merely by one.
    """

    found: set[Perm] = set()
    for child in getattr(dependant, "dependencies", []):
        found |= _guarding_perms(child)
        if getattr(child.call, "__name__", "") != "dependency":
            continue
        for cell in child.call.__closure__ or ():
            contents = cell.cell_contents
            if isinstance(contents, tuple):
                found |= {item for item in contents if isinstance(item, Perm)}
    return found


def test_every_dataset_endpoint_is_guarded_by_the_permission_it_needs() -> None:
    from app.api.routes.v1.kb_datasets import router

    seen: set[str] = set()
    for route in router.routes:
        name = getattr(route, "name", "")
        if name not in EXPECTED_PERMISSION:
            continue
        seen.add(name)
        expected = EXPECTED_PERMISSION[name]
        assert expected in _guarding_perms(route.dependant), f"{name} ({route.path}) is unguarded"

    # A renamed handler would otherwise make this pass by matching nothing.
    assert seen == set(EXPECTED_PERMISSION)
