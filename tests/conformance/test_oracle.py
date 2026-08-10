"""Mutation-sensitive checks for the canonical envelope oracle."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from tests.conformance.oracle import CanonicalEnvelope, Surface, assert_equivalent


def _envelope(surface: Surface) -> CanonicalEnvelope:
    return CanonicalEnvelope(
        surface=surface,
        operation="plan",
        plan_fingerprint="a" * 64,
        counts={"create": 1, "update": 0, "delete": 0},
        outcome="planned",
        destination_effects={"created": 0, "updated": 0, "deleted": 0},
        product_record={
            "run_id": f"{surface}-run",
            "operation": "plan",
            "configuration_reference": "configuration-v1",
            "actor": None,
            "audit_links": [],
            "started_at": "2026-08-10T12:00:00+00:00",
            "finished_at": "2026-08-10T12:00:01+00:00",
            "phase": "planned",
            "outcome": "planned",
            "summary": {"create": 1, "update": 0, "delete": 0},
            "results": {"operation": "plan"},
            "artifact_refs": [],
            "prefect_executions": [],
        },
        result={"run_id": f"{surface}-run", "operation": "plan", "outcome": "planned"},
        artifact_references=[],
        artifact_semantics={"checksum": "a" * 64, "operations": [{"action": "create"}]},
    )


def test_oracle_normalizes_only_generated_ids_and_timestamps() -> None:
    assert_equivalent([_envelope("cli"), _envelope("python"), _envelope("managed")])


def test_oracle_refuses_to_hide_a_named_product_field_disagreement() -> None:
    cli = _envelope("cli")
    managed_data = dict(deepcopy(_envelope("managed").product_record))
    managed_data["configuration_reference"] = "different-configuration"
    managed = replace(_envelope("managed"), product_record=managed_data)

    with pytest.raises(AssertionError, match="canonical interface disagreement"):
        assert_equivalent([cli, managed])
