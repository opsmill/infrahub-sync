"""AR3: concurrent construction across configurations, runs, and rebuilds stays isolated.

Barrier-synchronised so the threads are genuinely inside construction at the same time;
sequential rebuilds cannot show that two simultaneous runs do not share a class object.
"""

from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel
from infrahub_sync.runtime_schema import (
    ATTRIBUTE_TYPE_DOMAIN,
    bind_runtime_models,
    build_runtime_models,
    compute_consumed_schema_fingerprint,
    normalize_destination_schema,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_SNAPSHOT: dict[str, Any] = {
    "BuiltinTag": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {
            "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
            "description": {"kind": "Text", "optional": True, "default_value": None, "unique": False},
            "colour": {"kind": "Text", "optional": True, "default_value": None, "unique": False},
        },
        "relationships": {},
    },
}
_WORKERS = 8


def _configuration(name: str, fields: list[str]) -> SyncConfig:
    return SyncConfig(
        name=name,
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="BuiltinTag",
                identifiers=["name"],
                fields=[SchemaMappingField(name=field) for field in fields],
            )
        ],
    )


def _run_together(work: Sequence[Callable[..., Any]]) -> list[Any]:
    """Run every callable with all of them inside construction at the same time."""
    barrier = threading.Barrier(len(work))

    def _start(item: Callable[..., object]) -> object:
        barrier.wait(timeout=30)
        return item()

    with ThreadPoolExecutor(max_workers=len(work)) as pool:
        return list(pool.map(_start, work))


def test_concurrent_configurations_sharing_a_kind_never_share_a_class() -> None:
    snapshot = normalize_destination_schema(_SNAPSHOT)
    fields = [["name"], ["name", "description"], ["name", "colour"], ["name", "description", "colour"]]
    work = [
        (
            lambda index=index: build_runtime_models(
                snapshot=snapshot,
                configuration=_configuration(f"configuration-{index % len(fields)}", fields[index % len(fields)]),
                model_base=InfrahubModel,
            )
        )
        for index in range(_WORKERS)
    ]

    built = _run_together(work)

    classes = [models["BuiltinTag"] for models in built]
    assert len({id(model) for model in classes}) == _WORKERS
    for index, model in enumerate(classes):
        declared = set(model.model_fields) - set(InfrahubModel.model_fields) - {"local_id", "local_data"}
        assert declared == set(fields[index % len(fields)])


def test_concurrent_runs_of_one_configuration_never_share_a_class() -> None:
    snapshot = normalize_destination_schema(_SNAPSHOT)
    configuration = _configuration("one-configuration", ["name", "description"])
    work = [
        (lambda: build_runtime_models(snapshot=snapshot, configuration=configuration, model_base=InfrahubModel))
        for _ in range(_WORKERS)
    ]

    built = _run_together(work)

    classes = [models["BuiltinTag"] for models in built]
    assert len({id(model) for model in classes}) == _WORKERS
    assert len({model.__mro__[1] for model in classes}) == _WORKERS


def test_a_concurrent_rebuild_on_a_grown_schema_cannot_reach_the_earlier_classes() -> None:
    configuration = _configuration("grown", ["name", "description"])
    before = normalize_destination_schema(_SNAPSHOT)
    grown_snapshot = copy.deepcopy(_SNAPSHOT)
    grown_snapshot["BuiltinTag"]["attributes"]["extra"] = {
        "kind": "Text",
        "optional": True,
        "default_value": None,
        "unique": False,
    }
    after = normalize_destination_schema(grown_snapshot)
    original = build_runtime_models(snapshot=before, configuration=configuration, model_base=InfrahubModel)
    work = [
        (
            lambda snapshot=snapshot: build_runtime_models(
                snapshot=snapshot, configuration=configuration, model_base=InfrahubModel
            )
        )
        for snapshot in ([before, after] * (_WORKERS // 2))
    ]

    built = _run_together(work)

    assert "extra" not in original["BuiltinTag"].model_fields
    rebuilt = [models["BuiltinTag"] for models in built]
    assert all(model is not original["BuiltinTag"] for model in rebuilt)
    # The grown attribute is unmapped, so no rebuild declares it and none changes identity.
    assert all("extra" not in model.model_fields for model in rebuilt)
    assert compute_consumed_schema_fingerprint(
        configuration=configuration, snapshot=after
    ) == compute_consumed_schema_fingerprint(configuration=configuration, snapshot=before)


def test_the_shared_type_table_is_unchanged_by_concurrent_construction() -> None:
    snapshot = normalize_destination_schema(_SNAPSHOT)
    configuration = _configuration("table", ["name"])
    before = dict(ATTRIBUTE_TYPE_DOMAIN)

    _run_together(
        [
            (lambda: build_runtime_models(snapshot=snapshot, configuration=configuration, model_base=InfrahubModel))
            for _ in range(_WORKERS)
        ]
    )

    assert dict(ATTRIBUTE_TYPE_DOMAIN) == before


@pytest.mark.parametrize("attempt", range(3))
def test_concurrent_fingerprints_of_one_snapshot_agree(attempt: int) -> None:
    del attempt
    snapshot = normalize_destination_schema(_SNAPSHOT)
    configuration = _configuration("fingerprint", ["name", "description"])

    fingerprints = _run_together(
        [
            (lambda: compute_consumed_schema_fingerprint(configuration=configuration, snapshot=snapshot))
            for _ in range(_WORKERS)
        ]
    )

    assert len(set(fingerprints)) == 1


# --- AR3: binding is per adapter instance, never onto the shared adapter class ----------


class _SharedAdapter(InfrahubAdapter):
    """One adapter class with many instances — the shape binding must never mutate.

    A local subclass so a regression that bound onto the class could not leak into the
    bundled adapter and change another test's meaning.
    """


def test_concurrent_binding_keeps_each_adapter_instance_to_its_own_models() -> None:
    # A regression that bound onto `type(adapter)` would satisfy every per-instance
    # assertion elsewhere, because there is only one instance in those tests. Here the
    # instances share a class, so class-level binding makes the last writer win.
    snapshot = normalize_destination_schema(_SNAPSHOT)
    configuration = _configuration("concurrent-binding", ["name", "description"])
    plans = [
        build_runtime_models(snapshot=snapshot, configuration=configuration, model_base=InfrahubModel)
        for _ in range(_WORKERS)
    ]
    # Constructed without __init__: binding is the only behaviour under test, and the
    # adapter's own constructor would open a client.
    adapters = [object.__new__(_SharedAdapter) for _ in range(_WORKERS)]
    assert len({id(model["BuiltinTag"]) for model in plans}) == _WORKERS

    _run_together(
        [
            (lambda adapter=adapter, models=models: bind_runtime_models(adapter, models))
            for adapter, models in zip(adapters, plans, strict=True)
        ]
    )

    for adapter, models in zip(adapters, plans, strict=True):
        assert adapter.BuiltinTag is models["BuiltinTag"]
        assert "BuiltinTag" in vars(adapter)
    assert getattr(_SharedAdapter, "BuiltinTag", None) is None
    assert "BuiltinTag" not in vars(_SharedAdapter)
    assert getattr(InfrahubAdapter, "BuiltinTag", None) is None


def test_concurrent_binding_of_distinct_configurations_stays_per_instance() -> None:
    # The same property when the plans differ in shape as well as identity.
    snapshot = normalize_destination_schema(_SNAPSHOT)
    fields = [["name"], ["name", "description"], ["name", "colour"], ["name", "description", "colour"]]
    plans = [
        build_runtime_models(
            snapshot=snapshot,
            configuration=_configuration(f"configuration-{index}", fields[index % len(fields)]),
            model_base=InfrahubModel,
        )
        for index in range(_WORKERS)
    ]
    adapters = [object.__new__(_SharedAdapter) for _ in range(_WORKERS)]

    _run_together(
        [
            (lambda adapter=adapter, models=models: bind_runtime_models(adapter, models))
            for adapter, models in zip(adapters, plans, strict=True)
        ]
    )

    for index, (adapter, models) in enumerate(zip(adapters, plans, strict=True)):
        bound = adapter.BuiltinTag
        assert bound is models["BuiltinTag"]
        declared = set(bound.model_fields) - set(InfrahubModel.model_fields) - {"local_id", "local_data"}
        assert declared == set(fields[index % len(fields)])
    assert "BuiltinTag" not in vars(_SharedAdapter)
