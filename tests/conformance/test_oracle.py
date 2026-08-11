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
            "artifact_refs": [
                {
                    "artifact_id": "plan-review",
                    "run_id": f"{surface}-run",
                    "created_at": "2026-08-10T12:00:00+00:00",
                }
            ],
            "prefect_executions": [],
        },
        result={"run_id": f"{surface}-run", "operation": "plan", "outcome": "planned"},
        artifact_references=[
            {
                "artifact_id": "plan-review",
                "run_id": f"{surface}-run",
                "created_at": "2026-08-10T12:00:00+00:00",
            }
        ],
        artifact_semantics={
            "run_id": f"{surface}-run",
            "checksum": "a" * 64,
            "operations": [{"action": "create"}],
        },
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


@pytest.mark.parametrize(
    ("container", "nested_field"),
    [("product_record", "run_id"), ("result", "created_at"), ("artifact_semantics", "run_id")],
)
def test_oracle_does_not_normalize_semantic_payload_keys(container: str, nested_field: str) -> None:
    cli = _envelope("cli")
    managed = _envelope("managed")
    cli_data = dict(deepcopy(getattr(cli, container)))
    managed_data = dict(deepcopy(getattr(managed, container)))
    cli_data["payload"] = {nested_field: "semantic-a"}
    managed_data["payload"] = {nested_field: "semantic-b"}

    with pytest.raises(AssertionError, match="canonical interface disagreement"):
        assert_equivalent(
            [
                replace(cli, **{container: cli_data}),
                replace(managed, **{container: managed_data}),
            ]
        )


@pytest.mark.parametrize("owner", ["product-record", "result", "artifact"])
def test_oracle_refuses_intra_envelope_run_ownership_disagreement(owner: str) -> None:
    envelope = _envelope("cli")
    if owner == "product-record":
        product_record = dict(deepcopy(envelope.product_record))
        product_record["run_id"] = "wrong-owner"
        mutated = replace(envelope, product_record=product_record)
    elif owner == "result":
        result = dict(envelope.result)
        result["run_id"] = "wrong-owner"
        mutated = replace(envelope, result=result)
    else:
        artifact_semantics = dict(deepcopy(envelope.artifact_semantics))
        artifact_semantics["run_id"] = "wrong-owner"
        mutated = replace(envelope, artifact_semantics=artifact_semantics)

    with pytest.raises(AssertionError, match="inconsistent run identity aliases"):
        mutated.normalized()
