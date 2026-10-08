"""Strict qualification-manifest and decision tests."""

import json
from copy import deepcopy
from typing import cast

import pytest

from tasks.acceptance import (
    ManifestValidationError,
    QualificationManifest,
    qualification_manifest_schema,
    validate_manifest,
)
from tests.release.acceptance_fixtures import contract_document, criterion_result, qualification_manifest


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


def test_generated_schema_is_stable() -> None:
    first = json.dumps(qualification_manifest_schema(), indent=2, sort_keys=True) + "\n"
    second = json.dumps(QualificationManifest.model_json_schema(), indent=2, sort_keys=True) + "\n"
    assert first == second


def test_approval_cannot_precede_evidence() -> None:
    manifest = deepcopy(qualification_manifest())
    evaluator = cast("dict[str, object]", manifest["evaluator"])
    evaluator["approved_at"] = "2026-10-08T09:59:00+00:00"
    with pytest.raises(ManifestValidationError, match="timestamp"):
        validate_manifest(manifest, contract_document())
