"""Typer coverage for ``infrahub-sync configs convert`` (spec FR-001 to FR-005, FR-021)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from infrahub_sync import cli
from infrahub_sync.cli import app
from infrahub_sync.configuration.conversion import V2_NON_CREDENTIAL_VARIABLES

ROOT = Path(__file__).resolve().parents[2]
NETBOX_EXAMPLE = ROOT / "tests/data/v2_examples/netbox_to_infrahub/config.yml"
RUNNER = CliRunner()


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test without v2 variables or Sync API settings, and without a terminal."""
    for name in (*V2_NON_CREDENTIAL_VARIABLES, "INFRAHUB_SYNC_API_URL", "INFRAHUB_SYNC_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    target = tmp_path / "config.yml"
    shutil.copyfile(NETBOX_EXAMPLE, target)
    return target


def _convert(*args: str, stdin: str | None = None) -> tuple[int, str, str]:
    result = RUNNER.invoke(app, ["configs", "convert", *args], input=stdin)
    return result.exit_code, result.stdout, result.stderr


def _terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)


def test_yes_writes_the_package_next_to_the_input_after_the_report(config_file: Path) -> None:
    code, stdout, stderr = _convert(str(config_file), "--yes")

    assert code == 0, stderr
    package = config_file.parent / "package.yml"
    written = yaml.safe_load(package.read_text(encoding="utf-8"))
    assert written["format_version"] == 1
    assert written["configuration"]["name"] == "from-netbox"
    assert set(written["credentials"]) == {"netbox-token", "infrahub-token"}
    assert stdout.startswith('Converting config.yml (v1 or v2 configuration "from-netbox")')
    sections = ["Credentials (", "Settings taken from", "Notes:", "Next steps:"]
    positions = [stdout.index(section) for section in sections]
    assert positions == sorted(positions)
    assert (
        "  1. Set INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN and INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN in .env"
        in stdout
    )
    assert f"  3. infrahub-sync configs register {package}" in stdout
    assert set(config_file.parent.iterdir()) == {config_file, package}


def test_report_is_printed_before_the_question_and_nothing_is_written_before_it(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _terminal(monkeypatch)
    package = config_file.parent / "package.yml"

    def _confirm(text: str, **_: object) -> bool:
        assert not package.exists()
        assert text == f"Write {package}?"
        return True

    monkeypatch.setattr(cli.typer, "confirm", _confirm)
    code, stdout, stderr = _convert(str(config_file))

    assert code == 0, stderr
    assert "Credentials (set these in the Sync deployment's .env file):" in stdout
    assert package.exists()


def test_answer_y_at_a_terminal_writes(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _terminal(monkeypatch)
    package = config_file.parent / "package.yml"

    code, stdout, stderr = _convert(str(config_file), stdin="y\n")

    assert code == 0, stderr
    assert f"Write {package}? [y/N]" in stdout
    assert package.exists()
    assert "Next steps:" in stdout


@pytest.mark.parametrize("answer", ["n\n", "\n"], ids=["n", "empty"])
def test_answer_n_or_empty_writes_nothing_and_exits_0(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    _terminal(monkeypatch)

    code, stdout, _ = _convert(str(config_file), stdin=answer)

    assert code == 0
    assert "nothing written" in stdout
    assert "Next steps:" not in stdout
    assert not (config_file.parent / "package.yml").exists()


def test_no_terminal_and_no_yes_writes_nothing_and_exits_1(config_file: Path) -> None:
    code, stdout, stderr = _convert(str(config_file))

    assert code == 1
    assert "Credentials (" in stdout
    assert "nothing written; run again with --yes" in stdout + stderr
    assert not (config_file.parent / "package.yml").exists()


def test_existing_output_without_force_exits_1_before_the_report(config_file: Path) -> None:
    package = config_file.parent / "package.yml"
    package.write_text("kept\n", encoding="utf-8")

    code, stdout, stderr = _convert(str(config_file), "--yes")

    assert code == 1
    assert f"{package} exists; pass --force to replace it" in stderr
    assert "Credentials (" not in stdout
    assert package.read_text(encoding="utf-8") == "kept\n"


def test_force_replaces_an_existing_output(config_file: Path) -> None:
    package = config_file.parent / "package.yml"
    package.write_text("kept\n", encoding="utf-8")

    code, _, stderr = _convert(str(config_file), "--yes", "--force")

    assert code == 0, stderr
    assert yaml.safe_load(package.read_text(encoding="utf-8"))["format_version"] == 1
    assert sorted(path.name for path in config_file.parent.iterdir()) == ["config.yml", "package.yml"]


def test_output_sets_the_path(config_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "out" / "netbox.yml"
    target.parent.mkdir()

    code, stdout, stderr = _convert(str(config_file), "--output", str(target), "--yes")

    assert code == 0, stderr
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["format_version"] == 1
    assert not (config_file.parent / "package.yml").exists()
    assert f"infrahub-sync configs register {target}" in stdout


def test_output_in_a_missing_directory_exits_2(config_file: Path, tmp_path: Path) -> None:
    code, _, stderr = _convert(str(config_file), "--output", str(tmp_path / "missing" / "p.yml"), "--yes")

    assert code == 2
    assert "error:" in stderr


def test_unreadable_file_exits_2(tmp_path: Path) -> None:
    code, _, stderr = _convert(str(tmp_path / "absent.yml"), "--yes")

    assert code == 2
    assert "error: cannot read" in stderr
    assert not (tmp_path / "package.yml").exists()


def test_invalid_yaml_reports_only_line_and_column(tmp_path: Path) -> None:
    source = tmp_path / "config.yml"
    source.write_text("name: x\nsource:\n  token: canary-secret-value: [\n", encoding="utf-8")

    code, stdout, stderr = _convert(str(source), "--yes")

    assert code == 2
    assert "error: config.yml is not valid YAML at line 3, column" in stderr
    assert "canary-secret-value" not in stdout + stderr
    assert not (tmp_path / "package.yml").exists()


def test_v3_package_input_exits_2(tmp_path: Path) -> None:
    source = tmp_path / "config.yml"
    source.write_text(yaml.safe_dump({"format_version": 1, "configuration": {}}), encoding="utf-8")

    code, _, stderr = _convert(str(source), "--yes")

    assert code == 2
    assert "format_version: the file is already a V3 configuration package" in stderr
    assert not (tmp_path / "package.yml").exists()


@pytest.mark.parametrize(
    "content",
    ["just text\n", "- a\n- b\n", "name: x\nsource: {}\n", ""],
    ids=["scalar", "list", "no-source-name", "empty"],
)
def test_input_that_is_not_a_v2_mapping_exits_2(tmp_path: Path, content: str) -> None:
    source = tmp_path / "config.yml"
    source.write_text(content, encoding="utf-8")

    code, _, stderr = _convert(str(source), "--yes")

    assert code == 2
    assert "error: config.yml cannot be converted" in stderr
    assert not (tmp_path / "package.yml").exists()


def test_no_sync_api_variable_is_needed_and_no_client_is_built(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_client(*_: object, **__: object) -> None:
        msg = "configs convert must not build a Sync API client"
        raise AssertionError(msg)

    monkeypatch.setattr(cli, "SyncClient", _no_client)

    code, _, stderr = _convert(str(config_file), "--yes")

    assert code == 0, stderr


def test_visible_v2_address_variable_is_reported_and_written(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETBOX_ADDRESS", "https://netbox.example.com")

    code, stdout, stderr = _convert(str(config_file), "--yes")

    assert code == 0, stderr
    assert "from NETBOX_ADDRESS (config.yml had https://demo.netbox.dev)" in stdout
    assert "v2 variables visible here: NETBOX_ADDRESS" in stdout
    written = yaml.safe_load((config_file.parent / "package.yml").read_text(encoding="utf-8"))
    assert written["configuration"]["source"]["settings"]["url"] == "https://netbox.example.com"


def test_source_url_flag_overrides_the_file(config_file: Path) -> None:
    code, stdout, stderr = _convert(str(config_file), "--yes", "--source-url", "https://nb.internal")

    assert code == 0, stderr
    assert "from --source-url" in stdout
    written = yaml.safe_load((config_file.parent / "package.yml").read_text(encoding="utf-8"))
    assert written["configuration"]["source"]["settings"]["url"] == "https://nb.internal"
