"""Credential-canary checks for the v2 conversion (spec SC-003, FR-007, FR-030; research R9).

Follows ``tests/test_credential_canary.py``: one canary value is planted wherever a credential
can reach the conversion, and every output is scanned for it.

- Every credential setting of the source and the destination holds the canary as a literal.
- Every v2 credential variable of ``V2_ENVIRONMENT_FIRST`` is set to the canary in the
  environment the command sees. The pure function receives only ``visible_v2_variables``.
- ``url: ${NETBOX_TOKEN}`` names a credential variable in an address placeholder.
- An address holds the canary as its password, with or without a scheme, in the value v2
  used or in the value that lost to it.
- A setting the bundled adapter does not accept holds the canary, whatever its name.
- A V3 package, a list and a scalar that hold the canary are given to the command.

The outputs scanned are the written package, standard output, standard error and the logs
captured at DEBUG. A control test plants the canary in a copied key to show that the scans
find a value that is there.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from typer.testing import CliRunner

from infrahub_sync import cli
from infrahub_sync.cli import app
from infrahub_sync.configuration.capabilities import get_adapter_capabilities
from infrahub_sync.configuration.conversion import (
    V2_ENVIRONMENT_FIRST,
    V2_NON_CREDENTIAL_VARIABLES,
    ConversionResult,
    convert_v2_configuration,
    visible_v2_variables,
)
from infrahub_sync.configuration.conversion_report import next_step_lines, refusal_lines, report_lines

if TYPE_CHECKING:
    from collections.abc import Callable

CANARY = "c4n4ry-5b0e2c71d9a8f36-do-not-leak"

_EXAMPLES = Path(__file__).resolve().parents[1] / "data" / "v2_examples"
_RUNNER = CliRunner()

CREDENTIAL_VARIABLES = sorted(
    {
        variable
        for settings in V2_ENVIRONMENT_FIRST.values()
        for entry in settings
        if entry.kind == "credential"
        for variable in entry.variables
    }
)

# Non-credential values a customer would give for the examples that need them
# (see ``test_conversion_examples.py``).
_EXAMPLE_VARIABLES = {
    "aci_to_infrahub": {"CISCO_APIC_URL": "https://apic.example.com", "CISCO_APIC_VERIFY": "true"},
}
_EXAMPLE_SOURCE_URL = {
    "observium_to_infrahub": "https://observium.example.com",
    "slurpit_to_infrahub": "https://slurpit.example.com",
}

CONVERTED = [
    "aci_to_infrahub",
    "device42_to_infrahub",
    "ipfabric_to_infrahub",
    "librenms_to_infrahub",
    "nautobot-v1_to_infrahub",
    "nautobot-v2_to_infrahub",
    "netbox_to_infrahub",
    "observium_to_infrahub",
    "peering-manager_to_infrahub",
    "peeringdb_to_infrahub",
    "prometheus_to_infrahub (node_exporter)",
    "slurpit_to_infrahub",
]


def _example(name: str) -> dict[str, Any]:
    return yaml.safe_load((_EXAMPLES / name / "config.yml").read_text(encoding="utf-8"))


def _with_literal_credentials(data: dict[str, Any]) -> dict[str, Any]:
    """Return ``data`` with the canary as a literal in every credential setting of both sides."""
    planted = copy.deepcopy(data)
    for side in ("source", "destination"):
        adapter = planted[side]
        settings = adapter.setdefault("settings", {}) or {}
        adapter["settings"] = settings
        for setting in get_adapter_capabilities(adapter["name"]).credential_setting_paths:
            settings[setting] = CANARY
    return planted


def _netbox_with_url(url: str) -> dict[str, Any]:
    data = _with_literal_credentials(_example("netbox_to_infrahub"))
    data["source"]["settings"]["url"] = url
    return data


def _netbox_with_setting(key: str) -> dict[str, Any]:
    """Return the NetBox example with a setting the adapter does not accept, holding the canary."""
    data = _with_literal_credentials(_example("netbox_to_infrahub"))
    data["source"]["settings"][key] = CANARY
    return data


def _ipfabric_with_base_url(url: str) -> dict[str, Any]:
    data = _with_literal_credentials(_example("ipfabric_to_infrahub"))
    data["source"]["settings"]["base_url"] = url
    return data


@dataclass(frozen=True)
class _RefusedInput:
    """An input that is refused, the setting the refusal names and the v2 variables it needs."""

    make: Callable[[], dict[str, Any]]
    location: str
    variables: dict[str, str] = field(default_factory=dict)


_SCHEME_LESS = f"admin:{CANARY}@netbox.example.com"

REFUSED_INPUTS: dict[str, _RefusedInput] = {
    "url-names-a-credential-variable": _RefusedInput(
        lambda: _netbox_with_url("${NETBOX_TOKEN}"), "source.settings.url"
    ),
    "address-with-password": _RefusedInput(
        lambda: _netbox_with_url(f"https://admin:{CANARY}@netbox.example.com"), "source.settings.url"
    ),
    "scheme-less-address-wins": _RefusedInput(lambda: _netbox_with_url(_SCHEME_LESS), "source.settings.url"),
    "scheme-less-address-loses-to-a-variable": _RefusedInput(
        lambda: _netbox_with_url(_SCHEME_LESS),
        "source.settings.url",
        {"NETBOX_ADDRESS": "https://netbox.example.com"},
    ),
    "ipfabric-variable-loses-to-the-file": _RefusedInput(
        lambda: _ipfabric_with_base_url("https://ipf.example.com"),
        "source.settings.base_url",
        {"IPF_URL": f"admin:{CANARY}@ipf.example.com"},
    ),
    **{
        f"unaccepted-setting-{key}": _RefusedInput(lambda key=key: _netbox_with_setting(key), f"source.settings.{key}")
        for key in ("api_token", "tokn", "pwd", "x")
    },
}


def _environment(example: str | None = None) -> dict[str, str]:
    """Every v2 credential variable set to the canary, plus the example's non-credential values."""
    environment = dict.fromkeys(CREDENTIAL_VARIABLES, CANARY)
    environment.update(_EXAMPLE_VARIABLES.get(example or "", {}))
    return environment


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from no v2 variable and no terminal; each test sets what it plants."""
    for name in (*V2_NON_CREDENTIAL_VARIABLES, *CREDENTIAL_VARIABLES):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)


def _result_text(result: ConversionResult) -> str:
    """Everything the command can print or write from ``result``."""
    lines = [repr(result)]
    if result.package is None:
        lines += refusal_lines("config.yml", result.refusals)
    else:
        lines += [
            yaml.safe_dump(dict(result.package), sort_keys=False),
            *report_lines("config.yml", "canary", result),
            *next_step_lines("package.yml", result),
        ]
    return "\n".join(lines)


def _convert_with_function(
    data: dict[str, Any], environment: dict[str, str], source_url: str | None = None
) -> ConversionResult:
    return convert_v2_configuration(data, visible_v2_variables(environment), source_url=source_url)


def _convert_with_command(
    tmp_path: Path, data: dict[str, Any], environment: dict[str, str], *args: str
) -> tuple[int, str, str, str]:
    """Run ``configs convert --yes``; return the exit code, stdout, stderr and the written package."""
    source = tmp_path / "config.yml"
    source.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    result = _RUNNER.invoke(app, ["configs", "convert", str(source), "--yes", *args], env=environment)
    package = tmp_path / "package.yml"
    written = package.read_text(encoding="utf-8") if package.exists() else ""
    return result.exit_code, result.stdout, result.stderr, written


def test_every_credential_setting_and_variable_is_planted() -> None:
    planted = {
        (side, setting)
        for name in CONVERTED
        for side in ("source", "destination")
        for setting, value in _with_literal_credentials(_example(name))[side]["settings"].items()
        if value == CANARY
    }

    assert ("source", "token") in planted
    assert ("destination", "token") in planted
    assert {"NETBOX_TOKEN", "INFRAHUB_API_TOKEN", "CISCO_APIC_PASSWORD", "IPF_TOKEN", "PASSWORD"} <= set(
        CREDENTIAL_VARIABLES
    )
    assert dict(visible_v2_variables(_environment())) == {}


@pytest.mark.parametrize("example", CONVERTED)
def test_function_output_never_holds_the_canary(
    example: str, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)

    result = _convert_with_function(
        _with_literal_credentials(_example(example)), _environment(example), _EXAMPLE_SOURCE_URL.get(example)
    )

    assert result.package is not None, result.refusals
    captured = capsys.readouterr()
    assert CANARY not in _result_text(result)
    assert CANARY not in captured.out + captured.err
    assert CANARY not in caplog.text


@pytest.mark.parametrize("example", CONVERTED)
def test_command_output_never_holds_the_canary(example: str, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    source_url = _EXAMPLE_SOURCE_URL.get(example)
    args = ("--source-url", source_url) if source_url else ()

    code, stdout, stderr, package = _convert_with_command(
        tmp_path, _with_literal_credentials(_example(example)), _environment(example), *args
    )

    assert code == 0, stderr
    assert package
    assert CANARY not in package
    assert CANARY not in stdout
    assert CANARY not in stderr
    assert CANARY not in caplog.text


@pytest.mark.parametrize("refused", list(REFUSED_INPUTS.values()), ids=list(REFUSED_INPUTS))
def test_refused_input_never_shows_the_canary_from_the_function(
    refused: _RefusedInput,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)

    result = _convert_with_function(refused.make(), {**_environment(), **refused.variables})

    assert result.package is None
    assert any(refusal.location == refused.location for refusal in result.refusals)
    captured = capsys.readouterr()
    assert CANARY not in _result_text(result)
    assert CANARY not in captured.out + captured.err
    assert CANARY not in caplog.text


@pytest.mark.parametrize("refused", list(REFUSED_INPUTS.values()), ids=list(REFUSED_INPUTS))
def test_refused_input_never_shows_the_canary_from_the_command(
    refused: _RefusedInput, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    code, stdout, stderr, package = _convert_with_command(
        tmp_path, refused.make(), {**_environment(), **refused.variables}
    )

    assert code == 1
    assert refused.location in stderr
    assert not package
    assert CANARY not in stdout
    assert CANARY not in stderr
    assert CANARY not in caplog.text


@pytest.mark.parametrize(
    "data",
    [
        {"format_version": 1, "configuration": {"name": "x"}, "credentials": {"token": CANARY}},
        [CANARY, {"token": CANARY}],
        CANARY,
    ],
    ids=["v3-package", "list", "scalar"],
)
def test_input_the_conversion_cannot_read_never_shows_the_canary(
    data: object, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    source = tmp_path / "config.yml"
    source.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    result = _RUNNER.invoke(app, ["configs", "convert", str(source), "--yes"], env=_environment())

    assert result.exit_code == 2
    assert "cannot be converted" in result.stderr
    assert CANARY not in result.stdout
    assert CANARY not in result.stderr
    assert CANARY not in caplog.text
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.yml"]


def test_canary_scans_detect_a_planted_copied_value(tmp_path: Path) -> None:
    data = _with_literal_credentials(_example("netbox_to_infrahub"))
    data["name"] = CANARY

    code, stdout, stderr, package = _convert_with_command(tmp_path, data, _environment())

    assert code == 0, stderr
    assert CANARY in package
    assert CANARY in stdout
