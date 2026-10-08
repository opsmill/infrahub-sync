"""Invoke boundary tests for selected-contract acceptance validation."""

import json
from pathlib import Path

import pytest
from invoke import Context
from invoke.exceptions import Exit

from tasks import acceptance, release
from tests.release.acceptance_fixtures import contract_document, qualification_manifest


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
