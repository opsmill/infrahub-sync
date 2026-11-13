"""The Infrahub adapter's read loop admits each node through `load_record` (spec 020 T022).

The adapter reads the fixture's nodes through a stand-in SDK client (`tests/plan/problem_plan.py`).
On the destination side, two nodes with one mapped identity are recorded as a destination identity
ambiguity and the read continues as before: `update_or_add_model_instance` updates the stored
record with the later node's values. On the source side, they are a
`duplicate_source_identity` entry of the load report. Each admitted record fills the SDK store.
"""

from __future__ import annotations

from typing import Any

from infrahub_sync import SchemaMappingFilter, SchemaMappingTransform
from tests.plan.conftest import load_plan_problem_fixture
from tests.plan.problem_plan import FixtureDestination, build_destination

TWIN_A = "18d52a8a-0000-0000-0000-000000000061"
TWIN_B = "18d52a8a-0000-0000-0000-000000000062"


def _sites_model(adapter: FixtureDestination) -> type[Any]:
    """The runtime model bound for `LocationSite`."""
    return getattr(adapter, "LocationSite")  # noqa: B009 - bound at runtime, unknown to the type checker


def _sites(*rows: tuple[str, str]) -> dict[str, list[dict[str, Any]]]:
    return {"LocationSite": [{"local_id": node_id, "name": name, "status": "active"} for node_id, name in rows]}


def test_two_destination_nodes_with_one_identity_are_an_ambiguity() -> None:
    """Both node IDs are recorded; the read continues and updates the stored record, as before."""
    fixture = load_plan_problem_fixture()
    destination = build_destination(fixture, _sites((TWIN_A, "twin"), (TWIN_B, "twin"), ("other", "paris")))

    destination.model_loader("LocationSite", _sites_model(destination))

    (ambiguity,) = destination.destination_identity_ambiguities
    assert (ambiguity.kind, ambiguity.unique_id, ambiguity.identity, ambiguity.node_ids) == (
        "LocationSite",
        "twin",
        {"name": "twin"},
        (TWIN_A, TWIN_B),
    )
    assert sorted(site.model_dump()["local_id"] for site in destination.get_all("LocationSite")) == [TWIN_A, "other"]
    assert destination.client.store.keys == [  # ty: ignore[unresolved-attribute] -- the stand-in store
        ("LocationSite", "twin"),
        ("LocationSite", "twin"),
        ("LocationSite", "paris"),
    ]


def test_one_node_per_identity_is_no_ambiguity() -> None:
    """Distinct identities record nothing."""
    fixture = load_plan_problem_fixture()
    destination = build_destination(fixture, _sites((TWIN_A, "twin"), ("other", "paris")))

    destination.model_loader("LocationSite", _sites_model(destination))

    assert destination.destination_identity_ambiguities == []


def test_on_the_source_side_two_nodes_with_one_identity_are_a_duplicate() -> None:
    """Infrahub as a source records the second node as `duplicate_source_identity` and keeps the first."""
    fixture = load_plan_problem_fixture()
    source = build_destination(fixture, _sites((TWIN_A, "twin"), (TWIN_B, "twin")))
    source.target = "source"

    source.model_loader("LocationSite", _sites_model(source))

    assert [
        (entry.reason, entry.record.source_id, entry.existing.source_id if entry.existing else None)
        for entry in source.load_report.entries
    ] == [("duplicate_source_identity", TWIN_B, TWIN_A)]
    assert [site.model_dump()["local_id"] for site in source.get_all("LocationSite")] == [TWIN_A]
    assert source.destination_identity_ambiguities == []


def test_on_the_source_side_filters_and_transforms_name_the_node_id() -> None:
    """A filtered node is recorded by its node ID; a failed transform names the node it failed for."""
    fixture = load_plan_problem_fixture()
    source = build_destination(fixture, _sites(("kept", "paris"), ("removed", "berlin"), ("broken", "rome")))
    source.target = "source"
    (element,) = [entry for entry in source.config.schema_mapping if entry.name == "LocationSite"]
    element.filters = [SchemaMappingFilter(field="name", operation="!=", value="berlin")]
    element.transforms = [
        SchemaMappingTransform(field="name", expression="{{ name if name != 'rome' else missing.attribute }}")
    ]

    source.model_loader("LocationSite", _sites_model(source))

    assert source.load_report.filtered_ids["LocationSite"] == {"removed": 0}
    assert [
        (entry.reason, entry.record.source_id, entry.field, entry.config_entry) for entry in source.load_report.entries
    ] == [("transform_failed", "broken", "name", "transforms[0]")]
    assert [site.model_dump()["local_id"] for site in source.get_all("LocationSite")] == ["kept"]
