"""Invoke boundary tests for selected-contract acceptance validation."""

import json
from pathlib import Path
from typing import cast

import pytest
from invoke import Context
from invoke.exceptions import Exit

from tasks import acceptance, release
from tests.release.acceptance_fixtures import contract_document, evidence_reference, qualification_manifest


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    contract = tmp_path / "contract.md"
    manifest = tmp_path / "manifest.json"
    contract.write_bytes(contract_document())
    manifest.write_text(json.dumps(qualification_manifest(contract=contract.read_bytes())), encoding="utf-8")
    return contract, manifest


def test_command_reports_consistency_without_approval_or_eligibility_claims(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, manifest = _write_inputs(tmp_path)
    monkeypatch.setattr(acceptance, "DEFAULT_CONTRACT_PATH", contract)

    release.validate_acceptance.body(Context(), str(manifest))

    output = capsys.readouterr().out
    assert "consistent with selected contract: yes" in output
    assert "contract approved" not in output.lower()
    assert "release-eligible" not in output.lower()
    assert len(output) < 2_000


def test_command_refuses_invalid_input_with_safe_next_action(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, manifest = _write_inputs(tmp_path)
    manifest.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(acceptance, "DEFAULT_CONTRACT_PATH", contract)

    with pytest.raises(Exit) as raised:
        release.validate_acceptance.body(Context(), str(manifest))
    assert raised.value.code != 0
    output = capsys.readouterr().err
    assert "Next action:" in output
    assert len(output) < 2_000


def test_command_labels_checked_out_contract_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, manifest = _write_inputs(tmp_path)
    monkeypatch.setattr(acceptance, "DEFAULT_CONTRACT_PATH", contract)

    release.validate_acceptance.body(Context(), str(manifest))

    assert "Selection checked-out" in capsys.readouterr().out


def test_command_accepts_and_labels_retained_contract_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contract, manifest = _write_inputs(tmp_path)

    release.validate_acceptance.body(Context(), str(manifest), contract=str(contract))

    output = capsys.readouterr().out
    assert "Selection retained" in output
    assert "consistent with selected contract: yes" in output


def test_command_states_spec_014_boundaries_without_approval_or_eligibility_claims(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, manifest = _write_inputs(tmp_path)
    monkeypatch.setattr(acceptance, "DEFAULT_CONTRACT_PATH", contract)

    release.validate_acceptance.body(Context(), str(manifest))

    output = capsys.readouterr().out.lower()
    assert "artifact availability and byte verification remain a spec-014 gate" in output
    assert "contract approval and release eligibility remain spec-014 decisions" in output
    assert "contract approved" not in output
    assert "release-eligible" not in output


def test_command_discloses_metadata_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, manifest = _write_inputs(tmp_path)
    document = qualification_manifest(contract=contract.read_bytes())
    catalog = cast("dict[str, dict[str, object]]", document["evidence"])
    catalog["contract-test"]["producer"] = "PRIVATE-PRODUCER-CANARY"
    catalog["contract-test"]["locator"] = {
        "kind": "relative-path",
        "value": "private/SECRET-BODY-CANARY.json",
    }
    manifest.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(acceptance, "DEFAULT_CONTRACT_PATH", contract)

    release.validate_acceptance.body(Context(), str(manifest))

    output = capsys.readouterr().out
    assert "PRIVATE-PRODUCER-CANARY" not in output
    assert "SECRET-BODY-CANARY" not in output
    assert "application/json" not in output


def test_command_refusal_does_not_echo_credential_canary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    contract, manifest = _write_inputs(tmp_path)
    credential_canary = "TOKEN-CANARY-DO-NOT-PRINT"
    document = qualification_manifest(
        contract=contract.read_bytes(),
        evidence={
            "contract-test": evidence_reference(
                locator_value=f"https://user:{credential_canary}@example.test/evidence.json"
            )
        },
    )
    manifest.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(acceptance, "DEFAULT_CONTRACT_PATH", contract)

    with pytest.raises(Exit):
        release.validate_acceptance.body(Context(), str(manifest))

    output = capsys.readouterr().err
    assert credential_canary not in output
    assert "https://" not in output
    assert "Next action:" in output
    assert len(output) < 2_000
