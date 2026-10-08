"""Contract grammar tests for the normative MVP acceptance document."""

from hashlib import sha256

import pytest

from tasks.acceptance import ContractValidationError, parse_contract
from tests.release.acceptance_fixtures import contract_criterion, contract_document


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
