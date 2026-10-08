"""Strict qualification-manifest and decision tests."""

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator

from tasks.acceptance import (
    ACCEPTANCE_SCHEMA_PATH,
    DEFAULT_CONTRACT_PATH,
    ManifestValidationError,
    qualification_manifest_schema,
    validate_manifest,
)
from tests.release.acceptance_fixtures import (
    CANDIDATE_DIGEST,
    SOURCE_REVISION,
    contract_criterion,
    contract_document,
    criterion_result,
    evidence_reference,
    qualification_manifest,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "acceptance"
DESIGN_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "009-mvp-acceptance-contract"
    / "contracts"
    / "qualification-manifest.schema.json"
)


def test_manifest_models_are_strict() -> None:
    manifest = qualification_manifest()
    manifest["unexpected"] = True
    with pytest.raises(ManifestValidationError, match="manifest structure"):
        validate_manifest(manifest, contract_document())


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("contract", "sha256"), "0" * 64),
        (("contract", "version"), 2),
        (("candidate", "source_revision"), "short"),
        (("candidate", "image_digest"), "2" * 64),
    ],
    ids=("contract-digest", "contract-version", "source-revision", "image-digest"),
)
def test_manifest_refuses_invalid_or_mismatched_identities(path: tuple[str, str], value: object) -> None:
    manifest = qualification_manifest()
    nested = cast("dict[str, object]", manifest[path[0]])
    nested[path[1]] = value
    with pytest.raises(ManifestValidationError):
        validate_manifest(manifest, contract_document())


@pytest.mark.parametrize(
    "criteria",
    [
        [],
        [criterion_result(), criterion_result()],
        [criterion_result(criterion_id="MVP-999-001")],
    ],
    ids=("missing", "duplicate", "unknown"),
)
def test_manifest_requires_exact_criterion_accounting(criteria: list[dict[str, object]]) -> None:
    with pytest.raises(ManifestValidationError, match="criterion"):
        validate_manifest(qualification_manifest(criteria=criteria), contract_document())


def test_manifest_has_no_waiver_result() -> None:
    manifest = qualification_manifest(criteria=[criterion_result(result="waived")])
    with pytest.raises(ManifestValidationError, match="manifest structure"):
        validate_manifest(manifest, contract_document())


def test_manifest_decision_must_match_criterion_results() -> None:
    manifest = qualification_manifest(criteria=[criterion_result(result="fail", evidence_ids=(), note="failed")])
    with pytest.raises(ManifestValidationError, match="decision"):
        validate_manifest(manifest, contract_document())


def test_committed_schema_is_byte_equivalent_to_runtime_generation() -> None:
    generated = json.dumps(qualification_manifest_schema(), indent=2, sort_keys=True) + "\n"

    assert ACCEPTANCE_SCHEMA_PATH.read_text(encoding="utf-8") == generated
    assert DESIGN_SCHEMA_PATH.read_text(encoding="utf-8") == generated


@pytest.mark.parametrize("fixture", sorted((FIXTURE_ROOT / "valid").glob("*.json")), ids=lambda path: path.stem)
def test_valid_manifest_corpus_passes_published_json_schema(fixture: Path) -> None:
    document = json.loads(fixture.read_text(encoding="utf-8"))
    schema = json.loads(ACCEPTANCE_SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(document)


@pytest.mark.parametrize("name", ["duplicate-evidence-id", "unsafe-locator"])
def test_schema_expressible_invalid_corpus_fails_published_json_schema(name: str) -> None:
    fixture = FIXTURE_ROOT / "invalid" / f"{name}.json"
    document = json.loads(fixture.read_text(encoding="utf-8"))
    schema = json.loads(ACCEPTANCE_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(document))


@pytest.mark.parametrize(
    "mutation",
    ["unsafe-evidence-key", "passing-without-evidence", "failure-without-note"],
)
def test_published_json_schema_encodes_runtime_invariants(mutation: str) -> None:
    document = qualification_manifest()
    if mutation == "unsafe-evidence-key":
        catalog = cast("dict[str, object]", document["evidence"])
        catalog["unsafe key"] = catalog.pop("contract-test")
        document["qualification_record"] = "unsafe key"
    else:
        criteria = cast("list[dict[str, object]]", document["criteria"])
        criteria[0]["evidence"] = []
        if mutation == "failure-without-note":
            criteria[0]["result"] = "fail"
            document["decision"] = "fail"
    schema = json.loads(ACCEPTANCE_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(document))


@pytest.mark.parametrize("fixture", sorted((FIXTURE_ROOT / "valid").glob("*.json")), ids=lambda path: path.stem)
def test_valid_manifest_corpus_passes_runtime_validation(fixture: Path) -> None:
    document = json.loads(fixture.read_text(encoding="utf-8"))

    validate_manifest(document, DEFAULT_CONTRACT_PATH.read_bytes())


@pytest.mark.parametrize("fixture", sorted((FIXTURE_ROOT / "invalid").glob("*.json")), ids=lambda path: path.stem)
def test_invalid_manifest_corpus_fails_runtime_validation(fixture: Path) -> None:
    document = json.loads(fixture.read_text(encoding="utf-8"))

    with pytest.raises(ManifestValidationError):
        validate_manifest(document, DEFAULT_CONTRACT_PATH.read_bytes())


def test_approval_cannot_precede_evidence() -> None:
    manifest = deepcopy(qualification_manifest())
    evaluator = cast("dict[str, object]", manifest["evaluator"])
    evaluator["approved_at"] = "2026-10-08T09:59:00+00:00"
    with pytest.raises(ManifestValidationError, match="timestamp"):
        validate_manifest(manifest, contract_document())


def test_manifest_resolves_normalized_evidence_catalog() -> None:
    manifest = qualification_manifest()

    validated = validate_manifest(manifest, contract_document())

    assert validated.qualification_record == "contract-test"
    assert validated.criteria[0].evidence == ("contract-test",)
    assert list(validated.evidence) == ["contract-test"]


def test_validated_manifest_is_deeply_immutable() -> None:
    validated = validate_manifest(qualification_manifest(), contract_document())
    evidence_catalog = cast("dict[str, object]", validated.evidence)
    criterion_evidence = cast("list[str]", validated.criteria[0].evidence)

    with pytest.raises(TypeError):
        evidence_catalog["replacement"] = validated.evidence["contract-test"]
    with pytest.raises(AttributeError):
        criterion_evidence.append("replacement")


def test_qualification_record_requires_qualification_record_evidence_fixture() -> None:
    fixture = FIXTURE_ROOT / "invalid" / "qualification-record-type.json"
    manifest = json.loads(fixture.read_text(encoding="utf-8"))

    with pytest.raises(ManifestValidationError, match="wrong evidence type"):
        validate_manifest(manifest, DEFAULT_CONTRACT_PATH.read_bytes())


@pytest.mark.parametrize(
    ("criterion_ids", "qualification_record", "catalog", "message"),
    [
        (("missing",), "contract-test", {"contract-test": evidence_reference()}, "evidence reference"),
        (("contract-test",), "missing", {"contract-test": evidence_reference()}, "qualification_record"),
        (
            ("contract-test",),
            "contract-test",
            {"contract-test": evidence_reference(), "unused": evidence_reference()},
            "dangling",
        ),
        (
            ("contract-test", "contract-test"),
            "contract-test",
            {"contract-test": evidence_reference()},
            "unique",
        ),
    ],
    ids=("missing-criterion-reference", "missing-qualification-record", "dangling-entry", "duplicate-reference"),
)
def test_manifest_refuses_missing_dangling_or_duplicate_evidence_ids(
    criterion_ids: tuple[str, ...],
    qualification_record: str,
    catalog: dict[str, dict[str, object]],
    message: str,
) -> None:
    manifest = qualification_manifest(
        criteria=[criterion_result(evidence_ids=criterion_ids)],
        evidence=catalog,
    )
    manifest["qualification_record"] = qualification_record

    with pytest.raises(ManifestValidationError, match=message):
        validate_manifest(manifest, contract_document())


def test_manifest_requires_declared_evidence_type_for_every_reference() -> None:
    manifest = qualification_manifest(evidence={"contract-test": evidence_reference(evidence_type="other-report")})

    with pytest.raises(ManifestValidationError, match="evidence type"):
        validate_manifest(manifest, contract_document())


def test_manifest_refuses_incompatible_cross_criterion_reuse() -> None:
    criteria = (
        contract_criterion(evidence=("shared-report",)),
        contract_criterion(criterion_id="MVP-010-001", owner_spec="010", evidence=("other-report",)),
    )
    contract = contract_document(criteria)
    results = (
        criterion_result(evidence_ids=("shared",)),
        criterion_result(criterion_id="MVP-010-001", evidence_ids=("shared",)),
    )
    manifest = qualification_manifest(
        contract=contract,
        criteria=results,
        evidence={"shared": evidence_reference(evidence_type="shared-report")},
    )
    manifest["qualification_record"] = "shared"

    with pytest.raises(ManifestValidationError, match="evidence type"):
        validate_manifest(manifest, contract)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("relative-path", "/absolute/evidence.json"),
        ("relative-path", "evidence/../secret.json"),
        ("relative-path", "evidence//report.json"),
        ("relative-path", r"evidence\report.json"),
        ("relative-path", "https://example.test/report"),
        ("relative-path", "evidence/report.json?token=canary"),
        ("relative-path", "evidence/report.json#fragment"),
        ("relative-path", "user@example.test/report"),
        ("artifact-id", "artifact:canary"),
    ],
    ids=("absolute", "traversal", "empty-segment", "backslash", "scheme", "query", "fragment", "userinfo", "colon"),
)
def test_manifest_refuses_unsafe_evidence_locators(kind: str, value: str) -> None:
    manifest = qualification_manifest(
        evidence={"contract-test": evidence_reference(locator_kind=kind, locator_value=value)}
    )

    with pytest.raises(ManifestValidationError, match="manifest structure"):
        validate_manifest(manifest, contract_document())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_revision", "a" * 40),
        ("candidate_digest", "sha256:" + "b" * 64),
        ("contract_sha256", "c" * 64),
    ],
    ids=("source-revision", "candidate-digest", "contract-digest"),
)
def test_manifest_refuses_evidence_identity_mismatches(field: str, value: str) -> None:
    reference = evidence_reference(source_revision=SOURCE_REVISION, candidate_digest=CANDIDATE_DIGEST)
    reference[field] = value
    manifest = qualification_manifest(evidence={"contract-test": reference})

    with pytest.raises(ManifestValidationError, match="evidence identity"):
        validate_manifest(manifest, contract_document())


def test_decision_cannot_precede_evaluator_approval() -> None:
    manifest = qualification_manifest()
    manifest["decided_at"] = "2026-10-08T10:04:59+00:00"

    with pytest.raises(ManifestValidationError, match="decision timestamp"):
        validate_manifest(manifest, contract_document())


@pytest.mark.parametrize(
    "field",
    ["independent_from_implementation", "executed_qualified_journey", "approved"],
)
def test_passing_manifest_requires_evaluator_approval_invariants(field: str) -> None:
    manifest = qualification_manifest()
    evaluator = cast("dict[str, object]", manifest["evaluator"])
    evaluator[field] = False

    with pytest.raises(ManifestValidationError, match="evaluator"):
        validate_manifest(manifest, contract_document())


def test_fail_decision_can_be_caused_only_by_evaluator_rejection() -> None:
    manifest = qualification_manifest()
    evaluator = cast("dict[str, object]", manifest["evaluator"])
    evaluator["approved"] = False
    manifest["decision"] = "fail"

    validated = validate_manifest(manifest, contract_document())

    assert validated.decision == "fail"
    assert all(result.result == "pass" for result in validated.criteria)


@pytest.mark.parametrize(
    ("container", "field"),
    [("evidence", "produced_at"), ("evaluator", "approved_at"), ("manifest", "decided_at")],
    ids=("evidence", "approval", "decision"),
)
def test_manifest_requires_zero_offset_utc_timestamps(container: str, field: str) -> None:
    manifest = qualification_manifest()
    if container == "evidence":
        catalog = cast("dict[str, dict[str, object]]", manifest["evidence"])
        catalog["contract-test"][field] = "2026-10-08T12:00:00+02:00"
    elif container == "evaluator":
        evaluator = cast("dict[str, object]", manifest["evaluator"])
        evaluator[field] = "2026-10-08T12:05:00+02:00"
    else:
        manifest[field] = "2026-10-08T12:05:00+02:00"

    with pytest.raises(ManifestValidationError, match="manifest structure"):
        validate_manifest(manifest, contract_document())
