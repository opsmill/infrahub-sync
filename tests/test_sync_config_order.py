"""SyncConfig.compute_order() returns the operator override if present,
otherwise falls back to the auto-tiered, flattened topological order.
"""

from __future__ import annotations

from infrahub_sync import (
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)


def _cfg(order: list[str] | None, mapping: list[SchemaMappingModel]) -> SyncConfig:
    return SyncConfig(
        name="t",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        order=order or [],
        schema_mapping=mapping,
    )


def test_compute_order_uses_explicit_order_when_set() -> None:
    cfg = _cfg(
        order=["Device", "Tag"],
        mapping=[
            SchemaMappingModel(name="Tag", identifiers=["name"], fields=[SchemaMappingField(name="name")]),
            SchemaMappingModel(
                name="Device", identifiers=["name"], fields=[SchemaMappingField(name="tag", reference="Tag")]
            ),
        ],
    )
    assert cfg.compute_order() == ["Device", "Tag"]


def test_compute_order_falls_back_to_tiers_when_empty() -> None:
    cfg = _cfg(
        order=[],
        mapping=[
            SchemaMappingModel(name="Tag", identifiers=["name"], fields=[SchemaMappingField(name="name")]),
            SchemaMappingModel(
                name="Device", identifiers=["name"], fields=[SchemaMappingField(name="tag", reference="Tag")]
            ),
        ],
    )
    assert cfg.compute_order() == ["Tag", "Device"]
