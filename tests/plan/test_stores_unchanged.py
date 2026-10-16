"""The plan steps after the load change no store and edit no diff (spec 009 T034, research R2)."""

from __future__ import annotations

import ast
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import infrahub_sync.potenda
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.derive import derive_deletes, operations_from_diff, tier_of
from infrahub_sync.plan.problems import find_problems, select_operations
from infrahub_sync.runtime_schema.domain import normalize_destination_schema
from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.plan.problem_fixtures import (
    ORDER,
    ProblemFixtureAdapter,
    Variant,
    build_models,
    destination_records,
    schema_record,
    source_records,
)
from tests.plan.problem_plan import FixtureDestination, fixture_config

if TYPE_CHECKING:
    from diffsync import Adapter, DiffSyncModel


def _record(item: DiffSyncModel) -> dict[str, Any]:
    """Every value DiffSync compares, the source record ID and the model flags."""
    return {
        **item.get_identifiers(),
        **item.get_attrs(),
        "local_id": getattr(item, "local_id", None),
        "model_flags": item.model_flags.value,
    }


def _dump(adapter: Adapter) -> bytes:
    """A canonical dump of every record of a store, by kind and unique ID."""
    return canonical_json_bytes(
        {
            kind: {item.get_unique_id(): _record(item) for item in adapter.get_all(kind)}
            for kind in sorted(adapter.top_level)
        }
    )


def test_the_stores_are_the_same_before_the_diff_and_after_the_selection() -> None:
    config = fixture_config()
    source = ProblemFixtureAdapter(
        target="source", adapter=config.source, config=config, records=source_records(Variant.ALL)
    )
    bind_runtime_models(source, build_models(config))
    destination = FixtureDestination(config, destination_records(), schema_record())
    bind_runtime_models(destination, build_models(config))
    source.load()
    destination.load()
    before = (_dump(source), _dump(destination))

    diff = destination.diff_from(source)
    diff_before = diff.str()
    resolve = partial(tier_of, tiers=None, top_level=list(ORDER))
    operations = operations_from_diff(
        diff, config=config, tier_of=resolve, source_adapter=source, destination_adapter=destination
    )
    operations += derive_deletes(
        kinds=list(ORDER),
        source_adapter=source,
        destination_adapter=destination,
        config=config,
        tier_of=resolve,
        destination_full_extract=True,
    )
    snapshot = normalize_destination_schema(schema_record())
    report = find_problems(source.load_report, [], source, snapshot, operations, config=config, destination=destination)
    selected = select_operations(operations, report)

    assert (_dump(source), _dump(destination)) == before
    assert diff.str() == diff_before

    # The IP address keeps its reference to the left-out device in the source store, while
    # its plan operation holds no `device` relationship.
    (address,) = [
        item for item in source.get_all("IpamIPAddress") if item.get_identifiers()["address"] == "10.0.0.5/32"
    ]
    assert address.get_attrs()["device"] == "core-1__C1"
    (operation,) = [
        item for item in selected if item.kind == "IpamIPAddress" and item.identity == {"address": "10.0.0.5/32"}
    ]
    assert all(reference.field != "device" for reference in operation.relationships or ())


def test_no_runtime_model_declares_children() -> None:
    models: dict[str, Any] = build_models(fixture_config())

    assert models
    for model in models.values():
        assert not getattr(model, "_children", None), model.__name__


def test_the_engine_calls_diff_from_and_no_diffsync_sync_method() -> None:
    tree = ast.parse(Path(infrahub_sync.potenda.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "diff_from" in called
    assert not called & {"sync_from", "sync_to", "diff_to"}
