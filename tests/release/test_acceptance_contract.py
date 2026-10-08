"""Contract grammar tests for the normative MVP acceptance document."""

from hashlib import sha256
from pathlib import Path

import pytest

from tasks.acceptance import ContractValidationError, parse_contract
from tests.release.acceptance_fixtures import contract_criterion, contract_document

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "docs" / "develop" / "knowledge" / "mvp-acceptance-contract.md"
)
EXPECTED_SCOPE_OWNERS = {"009", "010", "011", "012", "013", "014"}
EXPECTED_DEFERRALS = {
    "adapter-conformance",
    "governed-replay",
    "infrahub-branch-review",
    "scheduled-and-event-driven-execution",
    "incremental-extraction",
    "large-run-planning",
    "ownership-and-deletion",
    "parallel-execution",
    "production-operations",
    "operator-web-interface",
}


def _contract_text() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def _section(document: str, heading: str) -> str:
    start = document.index(f"## {heading}\n") + len(f"## {heading}\n")
    end = document.find("\n## ", start)
    return document[start:] if end == -1 else document[start:end]


def _bullet_keys(section: str) -> set[str]:
    return {line.split("**", 2)[1] for line in section.splitlines() if line.startswith("- **")}


def test_contract_version_criteria_and_exact_bytes_are_parsed() -> None:
    document = contract_document()

    contract = parse_contract(document)

    assert contract.version == 1
    assert contract.sha256 == sha256(document).hexdigest()
    assert contract.criteria[0].criterion_id == "MVP-009-001"


@pytest.mark.parametrize(
    "document",
    [
        contract_document().replace(b"**Contract version:** 1", b"**Contract version:** 0"),
        contract_document().replace(b"| ID | Owner spec |", b"| Identifier | Owner spec |"),
        contract_document().replace(b"MVP-009-001", b"MVP-009-001 | extra", 1),
    ],
    ids=("non-positive-version", "changed-header", "malformed-row"),
)
def test_contract_refuses_invalid_version_header_or_row(document: bytes) -> None:
    with pytest.raises(ContractValidationError):
        parse_contract(document)


def test_contract_refuses_duplicate_identifiers() -> None:
    criterion = contract_criterion()
    with pytest.raises(ContractValidationError, match="duplicate criterion identifier"):
        parse_contract(contract_document((criterion, criterion)))


@pytest.mark.parametrize(("field", "value"), [("owner_spec", "015"), ("class", "optional")])
def test_contract_refuses_unknown_owner_or_class(field: str, value: str) -> None:
    criterion = contract_criterion()
    criterion[field] = value
    with pytest.raises(ContractValidationError):
        parse_contract(contract_document((criterion,)))


@pytest.mark.parametrize("field", ["id", "owner_spec", "class", "requirement", "validation"])
def test_contract_refuses_empty_cells(field: str) -> None:
    criterion = contract_criterion()
    criterion[field] = ""
    with pytest.raises(ContractValidationError, match="empty cell"):
        parse_contract(contract_document((criterion,)))


def test_normative_contract_maps_every_mvp_spec_exactly_once() -> None:
    document = _contract_text()
    contract = parse_contract(document.encode())
    mapping = _section(document, "MVP scope mapping")

    assert {criterion.owner_spec for criterion in contract.criteria} == EXPECTED_SCOPE_OWNERS
    assert _bullet_keys(mapping) == EXPECTED_SCOPE_OWNERS


def test_normative_contract_lists_the_complete_larger_v3_deferral_set() -> None:
    deferrals = _section(_contract_text(), "Explicit post-MVP deferrals")

    assert _bullet_keys(deferrals) == EXPECTED_DEFERRALS
    assert "do not block MVP qualification" in deferrals


def test_required_criteria_and_deferrals_are_disjoint() -> None:
    document = _contract_text()
    required = _section(document, "Required MVP criteria")
    deferrals = _section(document, "Explicit post-MVP deferrals")

    for deferral in _bullet_keys(deferrals):
        assert f"**{deferral}**" not in required
    assert "uncertain writes require operator reconciliation and a new plan" in required
    assert "automatic replay" in deferrals


def test_standalone_write_requirements_are_explicitly_superseded() -> None:
    boundary = " ".join(_section(_contract_text(), "Service-first MVP boundary").split())

    assert "Sync HTTP API is the product boundary" in boundary
    assert "CLI uses the Python SDK" in boundary
    assert "offline CLI writes" in boundary
    assert "in-process writes" in boundary
    assert "superseded" in boundary
    assert "Direct Prefect execution remains read-only" in boundary
