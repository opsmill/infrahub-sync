"""SyncConfig.compute_order() returns the operator override if present,
otherwise falls back to the auto-tiered, flattened topological order.
"""

from __future__ import annotations

import logging

import pytest

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


@pytest.mark.parametrize(
    ("announce", "layout_level", "dropped_level"),
    [
        pytest.param(True, logging.INFO, logging.WARNING, id="announced"),
        pytest.param(False, logging.DEBUG, logging.DEBUG, id="recomputed-quietly"),
    ],
)
def test_compute_order_and_tiers_logs_the_layout_and_dropped_edges_at_the_announce_level(
    caplog: pytest.LogCaptureFixture,
    announce: bool,  # noqa: FBT001 - one parametrized dimension of the logged levels.
    layout_level: int,
    dropped_level: int,
) -> None:
    # RoutingAS.routing_device is optional, Device.asn is part of Device's identity: the
    # cycle is broken by dropping the optional edge.
    cfg = _cfg(
        order=[],
        mapping=[
            SchemaMappingModel(
                name="RoutingAS",
                identifiers=["asn"],
                fields=[SchemaMappingField(name="asn"), SchemaMappingField(name="routing_device", reference="Device")],
            ),
            SchemaMappingModel(
                name="Device",
                identifiers=["name", "asn"],
                fields=[SchemaMappingField(name="name"), SchemaMappingField(name="asn", reference="RoutingAS")],
            ),
        ],
    )
    caplog.set_level(logging.DEBUG, logger="infrahub_sync")

    cfg.compute_order_and_tiers(announce=announce)

    levels = {record.getMessage(): record.levelno for record in caplog.records if record.name == "infrahub_sync"}
    assert levels == {
        "tier 0 (1): ['RoutingAS']": layout_level,
        "tier 1 (1): ['Device']": layout_level,
        "dropped optional edges to break cycles: [('RoutingAS', 'Device')]": dropped_level,
    }


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
