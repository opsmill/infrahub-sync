"""Typer coverage for ``infrahub-sync configs convert`` (spec FR-001 to FR-005, FR-021)."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from infrahub_sync import cli
from infrahub_sync.cli import app
from infrahub_sync.configuration.conversion import V2_NON_CREDENTIAL_VARIABLES

if TYPE_CHECKING:
    from tests.cli.conftest import FakeSyncAPI

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
    assert "credentials" not in written
    assert written["configuration"]["source"]["credential"] == "netbox-token"
    assert written["configuration"]["destination"]["credential"] == "infrahub-token"
    assert "token" not in written["configuration"]["source"]["settings"]
    assert "token" not in written["configuration"]["destination"]["settings"]
    assert "INFRAHUB_SYNC_CREDENTIAL_" not in stdout
    assert ".env" not in stdout
    assert stdout.startswith('Converting config.yml (v1 or v2 configuration "from-netbox")')
    sections = ["Credentials (", "Settings taken from", "Notes:", "Next steps:"]
    positions = [stdout.index(section) for section in sections]
    assert positions == sorted(positions)
    assert "  1. infrahub-sync credentials create netbox-token" in stdout
    assert "  2. infrahub-sync credentials create infrahub-token" in stdout
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
    assert "Credentials (create each stored credential before you register the package):" in stdout
    assert "netbox-token (token)" in stdout
    assert "infrahub-token (token)" in stdout
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


@pytest.mark.parametrize("spelling", ["same", "through-a-dot-directory"])
def test_output_that_is_the_input_file_exits_1_even_with_force(config_file: Path, spelling: str) -> None:
    original = config_file.read_text(encoding="utf-8")
    output = config_file if spelling == "same" else config_file.parent / "." / config_file.name

    code, stdout, stderr = _convert(str(config_file), "--output", str(output), "--yes", "--force")

    assert code == 1
    assert f"--output {output} is the input file {config_file}" in stderr
    assert "Credentials (" not in stdout
    assert config_file.read_text(encoding="utf-8") == original
    assert sorted(path.name for path in config_file.parent.iterdir()) == ["config.yml"]


def test_output_created_while_the_question_waits_is_not_replaced(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _terminal(monkeypatch)
    package = config_file.parent / "package.yml"

    def _confirm(*_: object, **__: object) -> bool:
        package.write_text("written meanwhile\n", encoding="utf-8")
        return True

    monkeypatch.setattr(cli.typer, "confirm", _confirm)
    code, _, stderr = _convert(str(config_file))

    assert code == 1
    assert f"{package} exists; pass --force to replace it" in stderr
    assert package.read_text(encoding="utf-8") == "written meanwhile\n"
    assert sorted(path.name for path in config_file.parent.iterdir()) == ["config.yml", "package.yml"]


def test_write_failure_names_the_target_and_the_system_error(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(*_: object, **__: object) -> None:
        raise PermissionError(13, "Permission denied", "/a/path/that/is/not/printed")

    monkeypatch.setattr(cli.tempfile, "NamedTemporaryFile", _refuse)
    code, _, stderr = _convert(str(config_file), "--yes")

    package = config_file.parent / "package.yml"
    assert code == 1
    assert f"error: cannot write {package}: Permission denied" in stderr
    assert "/a/path/that/is/not/printed" not in stderr
    assert not package.exists()


def test_file_system_without_hard_links_still_writes_the_package(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_hard_links(*_: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(cli.os, "link", _no_hard_links)
    code, _, stderr = _convert(str(config_file), "--yes")

    package = config_file.parent / "package.yml"
    assert code == 0, stderr
    assert yaml.safe_load(package.read_text(encoding="utf-8"))["format_version"] == 1
    assert sorted(path.name for path in config_file.parent.iterdir()) == ["config.yml", "package.yml"]


def test_yaml_anchors_in_the_input_are_written_out_in_full(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    config.write_text(
        "name: from-netbox\n"
        "source: {name: netbox, settings: {url: 'https://netbox.example.com'}}\n"
        "destination: {name: infrahub, settings: {url: 'https://infrahub.example.com'}}\n"
        "schema_mapping:\n"
        "  - {name: BuiltinTag, mapping: extras.tags, identifiers: [name], fields: &fields [{name: name, mapping: name}]}\n"
        "  - {name: LocationSite, mapping: dcim.sites, identifiers: [name], fields: *fields}\n",
        encoding="utf-8",
    )

    code, _, stderr = _convert(str(config), "--yes")

    assert code == 0, stderr
    text = (tmp_path / "package.yml").read_text(encoding="utf-8")
    assert "&id" not in text
    assert "*id" not in text


def test_default_output_that_is_the_input_file_names_the_option(tmp_path: Path) -> None:
    config = tmp_path / "package.yml"
    shutil.copyfile(NETBOX_EXAMPLE, config)

    code, _, stderr = _convert(str(config), "--yes")

    assert code == 1
    assert f"error: the default output {config} is the input file; pass --output with another path" in stderr


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


def test_no_visible_v2_variable_is_reported(config_file: Path) -> None:
    code, stdout, stderr = _convert(str(config_file), "--yes")

    assert code == 0, stderr
    assert (
        "  no v2 variable (NETBOX_ADDRESS, NETBOX_URL, INFRAHUB_ADDRESS, INFRAHUB_URL) is set here; "
        "values come from config.yml or the flags"
    ) in stdout.splitlines()


# --- T021: refusals go to standard error, nothing is written, exit 1 (FR-015) -----------

_CANARY = "canary-refused-credential-value"


def test_refusal_prints_every_reason_to_stderr_in_the_contract_layout(tmp_path: Path) -> None:
    source = tmp_path / "config.yml"
    source.write_text(
        yaml.safe_dump(
            {
                "name": "refused",
                "source": {
                    "name": "netbox",
                    "adapter": "./custom_adapter.py:MockdbAdapter",
                    "settings": {"url": "https://netbox.example.com", "token": _CANARY},
                },
                "destination": {
                    "name": "peeringmanager",
                    "settings": {"url": "https://peering.example.com", "token": _CANARY},
                },
            }
        ),
        encoding="utf-8",
    )

    code, stdout, stderr = _convert(str(source), "--yes")

    assert code == 1
    assert not stdout
    assert stderr.splitlines() == [
        "error: config.yml cannot be converted (2 reasons)",
        "  source.adapter          ./custom_adapter.py:MockdbAdapter   "
        "custom adapters are not available in this release (INFP-654, planned for 3.0)",
        "  destination.name        peeringmanager                      "
        "V3 does not write to peeringmanager yet; this release writes only to Infrahub",
    ]
    assert _CANARY not in stderr
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.yml"]


def test_refusal_without_a_printable_value_has_an_empty_value_column(tmp_path: Path) -> None:
    source = tmp_path / "config.yml"
    source.write_text(
        yaml.safe_dump(
            {
                "name": "refused",
                "source": {"name": "netbox", "settings": {"url": f"https://user:{_CANARY}@netbox.example.com"}},
                "destination": {"name": "infrahub", "settings": {"url": "https://infrahub.example.com"}},
            }
        ),
        encoding="utf-8",
    )

    code, _, stderr = _convert(str(source), "--yes")

    assert code == 1
    assert stderr.splitlines()[1] == (
        f"  {'source.settings.url':<23} {'':<35} "
        "the address from config.yml holds a user name or password; remove it from the address and pass it as a credential"
    )
    assert _CANARY not in stderr
    assert not (tmp_path / "package.yml").exists()


# --- the stored credentials to create (spec 027, User Story 3) ----------------------------

_LITERAL = "canary-literal-credential-value-5d1e"
_LITERAL_DIGEST = hashlib.sha256(_LITERAL.encode()).hexdigest()
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _holds_literal(text: str) -> bool:
    """Whether a text holds the literal credential value; the value never reaches an assertion message."""
    size = len(_LITERAL)
    return any(
        hashlib.sha256(text[start : start + size].encode()).hexdigest() == _LITERAL_DIGEST
        for start in range(max(len(text) - size + 1, 0))
    )


@pytest.fixture
def account_config_file(tmp_path: Path) -> Path:
    """A v2 file whose source takes a username and a password, and whose destination takes a token.

    The password and the token are literal values in the file, as some v2 files hold them.
    """
    target = tmp_path / "config.yml"
    target.write_text(
        yaml.safe_dump(
            {
                "name": "from-cisco-aci",
                "source": {
                    "name": "aci",
                    "settings": {
                        "url": "https://apic.example.com",
                        "username": "${CISCO_APIC_USERNAME}",
                        "password": _LITERAL,
                    },
                },
                "destination": {
                    "name": "infrahub",
                    "settings": {"url": "https://infrahub.example.com", "token": _LITERAL},
                },
                "schema_mapping": [],
            }
        ),
        encoding="utf-8",
    )
    return target


def test_report_lists_each_credential_with_its_fields_and_its_command(account_config_file: Path) -> None:
    code, stdout, stderr = _convert(str(account_config_file), "--yes")

    assert code == 0, stderr
    lines = stdout.splitlines()
    assert (
        f"  {'source.credential':<28} aci-account (username, password): "
        "infrahub-sync credentials create aci-account --username <username>"
    ) in lines
    assert (
        f"  {'destination.credential':<28} infrahub-token (token): infrahub-sync credentials create infrahub-token"
    ) in lines
    assert (
        f"  {'source.settings.username':<28} ${{CISCO_APIC_USERNAME}} in config.yml -> field username of aci-account"
        in lines
    )
    assert f"  {'source.settings.password':<28} a literal value in config.yml -> field password of aci-account" in lines
    assert (
        f"  {'destination.settings.token':<28} a literal value in config.yml -> field token of infrahub-token" in lines
    )
    steps = lines[lines.index("Next steps:") + 1 :]
    assert steps == [
        "  1. infrahub-sync credentials create infrahub-token",
        "  2. infrahub-sync credentials create aci-account --username <username>",
        f"  3. infrahub-sync configs register {account_config_file.parent / 'package.yml'}",
    ]


def test_report_and_package_never_hold_a_literal_credential_value(account_config_file: Path) -> None:
    code, stdout, stderr = _convert(str(account_config_file), "--yes")

    assert code == 0
    package = (account_config_file.parent / "package.yml").read_text(encoding="utf-8")
    assert not _holds_literal(stdout)
    assert not _holds_literal(stderr)
    assert not _holds_literal(package)
    written = yaml.safe_load(package)["configuration"]
    assert written["source"]["credential"] == "aci-account"
    assert not {"username", "password"} & set(written["source"]["settings"])
    assert "token" not in written["destination"]["settings"]
    assert (
        "  warning: config.yml still holds a literal value for source.settings.password; remove it once V3 works"
        in (stdout.splitlines())
    )


def test_help_describes_the_stored_credentials_of_the_report() -> None:
    result = RUNNER.invoke(app, ["configs", "convert", "--help"], terminal_width=200)

    text = " ".join(_ANSI.sub("", result.output).split())
    assert result.exit_code == 0
    assert "each stored credential to create" in text
    assert "infrahub-sync credentials create" in text
    assert "environment variable the Sync worker reads" not in text
    assert "Compose" not in text


def _refused_registration(*credentials: dict[str, Any], reason: str = "credential-not-found") -> httpx.Response:
    """Return the answer of the Sync API to a registration that names stored credentials it cannot use."""
    error: dict[str, Any] = {
        "code": "configs-validation",
        "message": "the configuration service refused the request",
        "status": 422,
        "family": "validation",
        "reason": reason,
        "details": {"credentials": list(credentials)},
    }
    return httpx.Response(422, json={"error": error})


def _register(package: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    monkeypatch.setenv("INFRAHUB_SYNC_API_URL", "http://127.0.0.1:8001")
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", "ifs_convert_test_token")
    result = RUNNER.invoke(app, ["configs", "register", str(package)])
    return result.exit_code, result.stdout, result.stderr


def test_registration_before_the_credentials_exist_names_each_missing_credential_and_its_fields(
    account_config_file: Path, sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _, stderr = _convert(str(account_config_file), "--yes")
    assert code == 0, stderr
    package = account_config_file.parent / "package.yml"
    sync_api.answers["POST /configs"] = _refused_registration(
        {"block": "source", "name": "aci-account", "code": "credential-not-found", "fields": ["password", "username"]},
        {"block": "destination", "name": "infrahub-token", "code": "credential-not-found", "fields": ["token"]},
    )

    code, _, stderr = _register(package, monkeypatch)

    assert code == 1
    sent = cast("dict[str, Any]", sync_api.body())["package"]["configuration"]
    assert sent["source"]["credential"] == "aci-account"
    assert sent["destination"]["credential"] == "infrahub-token"
    lines = stderr.splitlines()
    assert "reason: credential-not-found" in lines
    assert (
        "credential: source names aci-account, which does not exist; it needs username, password; "
        "an administrator creates it with: infrahub-sync credentials create aci-account --username <username>"
    ) in lines
    assert (
        "credential: destination names infrahub-token, which does not exist; it needs token; "
        "an administrator creates it with: infrahub-sync credentials create infrahub-token"
    ) in lines
    assert not _holds_literal(stderr)


def test_registration_names_the_fields_that_a_stored_credential_does_not_hold(
    config_file: Path, sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _convert(str(config_file), "--yes")[0] == 0
    sync_api.answers["POST /configs"] = _refused_registration(
        {"block": "source", "name": "netbox-token", "code": "credential-field-missing", "fields": ["token"]},
        reason="credential-field-missing",
    )

    code, _, stderr = _register(config_file.parent / "package.yml", monkeypatch)

    assert code == 1
    assert (
        "credential: source names netbox-token, which does not hold token; "
        "an administrator sets it with: infrahub-sync credentials set netbox-token"
    ) in stderr.splitlines()


def test_registration_refused_by_a_sync_api_that_sends_no_details_prints_the_reason_only(
    config_file: Path, sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _convert(str(config_file), "--yes")[0] == 0
    answer = _refused_registration().json()
    del answer["error"]["details"]
    sync_api.answers["POST /configs"] = httpx.Response(422, json=answer)

    code, _, stderr = _register(config_file.parent / "package.yml", monkeypatch)

    assert code == 1
    assert "reason: credential-not-found" in stderr.splitlines()
    assert "credential:" not in stderr


@pytest.mark.parametrize(
    "details",
    [
        pytest.param({"credentials": "netbox-token"}, id="not-a-list"),
        pytest.param({"credentials": [{"block": "source", "name": "netbox-token"}]}, id="no-code"),
        pytest.param(
            {"credentials": [{"block": "worker", "name": "n", "code": "credential-not-found", "fields": []}]},
            id="unknown-block",
        ),
        pytest.param(
            {"credentials": [{"block": "source", "name": "n", "code": "credential-not-found", "fields": [1]}]},
            id="field-not-a-text",
        ),
    ],
)
def test_registration_refusal_with_malformed_details_is_a_protocol_error(
    config_file: Path, sync_api: FakeSyncAPI, monkeypatch: pytest.MonkeyPatch, details: dict[str, Any]
) -> None:
    assert _convert(str(config_file), "--yes")[0] == 0
    answer = _refused_registration().json()
    answer["error"]["details"] = details
    sync_api.answers["POST /configs"] = httpx.Response(422, json=answer)

    code, _, stderr = _register(config_file.parent / "package.yml", monkeypatch)

    assert code == 1
    assert stderr.splitlines()[0] == "error: protocol"
