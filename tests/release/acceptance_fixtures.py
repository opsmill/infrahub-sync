"""Deterministic builders for MVP acceptance contract and manifest tests.

Every builder returns fresh values. Tests can therefore introduce one deliberate violation without
sharing mutable state with another case.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, cast

CONTRACT_VERSION = 1
SOURCE_REVISION = "1" * 40
CANDIDATE_DIGEST = "sha256:" + "2" * 64
CANDIDATE_VERSION = "3.0.0a1"
CONTRACT_DIGEST = "3" * 64
EVIDENCE_DIGEST = "4" * 64
PRODUCED_AT = "2026-10-08T10:00:00+00:00"
APPROVED_AT = "2026-10-08T10:05:00+00:00"

CRITERION_TABLE_HEADER = "| ID | Owner spec | Class | Requirement | Validation | Evidence |\n|---|---|---|---|---|---|"


def contract_criterion(  # noqa: PLR0913 — one argument per criterion-table cell
    *,
    criterion_id: str = "MVP-009-001",
    owner_spec: str = "009",
    safety_class: str = "integrity",
    requirement: str = "The contract defines one deterministic criterion catalog.",
    validation: str = "Parse the normative criterion table.",
    evidence: Sequence[str] = ("contract-test",),
) -> dict[str, object]:
    """Build one criterion-table row as named cell values."""
    return {
        "id": criterion_id,
        "owner_spec": owner_spec,
        "class": safety_class,
        "requirement": requirement,
        "validation": validation,
        "evidence": list(evidence),
    }


def contract_document(
    criteria: Sequence[Mapping[str, object]] | None = None,
    *,
    version: int = CONTRACT_VERSION,
) -> bytes:
    """Build deterministic normative contract bytes with the exact table grammar."""
    rows = criteria if criteria is not None else (contract_criterion(),)
    rendered_rows = []
    for criterion in rows:
        evidence_values = cast("Sequence[object]", criterion["evidence"])
        evidence_types = ", ".join(str(item) for item in evidence_values)
        rendered_rows.append(
            f"| {criterion['id']} | {criterion['owner_spec']} | {criterion['class']} | "
            f"{criterion['requirement']} | {criterion['validation']} | {evidence_types} |"
        )
    body = [
        "# MVP acceptance contract",
        "",
        f"**Contract version:** {version}",
        "",
        "## Required MVP criteria",
        "",
        CRITERION_TABLE_HEADER,
        *rendered_rows,
        "",
        "## Service-first MVP boundary",
        "",
        "Deterministic fixture boundary.",
        "",
        "## MVP scope mapping",
        "",
        "Deterministic fixture scope.",
        "",
        "## Explicit post-MVP deferrals",
        "",
        "Deterministic fixture deferrals.",
        "",
    ]
    return "\n".join(body).encode()


def candidate_identity(
    *,
    version: str = CANDIDATE_VERSION,
    source_revision: str = SOURCE_REVISION,
    image_digest: str = CANDIDATE_DIGEST,
) -> dict[str, object]:
    """Build one immutable synthetic candidate identity."""
    return {"version": version, "source_revision": source_revision, "image_digest": image_digest}


def evaluator_approval(
    *,
    identity: str = "independent-evaluator@example.test",
    independent: bool = True,
    executed_journey: bool = True,
    approved: bool = True,
    approved_at: str = APPROVED_AT,
) -> dict[str, object]:
    """Build one deterministic independent-evaluator attestation."""
    return {
        "identity": identity,
        "independent_from_implementation": independent,
        "executed_qualified_journey": executed_journey,
        "approved": approved,
        "approved_at": approved_at,
    }


def criterion_result(
    *,
    criterion_id: str = "MVP-009-001",
    result: str = "pass",
    evidence_ids: Sequence[str] = ("contract-test",),
    note: str | None = None,
) -> dict[str, object]:
    """Build one criterion result without imposing runtime-model validation."""
    criterion: dict[str, object] = {
        "criterion_id": criterion_id,
        "result": result,
        "evidence": list(evidence_ids),
    }
    if note is not None:
        criterion["note"] = note
    return criterion


def evidence_reference(  # noqa: PLR0913 — one argument per evidence-reference field
    *,
    evidence_type: str = "contract-test",
    locator_kind: str = "relative-path",
    locator_value: str = "evidence/contract-test.json",
    digest: str = EVIDENCE_DIGEST,
    producer: str = "acceptance-test-suite",
    produced_at: str = PRODUCED_AT,
    source_revision: str = SOURCE_REVISION,
    candidate_digest: str = CANDIDATE_DIGEST,
    contract_sha256: str = CONTRACT_DIGEST,
) -> dict[str, object]:
    """Build bounded evidence metadata without an evidence body."""
    return {
        "evidence_type": evidence_type,
        "media_type": "application/json",
        "locator": {"kind": locator_kind, "value": locator_value},
        "sha256": digest,
        "producer": producer,
        "produced_at": produced_at,
        "source_revision": source_revision,
        "candidate_digest": candidate_digest,
        "contract_sha256": contract_sha256,
    }


def qualification_manifest(
    *,
    contract: bytes | None = None,
    criteria: Sequence[Mapping[str, Any]] | None = None,
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, object]:
    """Build a complete deterministic manifest for later semantic-validation tests."""
    contract_bytes = contract if contract is not None else contract_document()
    contract_digest = sha256(contract_bytes).hexdigest()
    evidence_catalog = (
        {"contract-test": evidence_reference(contract_sha256=contract_digest)}
        if evidence is None
        else {name: dict(reference) for name, reference in evidence.items()}
    )
    return {
        "schema_version": 1,
        "contract": {"version": CONTRACT_VERSION, "sha256": contract_digest},
        "candidate": candidate_identity(),
        "evidence": evidence_catalog,
        "qualification_record": "contract-test",
        "criteria": [dict(item) for item in ((criterion_result(),) if criteria is None else criteria)],
        "evaluator": evaluator_approval(),
        "decision": "pass",
        "decided_at": APPROVED_AT,
    }
