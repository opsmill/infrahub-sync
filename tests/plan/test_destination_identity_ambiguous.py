"""Two Infrahub nodes with one configured identity are a plan problem (spec 009, decided 2026-10-17).

The destination store holds one model per identity (the DiffSync unique ID of the mapping's
`identifiers`). When Infrahub holds two or more nodes with that identity, the load records them
instead of letting the last node replace the first. The plan lists a
`destination_identity_ambiguous` problem with the kind, the identity and the node IDs, leaves
out the source record with that identity (and its dependents), and plans no operation for that
identity, delete included. The rest of the plan continues.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from diffsync import Adapter, DiffSyncModel

from infrahub_sync.plan.destination_ambiguity import DestinationIdentityAmbiguities, DestinationIdentityAmbiguity
from tests.plan.problem_fixtures import (
    TWIN_SITE_NAME,
    TWIN_SITE_NODE_IDS,
    TWIN_SITE_SOURCE_ID,
    Variant,
    destination_records,
    source_records,
    twin_site_nodes,
)
from tests.plan.problem_plan import plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from tests.plan.problem_plan import PlannedFixture

PROBLEM_TYPE = "destination_identity_ambiguous"


def _ambiguous(planned: PlannedFixture) -> list[Any]:
    return [problem for problem in planned.report.problems if problem.problem_type == PROBLEM_TYPE]


def _site_operations(planned: PlannedFixture, name: str) -> list[Any]:
    return [
        operation
        for operation in planned.operations
        if operation.kind == "LocationSite" and operation.identity.get("name") == name
    ]


def test_the_source_record_with_an_ambiguous_identity_is_left_out(tmp_path: Path) -> None:
    planned = plan_fixture(tmp_path / "run")

    (problem,) = _ambiguous(planned)
    assert problem.record.kind == "LocationSite"
    assert problem.record.source_id == TWIN_SITE_SOURCE_ID
    assert problem.record.identity == {"name": TWIN_SITE_NAME}
    assert problem.destination_ids == list(TWIN_SITE_NODE_IDS)
    assert problem.reason == (
        'Infrahub holds 2 LocationSite objects with the identity {"name":"twin"}; the plan cannot tell '
        "which one LocationSite record source ID 6 maps to, so it plans no operation for that identity."
    )
    (left_out,) = [item for item in planned.report.left_out if item.record == problem.record]
    assert (left_out.cause, left_out.problem_types) == ("has_problem", [PROBLEM_TYPE])
    assert _site_operations(planned, TWIN_SITE_NAME) == []
    assert _site_operations(planned, "paris"), "The rest of the plan continues."


def test_the_problem_holds_no_attribute_value_beyond_the_identity(tmp_path: Path) -> None:
    planned = plan_fixture(tmp_path / "run")

    (problem,) = _ambiguous(planned)
    line = problem.model_dump_json()
    assert '"status"' not in line
    assert '"planned"' not in line
    assert problem.value is None


def test_an_ambiguous_identity_without_a_source_record_is_listed_and_not_deleted(tmp_path: Path) -> None:
    records = source_records(Variant.NO_PROBLEMS)
    destination = destination_records(records)
    destination["LocationSite"] = [*twin_site_nodes(), {"local_id": "node-rome", "name": "rome", "status": "active"}]

    planned = plan_fixture(tmp_path / "run", records=records, destination=destination)

    (problem,) = _ambiguous(planned)
    assert problem.record.source_id is None
    assert problem.record.identity == {"name": TWIN_SITE_NAME}
    assert problem.destination_ids == list(TWIN_SITE_NODE_IDS)
    assert planned.report.left_out == [], "No source record has that identity, so nothing is left out."
    assert _site_operations(planned, TWIN_SITE_NAME) == [], "No delete is proposed for the ambiguous nodes."
    assert [operation.action for operation in _site_operations(planned, "rome")] == ["delete"], (
        "A node that is not ambiguous is still deleted."
    )


def test_a_plan_whose_destination_has_one_node_per_identity_lists_no_such_problem(tmp_path: Path) -> None:
    records = source_records(Variant.ALL)
    destination = destination_records(records)
    # The one node holds another status than the source, so the plan updates it.
    destination["LocationSite"] = twin_site_nodes()[1:]

    planned = plan_fixture(tmp_path / "run", records=records, destination=destination)

    assert _ambiguous(planned) == []
    assert [operation.action for operation in _site_operations(planned, TWIN_SITE_NAME)] == ["update"]


# --- The recorder the destination adapters store through --------------------------------


class _Site(DiffSyncModel):
    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("status",)

    name: str
    status: str | None = None
    local_id: str | None = None


class _Store(Adapter):
    LocationSite = _Site
    top_level = ["LocationSite"]  # noqa: RUF012


def test_the_recorder_keeps_one_model_and_records_every_node_of_an_ambiguous_identity() -> None:
    adapter = _Store()
    recorder = DestinationIdentityAmbiguities()

    for node_id, status in (("id-1", "active"), ("id-2", "planned"), ("id-3", "active")):
        recorder.store(adapter, _Site(name="dc", status=status, local_id=node_id))
    recorder.store(adapter, _Site(name="other", local_id="id-4"))

    assert len(adapter.get_all("LocationSite")) == 2
    assert recorder.ambiguities() == [
        DestinationIdentityAmbiguity(
            kind="LocationSite", unique_id="dc", identifiers={"name": "dc"}, node_ids=("id-1", "id-2", "id-3")
        )
    ]


def test_the_same_node_stored_twice_is_not_ambiguous() -> None:
    adapter = _Store()
    recorder = DestinationIdentityAmbiguities()

    recorder.store(adapter, _Site(name="dc", local_id="id-1"))
    recorder.store(adapter, _Site(name="dc", local_id="id-1"))

    assert recorder.ambiguities() == []
