import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from invoke import Context, task

from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC"
CURRENT_DIRECTORY = Path(__file__).parent.resolve()
MAIN_DIRECTORY = "."
PYLINT_BASELINE_MAX_COUNTS = {
    "C0302": 1,
    "C0412": 1,
    "C0413": 9,
    "C0415": 5,
    "R0912": 1,
    "R0915": 1,
    "R0917": 5,
    "R1705": 1,
    "R1720": 1,
    "W0613": 4,
    "W0707": 1,
}


def _ty_check_command(python_major: int, python_minor: int) -> str:
    """Return the type-check command for the active supported runtime profile."""
    if (python_major, python_minor) == (3, 10):
        return "uv run ty check --exclude infrahub_sync/service --exclude tests/service ."
    return "uv run ty check ."


def _pylint_command(python_major: int, python_minor: int) -> str:
    """Return the Pylint command for the active supported runtime profile.

    The service tree imports optional dependencies that only install on Python
    3.11+, so the documented 3.10 profile excludes it, mirroring the ty exclusion.
    """
    if (python_major, python_minor) == (3, 10):
        return "pylint --output-format=json2 --ignore-paths='^infrahub_sync/service/' infrahub_sync/"
    return "pylint --output-format=json2 infrahub_sync/"


@task(name="lint")
def lint_all(context: Context) -> None:
    """Run all linters."""

    lint_ruff(context)
    lint_pylint(context)
    lint_yaml(context)
    lint_ty(context)

    print(f" - [{NAMESPACE}] All linters have been executed!")


@task(name="format")
def format_all(context: Context) -> None:
    """Run all formatters."""

    format_ruff(context)

    print(f" - [{NAMESPACE}] All formatters have been executed!")


# ----------------------------------------------------------------------------
# Linter tasks - Python
# ----------------------------------------------------------------------------
@task
def lint_pylint(context: Context) -> None:
    """Run Pylint and fail when diagnostics exceed the inherited baseline."""

    print(f" - [{NAMESPACE}] Check code with pylint")
    exec_cmd = _pylint_command(sys.version_info.major, sys.version_info.minor)
    with context.cd(ESCAPED_REPO_PATH):
        result = context.run(exec_cmd, hide=True, warn=True)
    if result is None:
        msg = "Pylint returned no command result"
        raise RuntimeError(msg)

    try:
        report = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        msg = "Pylint did not return a readable JSON report"
        raise RuntimeError(msg) from exc

    regressions = _pylint_regressions(report)
    if regressions:
        for regression in regressions:
            print(f" - [{NAMESPACE}] Pylint regression: {regression}")
        for location in _pylint_regression_locations(report):
            print(f" - [{NAMESPACE}]   {location}")
        msg = "Pylint diagnostics exceed the inherited baseline"
        raise RuntimeError(msg)

    messages = report.get("messages", [])
    statistics = report.get("statistics", {})
    score = statistics.get("score", "unknown") if isinstance(statistics, dict) else "unknown"
    print(f" - [{NAMESPACE}] Pylint baseline passed ({len(messages)} diagnostics, score {score})")


def _pylint_regressions(report: dict[str, Any]) -> list[str]:
    """Return diagnostic codes whose counts exceed the recorded baseline."""
    messages = report.get("messages")
    if not isinstance(messages, list):
        return ["report does not contain a messages list"]

    message_ids: list[str] = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("messageId"), str):
            return ["report contains a diagnostic without a messageId"]
        message_ids.append(message["messageId"])

    counts = Counter(message_ids)
    regressions = []
    for message_id, count in sorted(counts.items()):
        maximum = PYLINT_BASELINE_MAX_COUNTS.get(message_id)
        if maximum is None:
            regressions.append(f"new diagnostic code {message_id} ({count})")
        elif count > maximum:
            regressions.append(f"{message_id} increased from at most {maximum} to {count}")
    return regressions


def _pylint_regression_locations(report: dict[str, Any]) -> list[str]:
    """Return file:line locations for every diagnostic of a regressed code."""
    messages = report.get("messages")
    if not isinstance(messages, list):
        return []

    counts = Counter(
        message["messageId"]
        for message in messages
        if isinstance(message, dict) and isinstance(message.get("messageId"), str)
    )
    regressed = {
        message_id for message_id, count in counts.items() if count > PYLINT_BASELINE_MAX_COUNTS.get(message_id, 0)
    }

    locations = []
    for message in messages:
        if not isinstance(message, dict) or message.get("messageId") not in regressed:
            continue
        path = message.get("path") or message.get("absolutePath") or "<unknown>"
        line = message.get("line", "?")
        symbol = message.get("symbol", "")
        locations.append(f"{path}:{line}: {message['messageId']} ({symbol})")
    return sorted(locations)


@task
def lint_ruff(context: Context) -> None:
    """Run Ruff checks."""

    print(f" - [{NAMESPACE}] Check code with ruff")
    exec_cmd = f"ruff format --check --diff {MAIN_DIRECTORY} &&"
    exec_cmd += f"ruff check --diff {MAIN_DIRECTORY}"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


# ----------------------------------------------------------------------------
# Linter tasks - Yaml
# ----------------------------------------------------------------------------


@task
def lint_yaml(context: Context) -> None:
    """Run yamllint to validate all YAML files."""

    print(f" - [{NAMESPACE}] Format yaml with yamllint")
    exec_cmd = f"yamllint {MAIN_DIRECTORY}"
    context.run(exec_cmd, pty=True)


@task
def lint_ty(context: Context) -> None:
    """Run ty type checker against project files."""

    print(f" - [{NAMESPACE}] Check code with ty")
    exec_cmd = _ty_check_command(sys.version_info.major, sys.version_info.minor)

    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


# ----------------------------------------------------------------------------
# Formatting tasks - Python
# ----------------------------------------------------------------------------
@task
def format_ruff(context: Context) -> None:
    """Run Ruff formatting and safe fixes."""

    print(f" - [{NAMESPACE}] Check code with ruff")
    exec_cmd = f"ruff format {MAIN_DIRECTORY} && "
    exec_cmd += f"ruff check --fix {MAIN_DIRECTORY}"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)
