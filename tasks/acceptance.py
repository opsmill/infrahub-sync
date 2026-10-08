"""Safe, read-only boundaries shared by MVP acceptance validation tasks."""

# Refusal text is curated at each validation branch and is intentionally constructed where the
# relevant field and next action are known.
# ruff: noqa: EM101, TRY003

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

if TYPE_CHECKING:
    from collections.abc import Mapping

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


CRITERION_TABLE_HEADER = "| ID | Owner spec | Class | Requirement | Validation | Evidence |"
CRITERION_TABLE_SEPARATOR = "|---|---|---|---|---|---|"
VERSION_PATTERN = re.compile(r"\*\*Contract version:\*\* ([1-9][0-9]*)")
VERSION_LIKE_PATTERN = re.compile(r"^\s*\**contract\s+version\**\s*:", re.IGNORECASE)
CRITERION_ID_PATTERN = re.compile(r"[A-Z][A-Z0-9-]*-[0-9]{3}")
EVIDENCE_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9-]*")
CATALOG_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
CANDIDATE_VERSION_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9._+-]{0,126}[A-Za-z0-9])?$"
PRINTABLE_LINE_PATTERN = r"^[\x20-\x7e]+$"
RELATIVE_PATH_SCHEMA_PATTERN = (
    r"^(?!/)(?!.*(?:^|/)\.{1,2}(?:/|$))(?!.*//)(?!.*[\\:?#@])"
    r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$"
)
UTC_TIMESTAMP_SCHEMA_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]00:00)$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
OCI_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
REVISION_PATTERN = r"^[0-9a-f]{40}$"
CRITERION_RESULT_ID_PATTERN = r"^[A-Z][A-Z0-9-]*-[0-9]{3}$"
ALLOWED_OWNERS = frozenset({"009", "010", "011", "012", "013", "014"})
ALLOWED_CLASSES = frozenset({"safety", "integrity", "release"})
CRITERION_CELL_COUNT = 6
VERSION_LINE_INDEX = 2
MINIMUM_CONTRACT_LINES = 3


@dataclass(frozen=True)
class MvpCriterion:
    """One parsed row from the normative criterion table."""

    criterion_id: str
    owner_spec: str
    safety_class: str
    requirement: str
    validation_method: str
    evidence_types: tuple[str, ...]


@dataclass(frozen=True)
class AcceptanceContract:
    """The selected normative contract and its exact byte identity."""

    version: int
    sha256: str
    criteria: tuple[MvpCriterion, ...]


def _contract_error(problem: str, *, field: str | None = None) -> ContractValidationError:
    return ContractValidationError(
        problem,
        field=field,
        next_action="Correct the normative contract structure and increment its version when semantics change",
    )


def _parse_criterion_row(line: str, seen: set[str]) -> MvpCriterion:
    """Parse one exact six-cell row from the normative table."""
    if not line.startswith("| ") or not line.endswith(" |"):
        raise _contract_error("malformed criterion row", field="criteria")
    cells = line[2:-2].split(" | ")
    if len(cells) != CRITERION_CELL_COUNT:
        raise _contract_error("malformed criterion row", field="criteria")
    if any(not cell for cell in cells):
        raise _contract_error("criterion row contains an empty cell", field="criteria")
    if any("<" in cell or ">" in cell for cell in cells):
        raise _contract_error("criterion row contains HTML", field="criteria")
    criterion_id, owner, safety_class, requirement, validation, evidence_cell = cells
    if not CRITERION_ID_PATTERN.fullmatch(criterion_id):
        raise _contract_error("criterion identifier is invalid", field="criteria")
    if criterion_id in seen:
        raise _contract_error("duplicate criterion identifier", field=criterion_id)
    if owner not in ALLOWED_OWNERS:
        raise _contract_error("criterion owner is unknown", field=criterion_id)
    if safety_class not in ALLOWED_CLASSES:
        raise _contract_error("criterion class is unknown", field=criterion_id)
    evidence_types = evidence_cell.split(", ")
    if ", ".join(evidence_types) != evidence_cell or any(
        not EVIDENCE_TYPE_PATTERN.fullmatch(item) for item in evidence_types
    ):
        raise _contract_error("criterion evidence types are invalid", field=criterion_id)
    if len(set(evidence_types)) != len(evidence_types):
        raise _contract_error("criterion evidence types are duplicated", field=criterion_id)
    return MvpCriterion(
        criterion_id=criterion_id,
        owner_spec=owner,
        safety_class=safety_class,
        requirement=requirement,
        validation_method=validation,
        evidence_types=tuple(evidence_types),
    )


def parse_contract(content: bytes) -> AcceptanceContract:
    """Parse exact contract bytes, refusing every ambiguous criterion catalog."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise _contract_error("contract is not valid UTF-8") from None
    lines = text.splitlines()
    if not lines or lines[0] != "# MVP acceptance contract":
        raise _contract_error("contract title does not match the contract grammar")
    version_like = [(index, line) for index, line in enumerate(lines) if VERSION_LIKE_PATTERN.search(line)]
    if len(version_like) != 1:
        raise _contract_error("expected exactly one positive contract version", field="contract_version")
    version_index, version_line = version_like[0]
    version_match = VERSION_PATTERN.fullmatch(version_line)
    if version_index != VERSION_LINE_INDEX or len(lines) < MINIMUM_CONTRACT_LINES or lines[1] or version_match is None:
        raise _contract_error("contract version must immediately follow the H1 title", field="contract_version")

    headings = [index for index, line in enumerate(lines) if line == "## Required MVP criteria"]
    if len(headings) != 1:
        raise _contract_error("expected exactly one required criterion section", field="criteria")
    cursor = headings[0] + 1
    while cursor < len(lines) and not lines[cursor]:
        cursor += 1
    if (
        cursor + 1 >= len(lines)
        or lines[cursor] != CRITERION_TABLE_HEADER
        or lines[cursor + 1] != CRITERION_TABLE_SEPARATOR
    ):
        raise _contract_error("criterion table header does not match the contract grammar", field="criteria")
    cursor += 2

    criteria: list[MvpCriterion] = []
    seen: set[str] = set()
    while cursor < len(lines) and not lines[cursor].startswith("## "):
        line = lines[cursor]
        cursor += 1
        if not line:
            continue
        criterion = _parse_criterion_row(line, seen)
        criteria.append(criterion)
        seen.add(criterion.criterion_id)
    if not criteria:
        raise _contract_error("criterion table is empty", field="criteria")
    return AcceptanceContract(
        version=int(version_match.group(1)), sha256=sha256(content).hexdigest(), criteria=tuple(criteria)
    )


class StrictModel(BaseModel):
    """Strict immutable base for untrusted qualification input."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=False)


class ContractIdentity(StrictModel):
    """Exact normative-contract identity recorded by a manifest."""

    version: Annotated[int, Field(ge=1)]
    sha256: Annotated[str, Field(pattern=SHA256_PATTERN)]


class CandidateIdentity(StrictModel):
    """Immutable source and image identity for one candidate."""

    version: Annotated[str, Field(pattern=CANDIDATE_VERSION_PATTERN, max_length=128)]
    source_revision: Annotated[str, Field(pattern=REVISION_PATTERN)]
    image_digest: Annotated[str, Field(pattern=OCI_DIGEST_PATTERN)]


class RelativePathLocator(StrictModel):
    """A bundle-relative POSIX evidence path."""

    kind: Literal["relative-path"]
    value: Annotated[
        str,
        Field(
            min_length=1,
            max_length=1024,
            pattern=r"^[A-Za-z0-9._/-]+$",
        ),
    ]

    @field_validator("value")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        """Refuse absolute, traversing, or non-normalized paths."""
        segments = value.split("/")
        if value.startswith("/") or any(segment in {"", ".", ".."} for segment in segments):
            raise ValueError("relative evidence path must be normalized")
        return value


class ArtifactIdLocator(StrictModel):
    """A bounded opaque artifact identifier."""

    kind: Literal["artifact-id"]
    value: Annotated[str, Field(pattern=CATALOG_ID_PATTERN)]


EvidenceLocator = Annotated[RelativePathLocator | ArtifactIdLocator, Field(discriminator="kind")]


class EvidenceReference(StrictModel):
    """Bounded immutable metadata for retained evidence bytes."""

    evidence_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=128)]
    media_type: Annotated[str, Field(min_length=1, max_length=128)]
    locator: EvidenceLocator
    sha256: Annotated[str, Field(pattern=SHA256_PATTERN)]
    producer: Annotated[str, Field(min_length=1, max_length=256)]
    produced_at: Annotated[AwareDatetime, Field(strict=False)]
    source_revision: Annotated[str, Field(pattern=REVISION_PATTERN)]
    candidate_digest: Annotated[str, Field(pattern=OCI_DIGEST_PATTERN)]
    contract_sha256: Annotated[str, Field(pattern=SHA256_PATTERN)]

    @field_validator("produced_at")
    @classmethod
    def require_utc_production_time(cls, value: datetime) -> datetime:
        """Require an explicit zero-offset evidence timestamp."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("evidence timestamp must use UTC")
        return value


class EvaluatorApproval(StrictModel):
    """Independent evaluator attestation recorded with the decision."""

    identity: Annotated[str, Field(pattern=PRINTABLE_LINE_PATTERN, min_length=1, max_length=256)]
    independent_from_implementation: bool
    executed_qualified_journey: bool
    approved: bool
    approved_at: Annotated[AwareDatetime, Field(strict=False)]

    @field_validator("approved_at")
    @classmethod
    def require_utc_approval_time(cls, value: datetime) -> datetime:
        """Require an explicit zero-offset evaluator timestamp."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("approval timestamp must use UTC")
        return value


class CriterionResult(StrictModel):
    """One pass/fail result for one selected-contract criterion."""

    criterion_id: Annotated[str, Field(pattern=CRITERION_RESULT_ID_PATTERN)]
    result: Literal["pass", "fail"]
    evidence: list[Annotated[str, Field(pattern=CATALOG_ID_PATTERN)]]
    note: Annotated[str, Field(min_length=1, max_length=1000)] | None = None

    @model_validator(mode="after")
    def validate_result_details(self) -> CriterionResult:
        """Require evidence for passes and notes for failures."""
        if self.result == "pass" and not self.evidence:
            raise ValueError("passing criterion requires evidence")
        if self.result == "fail" and self.note is None:
            raise ValueError("failed criterion requires a note")
        return self


class QualificationManifest(StrictModel):
    """Strict machine-readable consistency decision for one candidate and contract."""

    schema_version: Literal[1]
    contract: ContractIdentity
    candidate: CandidateIdentity
    evidence: dict[Annotated[str, Field(pattern=CATALOG_ID_PATTERN)], EvidenceReference]
    qualification_record: Annotated[str, Field(pattern=CATALOG_ID_PATTERN)]
    criteria: list[CriterionResult]
    evaluator: EvaluatorApproval
    decision: Literal["pass", "fail"]
    decided_at: Annotated[AwareDatetime, Field(strict=False)]

    @field_validator("decided_at")
    @classmethod
    def require_utc_decision_time(cls, value: datetime) -> datetime:
        """Require an explicit zero-offset decision timestamp."""
        if value.utcoffset() != timedelta(0):
            raise ValueError("decision timestamp must use UTC")
        return value


@dataclass(frozen=True)
class ValidatedCriterionResult:
    """Immutable criterion result exposed by validated manifests."""

    criterion_id: str
    result: Literal["pass", "fail"]
    evidence: tuple[str, ...]
    note: str | None


@dataclass(frozen=True)
class ValidatedQualificationManifest:
    """Deeply immutable manifest returned only after all consistency checks pass."""

    schema_version: int
    contract: ContractIdentity
    candidate: CandidateIdentity
    evidence: Mapping[str, EvidenceReference]
    qualification_record: str
    criteria: tuple[ValidatedCriterionResult, ...]
    evaluator: EvaluatorApproval
    decision: Literal["pass", "fail"]
    decided_at: datetime


def qualification_manifest_schema() -> dict[str, Any]:
    """Return the deterministic JSON Schema generated from runtime models."""
    schema = QualificationManifest.model_json_schema()
    definitions = schema["$defs"]
    relative_path = definitions["RelativePathLocator"]["properties"]["value"]
    relative_path["pattern"] = RELATIVE_PATH_SCHEMA_PATTERN
    criterion = definitions["CriterionResult"]
    criterion["properties"]["evidence"]["uniqueItems"] = True
    criterion["allOf"] = [
        {
            "if": {"properties": {"result": {"const": "pass"}}, "required": ["result"]},
            "then": {"properties": {"evidence": {"minItems": 1}}},
        },
        {
            "if": {"properties": {"result": {"const": "fail"}}, "required": ["result"]},
            "then": {"required": ["note"]},
        },
    ]
    evidence_map = schema["properties"]["evidence"]
    evidence_map["propertyNames"] = {"pattern": CATALOG_ID_PATTERN}
    for model_name, field_name in (
        ("EvidenceReference", "produced_at"),
        ("EvaluatorApproval", "approved_at"),
    ):
        definitions[model_name]["properties"][field_name]["pattern"] = UTC_TIMESTAMP_SCHEMA_PATTERN
    schema["properties"]["decided_at"]["pattern"] = UTC_TIMESTAMP_SCHEMA_PATTERN
    return schema


def write_qualification_manifest_schema(path: Path = ACCEPTANCE_SCHEMA_PATH) -> Path:
    """Regenerate the committed JSON Schema from the runtime manifest model."""
    rendered = json.dumps(qualification_manifest_schema(), indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return path


def _manifest_error(problem: str, *, field: str | None = None, next_action: str) -> ManifestValidationError:
    return ManifestValidationError(problem, field=field, next_action=next_action)


def _validate_evidence_catalog(manifest: QualificationManifest, contract: AcceptanceContract) -> None:
    """Resolve normalized evidence metadata and enforce its cross-entity invariants."""
    criteria_by_id = {criterion.criterion_id: criterion for criterion in contract.criteria}
    referenced_evidence = {manifest.qualification_record}
    if manifest.qualification_record not in manifest.evidence:
        raise _manifest_error(
            "qualification record does not resolve in the evidence catalog",
            field="qualification_record",
            next_action="Rebuild the normalized evidence catalog and references",
        )
    if manifest.evidence[manifest.qualification_record].evidence_type != "qualification-record":
        raise _manifest_error(
            "qualification record has the wrong evidence type",
            field="qualification_record",
            next_action="Reference catalog evidence declared as qualification-record",
        )
    for result in manifest.criteria:
        if len(result.evidence) != len(set(result.evidence)):
            raise _manifest_error(
                "criterion evidence identifiers are not unique",
                field=result.criterion_id,
                next_action="Rebuild the normalized evidence catalog and references",
            )
        accepted_types = criteria_by_id[result.criterion_id].evidence_types
        for evidence_id in result.evidence:
            reference = manifest.evidence.get(evidence_id)
            if reference is None:
                raise _manifest_error(
                    "evidence reference does not resolve in the catalog",
                    field=result.criterion_id,
                    next_action="Rebuild the normalized evidence catalog and references",
                )
            referenced_evidence.add(evidence_id)
            if reference.evidence_type not in accepted_types:
                raise _manifest_error(
                    "referenced evidence type is not accepted by the criterion",
                    field=result.criterion_id,
                    next_action="Attach evidence of a type declared by that criterion",
                )
    if set(manifest.evidence) != referenced_evidence:
        raise _manifest_error(
            "evidence catalog contains a dangling entry",
            field="evidence",
            next_action="Rebuild the normalized evidence catalog and references",
        )
    for reference in manifest.evidence.values():
        if (
            reference.source_revision != manifest.candidate.source_revision
            or reference.candidate_digest != manifest.candidate.image_digest
            or reference.contract_sha256 != manifest.contract.sha256
        ):
            raise _manifest_error(
                "evidence identity does not match the candidate and contract",
                field="evidence",
                next_action="Regenerate evidence for the named candidate",
            )


def validate_manifest(document: object, contract_content: bytes) -> ValidatedQualificationManifest:
    """Return a deeply immutable wrapper after complete consistency validation."""
    contract = parse_contract(contract_content)
    try:
        manifest = QualificationManifest.model_validate(document)
    except ValidationError:
        raise _manifest_error(
            "manifest structure is invalid",
            next_action="Regenerate the manifest from the documented template",
        ) from None
    if manifest.contract.version != contract.version or manifest.contract.sha256 != contract.sha256:
        raise _manifest_error(
            "contract identity does not match the selected bytes",
            field="contract",
            next_action="Select the contract used for qualification or requalify against the intended contract",
        )
    expected = {criterion.criterion_id for criterion in contract.criteria}
    actual = [criterion.criterion_id for criterion in manifest.criteria]
    if len(actual) != len(set(actual)):
        raise _manifest_error(
            "duplicate criterion result",
            field="criteria",
            next_action="Rebuild the manifest from the current criterion catalog",
        )
    if set(actual) != expected:
        raise _manifest_error(
            "criterion accounting does not match the selected contract",
            field="criteria",
            next_action="Rebuild the manifest from the current criterion catalog",
        )
    _validate_evidence_catalog(manifest, contract)
    evaluator_passed = (
        manifest.evaluator.independent_from_implementation
        and manifest.evaluator.executed_qualified_journey
        and manifest.evaluator.approved
    )
    if manifest.decision == "pass" and not evaluator_passed:
        raise _manifest_error(
            "evaluator approval invariants are not satisfied",
            field="evaluator",
            next_action="Have a non-implementing evaluator execute and approve the journey",
        )
    derived = "pass" if all(item.result == "pass" for item in manifest.criteria) and evaluator_passed else "fail"
    if manifest.decision != derived:
        raise _manifest_error(
            "overall decision is inconsistent with criterion and evaluator results",
            field="decision",
            next_action="Correct the decision to match criterion and evaluator results",
        )
    produced = [item.produced_at for item in manifest.evidence.values()]
    if produced and manifest.evaluator.approved_at < max(produced):
        raise _manifest_error(
            "evaluator approval timestamp precedes evidence production",
            field="evaluator.approved_at",
            next_action="Approve the completed evidence set after it has been produced",
        )
    if manifest.decided_at < manifest.evaluator.approved_at:
        raise _manifest_error(
            "decision timestamp precedes evaluator approval",
            field="decided_at",
            next_action="Record the decision after evaluator approval",
        )
    return ValidatedQualificationManifest(
        schema_version=manifest.schema_version,
        contract=manifest.contract,
        candidate=manifest.candidate,
        evidence=MappingProxyType(dict(manifest.evidence)),
        qualification_record=manifest.qualification_record,
        criteria=tuple(
            ValidatedCriterionResult(
                criterion_id=item.criterion_id,
                result=item.result,
                evidence=tuple(item.evidence),
                note=item.note,
            )
            for item in manifest.criteria
        ),
        evaluator=manifest.evaluator,
        decision=manifest.decision,
        decided_at=manifest.decided_at,
    )


def _read_contract(path: Path) -> bytes:
    """Read selected contract bytes with safe, actionable refusal categories."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise ContractValidationError(
            "selected contract path does not exist",
            next_action="Select an existing regular contract file",
        ) from None
    except IsADirectoryError:
        raise ContractValidationError(
            "selected contract path is a directory",
            next_action="Select a regular contract file rather than a directory",
        ) from None
    except PermissionError:
        raise ContractValidationError(
            "permission denied while reading selected contract",
            next_action="Grant read permission to the selected contract file",
        ) from None
    except OSError:
        raise ContractValidationError(
            "selected contract could not be read because of an I/O error",
            next_action="Check the contract file and storage, then retry",
        ) from None


def _read_manifest(path: Path) -> object:
    """Read manifest JSON with safe, actionable refusal categories."""
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        raise _manifest_error(
            "manifest path does not exist",
            next_action="Select an existing regular manifest file",
        ) from None
    except IsADirectoryError:
        raise _manifest_error(
            "manifest path is a directory",
            next_action="Select a regular manifest file rather than a directory",
        ) from None
    except PermissionError:
        raise _manifest_error(
            "permission denied while reading manifest",
            next_action="Grant read permission to the manifest file",
        ) from None
    except OSError:
        raise _manifest_error(
            "manifest could not be read because of an I/O error",
            next_action="Check the manifest file and storage, then retry",
        ) from None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise _manifest_error(
            "manifest is not valid UTF-8",
            next_action="Regenerate the manifest as UTF-8 JSON",
        ) from None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise _manifest_error(
            "manifest contains malformed JSON",
            next_action="Regenerate the manifest from the documented template",
        ) from None


def validate_acceptance(
    manifest_path: Path,
    *,
    contract_path: Path | None = None,
) -> AcceptanceValidationResult:
    """Read and validate one manifest against selected contract bytes."""
    selected = contract_path if contract_path is not None else DEFAULT_CONTRACT_PATH
    selection = ContractSelection(
        path=selected,
        mode=ContractSelectionMode.RETAINED if contract_path is not None else ContractSelectionMode.CHECKED_OUT,
    )
    contract_content = _read_contract(selected)
    contract = parse_contract(contract_content)
    loaded = _read_manifest(manifest_path)
    if not isinstance(loaded, dict):
        raise _manifest_error(
            "manifest root is not an object",
            next_action="Regenerate the manifest from the documented template",
        )
    manifest = validate_manifest(loaded, contract_content)
    return AcceptanceValidationResult(
        contract_version=contract.version,
        contract_sha256=contract.sha256,
        candidate_version=manifest.candidate.version,
        source_revision=manifest.candidate.source_revision,
        image_digest=manifest.candidate.image_digest,
        passed_criteria=sum(item.result == "pass" for item in manifest.criteria),
        total_criteria=len(manifest.criteria),
        evaluator_identity=manifest.evaluator.identity,
        evaluator_approved_at=manifest.evaluator.approved_at.isoformat(),
        decision=manifest.decision,
        selection=selection,
    )
