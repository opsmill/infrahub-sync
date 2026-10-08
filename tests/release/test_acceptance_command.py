"""Invoke boundary tests for selected-contract acceptance validation."""

import json
import subprocess  # noqa: S404 -- regression test executes a fixed local Invoke command
import sys
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


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("candidate", "3.0.0\r\nCANDIDATE-CONTROL-CANARY"),
        ("evaluator", "reviewer@example.test\x1b[31mEVALUATOR-ANSI-CANARY"),
    ],
    ids=("candidate-version", "evaluator-identity"),
)
def test_command_refuses_rendered_control_injection_without_echoing_canary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    value: str,
) -> None:
    contract, manifest = _write_inputs(tmp_path)
    document = qualification_manifest(contract=contract.read_bytes())
    if target == "candidate":
        candidate = cast("dict[str, object]", document["candidate"])
        candidate["version"] = value
    else:
        evaluator = cast("dict[str, object]", document["evaluator"])
        evaluator["identity"] = value
    manifest.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(acceptance, "DEFAULT_CONTRACT_PATH", contract)

    with pytest.raises(Exit) as raised:
        release.validate_acceptance.body(Context(), str(manifest))

    output = capsys.readouterr().err
    assert raised.value.code != 0
    assert "CANARY" not in output
    assert "acceptance validation failed" in output


@pytest.mark.parametrize(
    ("content", "message"),
    [(b"\xff", "not valid UTF-8"), (b"{", "malformed JSON")],
    ids=("invalid-utf8", "malformed-json"),
)
def test_manifest_read_failures_are_distinct(content: bytes, message: str, tmp_path: Path) -> None:
    contract, manifest = _write_inputs(tmp_path)
    manifest.write_bytes(content)

    with pytest.raises(acceptance.ManifestValidationError, match=message):
        acceptance.validate_acceptance(manifest, contract_path=contract)


@pytest.mark.parametrize(
    ("target", "message"),
    [("missing", "does not exist"), ("directory", "is a directory")],
)
def test_contract_path_failures_are_distinct(target: str, message: str, tmp_path: Path) -> None:
    _, manifest = _write_inputs(tmp_path)
    contract = tmp_path / ("missing.md" if target == "missing" else "contract-directory")
    if target == "directory":
        contract.mkdir()

    with pytest.raises(acceptance.ContractValidationError, match=message):
        acceptance.validate_acceptance(manifest, contract_path=contract)


def test_contract_invalid_utf8_is_reported_distinctly(tmp_path: Path) -> None:
    contract, manifest = _write_inputs(tmp_path)
    contract.write_bytes(b"\xff")

    with pytest.raises(acceptance.ContractValidationError, match="not valid UTF-8"):
        acceptance.validate_acceptance(manifest, contract_path=contract)


@pytest.mark.parametrize(
    ("exception", "message"),
    [(PermissionError(), "permission denied"), (OSError(), "I/O error")],
    ids=("permission", "generic-io"),
)
def test_manifest_io_failures_are_safe_and_actionable(
    exception: OSError,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract, manifest = _write_inputs(tmp_path)
    canary = "UNSAFE-PATH-CANARY"
    unsafe_manifest = manifest.with_name(canary)
    original_read_bytes = Path.read_bytes

    def fail_selected(path: Path) -> bytes:
        if path == unsafe_manifest:
            raise exception
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_selected)

    with pytest.raises(acceptance.ManifestValidationError, match=message) as raised:
        acceptance.validate_acceptance(unsafe_manifest, contract_path=contract)

    assert canary not in str(raised.value)
    assert "Next action:" in str(raised.value)


def test_invoke_renders_each_refusal_once(tmp_path: Path) -> None:
    contract, manifest = _write_inputs(tmp_path)
    manifest.write_text("[]", encoding="utf-8")

    completed = subprocess.run(  # noqa: S603 -- command and arguments are fixed test inputs
        [
            sys.executable,
            "-m",
            "invoke",
            "release.validate-acceptance",
            "--manifest",
            str(manifest),
            "--contract",
            str(contract),
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert output.count("acceptance validation failed") == 1
