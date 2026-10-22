"""The text lines ``configs convert`` prints for a ``ConversionResult``.

The layout follows ``contracts/convert-command.md`` of spec 012: the report on standard output,
the refusals on standard error, and the next steps after the write. These functions return
lines and print nothing; the command escapes each line for the terminal and prints it. No line
holds a credential value, because no report entry holds one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .conversion import ConversionResult, CredentialEntry, RefusalEntry, SettingOriginEntry

_LOCATION_COLUMN = 28
_REFUSAL_LOCATION_COLUMN = 23
_REFUSAL_VALUE_COLUMN = 35


def _row(location: str, text: str) -> str:
    return f"  {location:<{_LOCATION_COLUMN}} {text}"


def _credential_origin(entry: CredentialEntry) -> str:
    if entry.origin == "literal":
        return "a literal value in config.yml"
    if entry.origin == "placeholder":
        return f"${{{entry.origin_variable}}} in config.yml"
    if entry.origin == "variable":
        return f"{entry.origin_variable} (v2)"
    return "no v2 value"


def _setting_origin(entry: SettingOriginEntry, width: int) -> str:
    origin = entry.origin_variable if entry.origin == "variable" else entry.origin
    text = f"{entry.value:<{width}}   from {origin}"
    if entry.other_value is not None:
        holder = "config.yml" if entry.origin == "variable" else "a v2 variable"
        text += f" ({holder} had {entry.other_value})"
    return text


def _credential_section(result: ConversionResult) -> list[str]:
    if not result.credentials:
        return []
    lines = ["", "Credentials (set these in the Sync deployment's .env file):"]
    lines.extend(
        _row(entry.location, f"{_credential_origin(entry)} -> {entry.identifier}") for entry in result.credentials
    )
    lines.extend(f"  warning: {warning}" for entry in result.credentials for warning in entry.warnings)
    return lines


def _setting_section(result: ConversionResult) -> list[str]:
    visible = result.visible_variables.names if result.visible_variables is not None else ()
    if not result.setting_origins and not visible:
        return []
    width = max((len(entry.value) for entry in result.setting_origins), default=0)
    lines = ["", "Settings taken from the environment or the file:"]
    lines.extend(_row(entry.location, _setting_origin(entry, width)) for entry in result.setting_origins)
    lines.extend(f"  warning: {warning}" for entry in result.setting_origins for warning in entry.warnings)
    if visible:
        lines.append(f"  v2 variables visible here: {', '.join(visible)}")
    return lines


def report_lines(input_name: str, configuration_name: str, result: ConversionResult) -> list[str]:
    """Return the report in the contract's section order; a section with no entry is left out.

    The sections are credentials, settings, dropped keys, notes, then the warning findings of
    the registration checks.
    """
    lines = [f'Converting {input_name} (v1 or v2 configuration "{configuration_name}")']
    lines.extend(_credential_section(result))
    lines.extend(_setting_section(result))
    if result.dropped:
        lines.extend(["", "Dropped:"])
        lines.extend(_row(entry.location, entry.reason) for entry in result.dropped)
    if result.notes:
        lines.extend(["", "Notes:"])
        lines.extend(f"  {entry.text}" for entry in result.notes)
    if result.findings:
        lines.extend(["", "Warnings from the package checks:"])
        lines.extend(_row(entry.location or "/", f"{entry.code}: {entry.message}") for entry in result.findings)
    return lines


def refusal_lines(input_name: str, refusals: tuple[RefusalEntry, ...]) -> list[str]:
    """Return every refusal in the contract's layout; a credential setting has an empty value column."""
    count = len(refusals)
    lines = [f"error: {input_name} cannot be converted ({count} reason{'' if count == 1 else 's'})"]
    for entry in refusals:
        value = entry.value or ""
        lines.append(f"  {entry.location:<{_REFUSAL_LOCATION_COLUMN}} {value:<{_REFUSAL_VALUE_COLUMN}} {entry.reason}")
    return lines


def _join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def next_step_lines(output: str, result: ConversionResult) -> list[str]:
    """Return the numbered next steps printed after the package is written."""
    identifiers = list(dict.fromkeys(entry.identifier for entry in result.credentials))
    steps: list[str] = []
    if identifiers:
        steps.extend(
            (
                f"Set {_join_names(identifiers)} in .env",
                "Restart the deployment so the Sync worker receives them",
            )
        )
    steps.append(f"infrahub-sync configs register {output}")
    return ["", "Next steps:", *(f"  {number}. {step}" for number, step in enumerate(steps, start=1))]
