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


def test_the_shipped_netbox_example_headers_claim_no_parallel_execution_mode() -> None:
    """The two shipped example headers must not promise an execution mode that is gone.

    `config.yml` and `package.yml` under `examples/netbox_to_infrahub/` carry the same
    header, and it once said tiers group "kinds that can be written in parallel" and that
    "tiered parallel execution is the service execution policy". Both became false when
    the tier runner was deleted: tiers order a plan's operations, and an apply executes
    the reviewed sequence. Two copies of one paragraph drift, so this pins both.
    """
    from pathlib import Path

    examples = Path(__file__).resolve().parent.parent / "examples" / "netbox_to_infrahub"
    stale = ("written in parallel", "parallel execution is the", "tiered parallel")

    for name in ("config.yml", "package.yml"):
        header = (examples / name).read_text(encoding="utf-8")
        lowered = header.lower()
        for phrase in stale:
            assert phrase not in lowered, f"{name} still claims {phrase!r}"
        # And it still tells the reader what does order the writes.
        assert "write-order tiers" in header, name
        assert "reviewed operation sequence" in header, name
