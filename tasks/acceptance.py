"""Safe, read-only boundaries shared by MVP acceptance validation tasks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT_PATH = REPO_ROOT / "docs" / "docs" / "develop" / "knowledge" / "mvp-acceptance-contract.md"
ACCEPTANCE_SCHEMA_PATH = (
    REPO_ROOT / "docs" / "static" / "schemas" / "infrahub-sync-mvp-qualification-manifest-v1.schema.json"
)


@dataclass(frozen=True)
class AcceptancePaths:
    """Repository-owned paths used by the acceptance validator."""

    repository: Path = REPO_ROOT
    contract: Path = DEFAULT_CONTRACT_PATH
    schema: Path = ACCEPTANCE_SCHEMA_PATH


DEFAULT_PATHS = AcceptancePaths()


class ContractSelectionMode(str, Enum):
    """How the caller selected the contract bytes under validation."""

    CHECKED_OUT = "checked-out"
    RETAINED = "retained"


@dataclass(frozen=True)
class ContractSelection:
    """The contract path and the safe label used to describe its selection."""

    path: Path
    mode: ContractSelectionMode


class AcceptanceTaskError(RuntimeError):
    """Base class for an acceptance refusal safe to display to an operator.

    Callers must supply curated text rather than untrusted manifest values. The validator may name
    fields and criterion identifiers, but it must never pass notes, locators, or evidence bodies to
    this boundary.
    """

    def __init__(self, problem: str, *, field: str | None = None, next_action: str) -> None:
        self.problem = problem
        self.field = field
        self.next_action = next_action
        subject = f" for {field}" if field is not None else ""
        super().__init__(f"acceptance validation failed{subject}: {problem}. Next action: {next_action}")


class ContractValidationError(AcceptanceTaskError):
    """Raised when the selected normative contract is unreadable or malformed."""


class ManifestValidationError(AcceptanceTaskError):
    """Raised when a qualification manifest is unreadable or inconsistent."""


@dataclass(frozen=True)
class AcceptanceValidationResult:
    """Bounded metadata returned after a manifest is consistent with selected contract bytes."""

    contract_version: int
    contract_sha256: str
    candidate_version: str
    source_revision: str
    image_digest: str
    passed_criteria: int
    total_criteria: int
    evaluator_identity: str
    evaluator_approved_at: str
    decision: str
    selection: ContractSelection
    consistent: bool = True
