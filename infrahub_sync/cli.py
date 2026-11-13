"""Command-line client for the Sync HTTP API."""

# One module keeps every command on the one Typer app and its shared client boundary.
# pylint: disable=too-many-lines

from __future__ import annotations

import json
import logging
import os
import sys
import unicodedata
from contextlib import contextmanager
from enum import Enum
from functools import wraps
from math import isfinite
from pathlib import Path  # noqa: TC003 - Typer resolves command annotations at runtime.
from time import sleep
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import uuid4

import typer
import yaml
from pydantic import ValidationError

from infrahub_sync.client import (
    APIError,
    ApplyRunRequest,
    ApproveRunRequest,
    CancelRunRequest,
    ClientInputError,
    CompatibilityError,
    ConfigMutationRequest,
    ConfigsAPIError,
    CreateRunRequest,
    PlanResource,
    ProtocolError,
    RegisteredConfigurationResource,
    RegisteredVersionResource,
    ResumeRunRequest,
    RunResource,
    RunTerminalError,
    RunWaitTimeoutError,
    SyncClient,
    SyncClientError,
    TransportError,
    ValidationReportResource,
    VerifyRunRequest,
)
from infrahub_sync.client.models import (
    LOG_STAGES,
    MAX_FAILURE_CAUSE_TEXT,
    MAX_LIMIT,
    MAX_REPORT_LIMIT,
    RECOVERY_NEXT_ACTIONS,
    SCHEMA_CHANGED_NEXT_ACTION,
    ListPage,
    StageFailureResource,
    stage_failure_resource,
)
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_MESSAGE, UNSUPPORTED_STORE_REASON

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from infrahub_sync.client.models import (
        ArtifactReferenceResource,
        ConfigurationSummaryResource,
        ConfigurationVersionReferenceResource,
        ConfigurationVersionResource,
        ConfigurationVersionSummaryResource,
        EventResource,
        PlanLastChangeResource,
        PlanOperationResource,
        PlanReportPageResource,
        PublicRunResource,
        RecoveryAction,
        RunLogLine,
        RunLogPage,
        RunReference,
        SideRead,
    )

_Resource = TypeVar("_Resource")

DEFAULT_WAIT_TIMEOUT = 30 * 60.0
DEFAULT_POLL_INTERVAL = 2.0
VERBOSITY_MAP = {"quiet": logging.WARNING, "default": logging.INFO, "verbose": logging.DEBUG}
# Shown in the `runs plan` summary when the saved plan names an owner: the import never
# changes an existing owner, so the owner setting covers only the values a run creates.
OWNER_SCOPE_NOTICE = "The owner setting applies only to values this run creates."
# Shown for an `updated_by` or `updated_at` Infrahub holds no value for.
NOT_RECORDED = "not recorded"
_REQUEST_ARG = "request"
_PACKAGE_ARG = "package"
_KIND_ARG = "kind"
_TYPE_ARG = "type"
_VERSION_ARG = "version"
_DIFF_DEPRECATION = "warning: 'diff' is deprecated, use 'plan'"
_LIMIT_HELP = f"Items per page, 1-{MAX_LIMIT}. The server default applies when omitted."
_ALL_HELP = "Read every page instead of only the first."
_CONFIG_HELP = "Configuration ID or name. The ID is tried first, then the name."
# Characters a terminal acts on or a reader cannot see: every code point in the Unicode
# categories Cc (C0 and C1 controls, DEL), Cf (bidirectional overrides and isolates,
# zero-width characters, direction marks, soft hyphen, tag characters), Zl and Zp (line
# and paragraph separators). Each is rendered as a visible escape so a value from a source
# or the server cannot move the cursor, erase a line, hide text, or show a forged one.
_ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_READABLE_ESCAPES = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}

app = typer.Typer(help="Synchronize registered configurations through the Sync API.")
configs_app = typer.Typer(help="Register and inspect configuration packages.")
runs_app = typer.Typer(help="Inspect service-owned runs.")
app.add_typer(configs_app, name="configs")
app.add_typer(runs_app, name="runs")


class Verbosity(str, Enum):
    """Supported package logging levels for CLI calls."""

    quiet = "quiet"
    default = "default"
    verbose = "verbose"


def _setup_logging(level: int = logging.INFO) -> None:
    """Configure package logging for one CLI invocation."""
    package_logger = logging.getLogger("infrahub_sync")
    package_logger.setLevel(level)
    if not package_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))
        package_logger.addHandler(handler)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    api_url: str | None = typer.Option(
        None,
        "--api-url",
        envvar="INFRAHUB_SYNC_API_URL",
        help="Absolute URL of the Sync API. Defaults to INFRAHUB_SYNC_API_URL.",
    ),
    verbosity: Verbosity = typer.Option(Verbosity.default, "--verbosity", help="Log verbosity level."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Shorthand for --verbosity verbose."),  # noqa: FBT003
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Shorthand for --verbosity quiet."),  # noqa: FBT003
) -> None:
    """Synchronize registered configurations through the Sync API."""
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = VERBOSITY_MAP[verbosity.value]
    _setup_logging(level=level)
    ctx.ensure_object(dict)
    ctx.obj["api_url"] = api_url or ""
    ctx.obj["verbosity"] = level
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def _client(ctx: typer.Context) -> SyncClient:
    """Return the injected client or construct the command's one shared HTTP boundary."""
    ctx.ensure_object(dict)
    injected = ctx.obj.get("client")
    if injected is not None:
        return cast("SyncClient", injected)
    client = SyncClient(
        cast("str", ctx.obj.get("api_url", "")),
        os.environ.get("INFRAHUB_SYNC_API_TOKEN", ""),
    )
    ctx.obj["client"] = client
    ctx.call_on_close(client.close)
    return client


def _escape_character(character: str) -> str:
    if character in _READABLE_ESCAPES:
        return _READABLE_ESCAPES[character]
    if unicodedata.category(character) not in _ESCAPED_CATEGORIES:
        return character
    code = ord(character)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def _display(value: object) -> str:
    """Render a value for a terminal with its control and invisible characters made visible.

    Only the display changes: the saved plan and every JSON rendering keep the real value.
    """
    text = str(value)
    if text.isascii() and text.isprintable():
        return text
    return "".join(map(_escape_character, text))


def _echo_fields(fields: tuple[tuple[str, object], ...], *, err: bool = False) -> None:
    for name, value in fields:
        if value is None:
            rendered = "<none>"
        elif isinstance(value, tuple):
            rendered = ",".join(str(item) for item in value)
        elif isinstance(value, bool):
            rendered = str(value).lower()
        else:
            rendered = str(value)
        typer.echo(f"{name}: {_display(rendered)}", err=err)


_CLIENT_INPUT_HINTS = {
    "service_url": "set INFRAHUB_SYNC_API_URL or pass --api-url to an absolute http or https URL of the Sync API",
    "token": "set INFRAHUB_SYNC_API_TOKEN to a Sync API bearer token",
}


def _error_fields(error: SyncClientError) -> tuple[str, tuple[tuple[str, object], ...]]:
    """Render bounded error fields and the fixed store-refusal and address guidance."""
    if isinstance(error, ClientInputError):
        fields: tuple[tuple[str, object], ...] = (("argument", error.argument),)
        if error.argument in _CLIENT_INPUT_HINTS:
            fields = (*fields, ("hint", _CLIENT_INPUT_HINTS[error.argument]))
        result = "client-input", fields
    elif isinstance(error, CompatibilityError):
        result = (
            "compatibility",
            (
                ("server_version", error.server_version),
                ("api_versions", error.api_versions),
            ),
        )
    elif isinstance(error, ConfigsAPIError):
        result = (
            "configs-api",
            (
                ("status", error.status),
                ("code", error.code),
                ("family", error.family),
                ("reason", error.reason),
                ("mutation_id", error.mutation_id),
            ),
        )
    elif isinstance(error, APIError):
        result = (
            "api",
            (
                ("status", error.status),
                ("code", error.code),
                ("run_id", error.run_id),
                ("mutation_id", error.mutation_id),
            ),
        )
    elif isinstance(error, RunWaitTimeoutError):
        result = (
            "run-wait-timeout",
            (
                ("run_id", error.run_id),
                ("phase", error.phase),
                ("outcome", error.outcome),
                ("execution_state", error.execution_state),
            ),
        )
    elif isinstance(error, RunTerminalError):
        result = (
            "run-terminal",
            (
                ("run_id", error.run_id),
                ("terminal_state", error.terminal_state),
                ("terminal_outcome", error.terminal_outcome),
                ("phase", error.phase),
                ("outcome", error.outcome),
            ),
        )
    elif isinstance(error, ProtocolError):
        result = "protocol", (("operation", error.operation), ("status", error.status))
    elif isinstance(error, TransportError):
        result = "transport", (("operation", error.operation),)
    else:
        result = "sync-client", ()
    if (isinstance(error, ConfigsAPIError) and error.reason == UNSUPPORTED_STORE_REASON) or (
        isinstance(error, APIError) and error.code == UNSUPPORTED_STORE_REASON
    ):
        result = (result[0], (*result[1], ("message", UNSUPPORTED_STORE_MESSAGE)))
    return result


@contextmanager
def _client_errors() -> Iterator[None]:
    """Map the closed client error taxonomy to stable CLI exit behavior."""
    try:
        yield
    except SyncClientError as error:
        label, fields = _error_fields(error)
        typer.echo(f"error: {label}", err=True)
        _echo_fields(fields, err=True)
        raise typer.Exit(2 if isinstance(error, ClientInputError) else 1) from None


def _request(model: type[Any], **values: object) -> Any:
    try:
        return model(**values)
    except ValidationError:
        raise ClientInputError(_REQUEST_ARG) from None


def _package(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ClientInputError(_PACKAGE_ARG) from None
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ClientInputError(_PACKAGE_ARG)
    return cast("dict[str, Any]", value)


def _idempotency_key(explicit: str | None) -> str:
    return explicit if explicit is not None else uuid4().hex


def _positive_duration(value: float, argument: str) -> None:
    if not isfinite(value) or value <= 0:
        raise ClientInputError(argument)


def _echo_idempotency_key(key: str) -> None:
    # The key is emitted before I/O so an uncertain transport result can be retried deliberately.
    _echo_fields((("idempotency_key", key),))


def _stdin_is_interactive() -> bool:
    """Report whether a person can answer a confirmation prompt on standard input."""
    return sys.stdin.isatty()


def _is_not_found(error: SyncClientError) -> bool:
    return isinstance(error, APIError) and error.status == 404


def _resolve_config(
    client: SyncClient, value: str, *, not_found_hint: str | None = None
) -> ConfigurationSummaryResource:
    """Resolve a configuration ID or name, trying the ID first.

    A value that is both one configuration's ID and another's name resolves to the ID.
    ``not_found_hint`` is printed after the error when nothing matches.
    """
    try:
        return client.get_config(value)
    except ClientInputError:
        # The value cannot be an identifier, so it can only be a name.
        pass
    except APIError as error:
        if not _is_not_found(error):
            raise
    matches = list(client.iter_configs(name=value))
    if len(matches) == 1:
        return matches[0]
    if matches:
        typer.echo(_display(f"error: name '{value}' matches several configurations:"), err=True)
        for match in matches:
            typer.echo(_display(match.config_id), err=True)
    else:
        typer.echo(_display(f"error: no configuration has the id or name '{value}'"), err=True)
        if not_found_hint is not None:
            typer.echo(_display(not_found_hint), err=True)
    raise typer.Exit(2)


def _config_id(ctx: typer.Context, value: str) -> str:
    return _resolve_config(_client(ctx), value).config_id


def _echo_more(noun: str, next_cursor: str | None) -> None:
    # On stderr, so the rows on stdout stay the complete answer to read or parse.
    if next_cursor is not None:
        typer.echo(f"more {noun}: rerun with --all, or --limit up to {MAX_LIMIT}", err=True)


def _echo_list(
    noun: str,
    *,
    all_pages: bool,
    every_item: Callable[[], Iterable[_Resource]],
    first_page: Callable[[], ListPage[_Resource]],
    write: Callable[[Iterable[_Resource]], None],
) -> None:
    """Write every item with ``--all``, else the first page and a note when more follow."""
    if all_pages:
        write(every_item())
        return
    page = first_page()
    write(page.items)
    _echo_more(noun, page.next_cursor)


def _echo_rows(resources: Iterable[_Resource], row: Callable[[_Resource], str]) -> None:
    for resource in resources:
        typer.echo(row(resource))


def _echo_blocks(resources: Iterable[_Resource], echo: Callable[[_Resource], None]) -> None:
    for index, resource in enumerate(resources):
        if index:
            typer.echo("")
        echo(resource)


def _echo_config_summary(resource: ConfigurationSummaryResource) -> None:
    _echo_fields(
        (
            ("config_id", resource.config_id),
            ("name", resource.name),
            ("latest_version", resource.latest_version),
            ("created_at", resource.created_at.isoformat()),
        )
    )


def _run_reference(reference: RunReference | None, *, applied: bool = False) -> str | None:
    """Describe one run reference; an applied run shows when its apply finished, not when it was planned."""
    if reference is None:
        return None
    if applied:
        when = "-" if reference.finished_at is None else reference.finished_at.isoformat()
        timestamp = f"finished_at={when}"
    else:
        timestamp = f"started_at={reference.started_at.isoformat()}"
    return f"{reference.run_id} phase={reference.phase} outcome={reference.outcome or '-'} {timestamp}"


def _echo_config_version_summary(resource: ConfigurationVersionSummaryResource) -> None:
    _echo_fields(
        (
            ("config_id", resource.config_id),
            ("registry_version", resource.registry_version),
            ("package_checksum", resource.package_checksum),
            ("created_at", resource.created_at.isoformat()),
            ("registered_by", resource.registered_by),
            ("registration_reason", resource.registration_reason),
            ("latest_run", _run_reference(resource.latest_run)),
            ("latest_applied_run", _run_reference(resource.latest_applied_run, applied=True)),
        )
    )


def _echo_config_version_reference(
    resource: ConfigurationVersionReferenceResource | ConfigurationVersionResource,
) -> None:
    _echo_fields(
        (
            ("config_id", resource.config_id),
            ("registry_version", resource.registry_version),
            ("package_checksum", resource.package_checksum),
            ("created_at", resource.created_at.isoformat()),
        )
    )


def _echo_config_version(resource: ConfigurationVersionResource) -> None:
    _echo_config_version_reference(resource)
    _echo_fields((("declared_content", json.dumps(resource.declared_content, sort_keys=True, separators=(",", ":"))),))


def _echo_registered_config(resource: RegisteredConfigurationResource) -> None:
    # The registration response names the version by checksum only (spec 019 FR-038).
    _echo_config_summary(resource.configuration)
    _echo_config_version_reference(resource.version)


def _echo_registered_version(resource: RegisteredVersionResource) -> None:
    _echo_config_version_reference(resource.version)
    _echo_fields((("created", resource.created),))


def _echo_validation(resource: ValidationReportResource) -> None:
    _echo_fields(
        (
            ("config_id", resource.config_id),
            ("registry_version", resource.registry_version),
            ("package_checksum", resource.package_checksum),
            ("destination_schema_fingerprint", resource.destination_schema_fingerprint),
            ("offset", resource.offset),
            ("limit", resource.limit),
            ("total_findings", resource.total_findings),
            ("next_offset", resource.next_offset),
        )
    )
    for finding in resource.findings:
        typer.echo(
            _display(
                "finding: "
                f"code={finding.code} severity={finding.severity} location={finding.location} message={finding.message}"
            )
        )


@configs_app.command("register")
def configs_register(
    ctx: typer.Context,
    package: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the mutation."),
    idempotency_key: str | None = typer.Option(
        None,
        "--idempotency-key",
        help="Retry key. A new key is generated and printed when omitted.",
    ),
) -> None:
    """Register a JSON or YAML configuration package."""
    with _client_errors():
        key = _idempotency_key(idempotency_key)
        _echo_idempotency_key(key)
        request = cast(
            "ConfigMutationRequest",
            _request(ConfigMutationRequest, package=_package(package), reason=reason),
        )
        _echo_registered_config(_client(ctx).register_config(request, key))


@configs_app.command("version")
def configs_version(
    ctx: typer.Context,
    config: str = typer.Argument(..., metavar="CONFIG", help=_CONFIG_HELP),
    package: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the mutation."),
    idempotency_key: str | None = typer.Option(
        None,
        "--idempotency-key",
        help=(
            "Retry key. A new key is generated and printed when omitted. Retry with the printed "
            "configuration ID: the name follows the newest version, so it can change."
        ),
    ),
) -> None:
    """Create the next immutable version of a registered configuration."""
    with _client_errors():
        request = cast(
            "ConfigMutationRequest",
            _request(ConfigMutationRequest, package=_package(package), reason=reason),
        )
        # The name follows the newest version's package, so a first attempt that registered a
        # package with a new name leaves the old name unresolvable. The config_id is printed
        # with the key, before the mutation, so a retry can name the configuration by it.
        hint = (
            None
            if idempotency_key is None
            else "hint: retry with the config_id printed by the first attempt; "
            "the name follows the newest version and can change"
        )
        config_id = _resolve_config(_client(ctx), config, not_found_hint=hint).config_id
        key = _idempotency_key(idempotency_key)
        _echo_fields((("config_id", config_id),))
        _echo_idempotency_key(key)
        _echo_registered_version(_client(ctx).create_config_version(config_id, request, key))


@configs_app.command("list")
def configs_list(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", help="Only configurations with this exact name."),
    limit: int | None = typer.Option(None, "--limit", min=1, max=MAX_LIMIT, help=_LIMIT_HELP),
    all_pages: bool = typer.Option(False, "--all", help=_ALL_HELP),  # noqa: FBT003
) -> None:
    """List registered configurations, newest first."""
    with _client_errors():
        client = _client(ctx)
        _echo_list(
            "configurations",
            all_pages=all_pages,
            every_item=lambda: client.iter_configs(name=name, limit=limit),
            first_page=lambda: client.list_configs(name=name, limit=limit),
            write=lambda items: _echo_blocks(items, _echo_config_summary),
        )


@configs_app.command("show")
def configs_show(
    ctx: typer.Context,
    config: str = typer.Argument(..., metavar="CONFIG", help=_CONFIG_HELP),
    version: int | None = typer.Option(None, "--version", help="Show one immutable registry version."),
) -> None:
    """Show a configuration or one of its versions."""
    with _client_errors():
        resource = _resolve_config(_client(ctx), config)
        if version is None:
            _echo_config_summary(resource)
        else:
            _echo_config_version(_client(ctx).get_config_version(resource.config_id, version))


@configs_app.command("versions")
def configs_versions(
    ctx: typer.Context,
    config: str = typer.Argument(..., metavar="CONFIG", help=_CONFIG_HELP),
    limit: int | None = typer.Option(None, "--limit", min=1, max=MAX_LIMIT, help=_LIMIT_HELP),
    all_pages: bool = typer.Option(False, "--all", help=_ALL_HELP),  # noqa: FBT003
) -> None:
    """List a configuration's versions, newest first, with each version's latest run and latest applied run."""
    with _client_errors():
        client = _client(ctx)
        config_id = _config_id(ctx, config)
        _echo_list(
            "versions",
            all_pages=all_pages,
            every_item=lambda: client.iter_config_versions(config_id, limit=limit),
            first_page=lambda: client.list_config_versions(config_id, limit=limit),
            write=lambda items: _echo_blocks(items, _echo_config_version_summary),
        )


@configs_app.command("validate")
def configs_validate(
    ctx: typer.Context,
    config: str = typer.Argument(..., metavar="CONFIG", help=_CONFIG_HELP),
    version: int = typer.Argument(...),
    *,
    offset: int = typer.Option(0, "--offset", help="Finding offset."),
    limit: int = typer.Option(256, "--limit", help="Maximum findings to return."),
    branch: str | None = typer.Option(
        None,
        "--branch",
        help=(
            "Also check the mapping, the import account's permissions and the workers' credential "
            "variables against this Infrahub branch. Exits 1 when a finding is an error."
        ),
    ),
) -> None:
    """Validate a registered configuration version."""
    with _client_errors():
        config_id = _config_id(ctx, config)
        if branch is None:
            _echo_validation(_client(ctx).validate_config(config_id, version, offset=offset, limit=limit))
            return
        report = _client(ctx).validate_config(config_id, version, offset=offset, limit=limit, branch=branch)
        _echo_validation(report)
    # contracts/cli.md (spec 019): with a branch, an error finding on the page exits 1.
    if any(finding.severity == "error" for finding in report.findings):
        raise typer.Exit(code=1)


def _echo_run(resource: RunResource) -> None:
    run = resource.run
    selected = resource.orchestration[-1] if resource.orchestration else None
    _echo_fields(
        (
            ("run_id", run.run_id),
            ("operation", run.operation),
            ("config_id", run.config_id),
            ("registry_version", run.registry_version),
            ("package_checksum", run.package_checksum),
            ("phase", run.phase),
            ("outcome", run.outcome),
            # Printed for both values. Absent when false, an operator could not
            # tell a run that needs no reconciling from one this command does
            # not report on, and the only way to settle that is the HTTP API.
            ("reconciliation_required", run.reconciliation_required),
            # A durable verdict outranks the last provider observation, which is
            # frozen once an execution is terminal.
            ("execution_state", (selected.terminal_state or selected.state) if selected is not None else None),
            # The correlation an operator follows into Prefect, from the same
            # selected attempt the state above is read from.
            ("flow_run_id", selected.flow_run_id if selected is not None else None),
            # The same flow run's page in the Prefect UI, when the Sync API knows where that UI is.
            ("prefect_flow_run_url", selected.prefect_flow_run_url if selected is not None else None),
        )
    )
    # Printed only for an approved run: an unapproved run has no approval lines at all.
    if run.approval is not None:
        _echo_fields(
            (
                ("approved_by", run.approval.approved_by),
                ("approved_at", run.approval.approved_at.isoformat()),
                ("approved_checksum", run.approval.plan_checksum),
            )
        )


def _echo_reads(resource: RunResource) -> None:
    """Print each side's read state for `runs show`.

    `verify` and `apply` run as later executions of the same run and read no side, so the
    read state comes from the latest execution that reads, not from the selected one.
    """
    reading = next((entry for entry in reversed(resource.orchestration) if entry.purpose in {"plan", "sync"}), None)
    if reading is not None and reading.reads is not None:
        _echo_fields(
            (
                ("source_read", _side_read_text(reading.reads.source)),
                ("destination_read", _side_read_text(reading.reads.destination)),
            )
        )


def _echo_apply_progress(resource: RunResource) -> None:
    """Print a write's progress, whether the run can be resumed, each kind's counts and its resumes.

    Nothing is printed for a run that never started a write (spec 022 contracts/cli.md). The
    lines hold positions, counts, kinds, actors and times only, never a value from the plan.
    """
    progress = resource.apply_progress
    if progress is None:
        return
    saved = f"saved {progress.saved_at.isoformat()}" if progress.saved_at is not None else "no position saved"
    if progress.by_kind:
        committed = sum(entry.committed for entry in progress.by_kind)
        total = sum(entry.committed + entry.uncertain + entry.not_reached for entry in progress.by_kind)
        typer.echo(_display(f"progress: {committed}/{total} operations committed ({saved})"))
    else:
        # The saved plan cannot be read, so only the saved position is known.
        position = "<none>" if progress.position is None else progress.position
        typer.echo(_display(f"progress: last committed position {position} ({saved})"))
    if not progress.resumable:
        resumable = "no"
    elif progress.verify_required:
        resumable = "yes (verify required)"
    else:
        resumable = "yes"
    typer.echo(f"resumable: {resumable}")
    for entry in progress.by_kind:
        typer.echo(
            _display(
                f"kind {entry.kind}: committed {entry.committed}, uncertain {entry.uncertain}, "
                f"not reached {entry.not_reached}"
            )
        )
    for number, resume in enumerate(resource.resumes, start=1):
        start = "from the first operation" if resume.start_after is None else f"after {resume.start_after}"
        outcome = resume.outcome or "unfinished"
        wrote = "" if resume.operations_written is None else f", wrote {resume.operations_written} operations"
        typer.echo(
            _display(f"resume {number}: {resume.actor} {resume.requested_at.isoformat()} {start} {outcome}{wrote}")
        )


def _side_read_text(side: SideRead | None) -> str | None:
    """Render one side's read state as `key=value` pairs, leaving out the keys with no value."""
    if side is None:
        return None
    pairs = (
        ("state", side.state),
        ("mode", side.read_mode),
        ("started_at", side.started_at.isoformat() if side.started_at is not None else None),
        ("ended_at", side.ended_at.isoformat() if side.ended_at is not None else None),
        ("nodes", ",".join(f"{kind}:{count}" for kind, count in side.nodes_by_kind.items()) or None),
        ("error", side.error_class),
    )
    return " ".join(f"{key}={value}" for key, value in pairs if value is not None)


def _wait(
    client: SyncClient,
    accepted: RunResource,
    *,
    wait: bool,
    wait_timeout: float,
    poll_interval: float,
) -> RunResource:
    if not wait:
        return accepted
    latest = accepted

    def observe(resource: RunResource) -> None:
        nonlocal latest
        latest = resource

    try:
        return client.wait_for_run(
            accepted,
            timeout=wait_timeout,
            poll_interval=poll_interval,
            on_observation=observe,
        )
    except TransportError:
        _echo_run(latest)
        raise
    except KeyboardInterrupt:
        typer.echo("interrupted: local wait stopped; the remote run was not cancelled", err=True)
        _echo_run(latest)
        raise typer.Exit(130) from None


def _operation_request(
    *,
    operation: str,
    config_id: str,
    version: int,
    branch: str | None,
    reason: str | None,
) -> CreateRunRequest:
    return cast(
        "CreateRunRequest",
        _request(
            CreateRunRequest,
            operation=operation,
            config_id=config_id,
            registry_version=version,
            branch=branch,
            confirm_writes=operation == "sync",
            reason=reason,
        ),
    )


def _echo_counts(name: str, counts: Mapping[str, int]) -> None:
    typer.echo(_display(f"{name}: " + ", ".join(f"{key}={value}" for key, value in counts.items())))


def _owner_scope_disclosure(plan: PlanResource) -> None:
    """Say that a named owner covers only the values this run creates, never an existing owner."""
    if plan.lineage is not None and plan.lineage.owner is not None:
        typer.echo(OWNER_SCOPE_NOTICE)


def _delete_disclosure(plan: PlanResource) -> None:
    if not plan.summary.delete_operations_computed:
        typer.echo("Delete operations were NOT computed for this plan; the review may omit destination deletes.")
    deletes = plan.summary.deletes_not_executed
    if deletes:
        typer.echo(f"{deletes} delete operation(s) are recorded and NONE will be executed by apply.")
    else:
        typer.echo("0 delete operations are recorded; apply has no deletes to skip.")


def _identity_text(identity: Mapping[str, Any]) -> str:
    return " ".join(f"{key}={identity[key]}" for key in sorted(identity))


def _last_change_line(record: PlanLastChangeResource) -> str:
    """Render who last changed one field and when, or "not recorded" for a missing value.

    An account Infrahub holds without a display label is shown by its id, because a value
    Infrahub holds is never shown as "not recorded". Labels come from Infrahub, so they are escaped.
    """
    account = record.updated_by
    updated_by = NOT_RECORDED if account is None else (account.display_label or account.id)
    updated_at = record.updated_at or NOT_RECORDED
    return _display(
        f"  last change: {record.field} ({record.field_type}): updated_by={updated_by}, updated_at={updated_at}"
    )


def _operation_detail(operation: PlanOperationResource, last_changes: Sequence[PlanLastChangeResource] = ()) -> None:
    marker = " (not executed)" if operation.action == "delete" else ""
    # Identity values come from the source, so they are escaped before a reviewer reads them.
    typer.echo(
        _display(
            f"{operation.operation_id} {operation.action} {operation.kind} {_identity_text(operation.identity)}{marker}"
        )
    )
    # Only an update carries one, and it is what the apply will key that write by, so a
    # reviewer sees which destination object the operation names before approving it.
    if operation.destination_id is not None:
        typer.echo(f"  destination id: {_display(operation.destination_id)}")
    payload = operation.payload
    if payload is not None:
        typer.echo(f"  payload: {json.dumps(payload, sort_keys=True, separators=(',', ':'))}")
    relationships = operation.relationships
    if relationships:
        typer.echo(f"  relationships: {json.dumps(relationships, sort_keys=True, separators=(',', ':'))}")
    for record in last_changes:
        typer.echo(_last_change_line(record))


def _report_value(value: object) -> str:
    """One identity value of a report record: a peer reference shows its own identity values."""
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        identity = mapping.get("identity")
        inner = cast("dict[str, object]", identity) if isinstance(identity, dict) else mapping
        return ",".join(_report_value(inner[key]) for key in sorted(inner))
    if isinstance(value, list | tuple):
        return ",".join(_report_value(item) for item in value)
    return str(value)


def _report_record(record: Mapping[str, Any] | None) -> str:
    """A report record by its identity, or by its source ID when its identity is not known."""
    if not record:
        return "<none>"
    identity = record.get("identity")
    if isinstance(identity, dict) and identity:
        return " ".join(f"{key}={_report_value(identity[key])}" for key in sorted(identity))
    return f"source_id={record.get('source_id')}"


def _report_line(first: object, record: Mapping[str, Any], text: str) -> str:
    """One line of a problem, left-out or kept list: four columns separated by two spaces (contracts/cli.md)."""
    return _display(f"{first}  {record.get('kind')}  {_report_record(record)}  {text}")


def _problem_line(entry: Mapping[str, Any]) -> str:
    return _report_line(entry.get("problem_type"), entry.get("record") or {}, str(entry.get("reason") or ""))


def _left_out_line(entry: Mapping[str, Any]) -> str:
    if entry.get("cause") == "depends_on_left_out":
        depends_on = entry.get("depends_on") or {}
        text = f"depends on {depends_on.get('kind')} {_report_record(depends_on)} through {entry.get('relationship')}"
    else:
        text = "has " + ", ".join(str(problem_type) for problem_type in entry.get("problem_types") or ())
    return _report_line(entry.get("cause"), entry.get("record") or {}, text)


def _kept_line(entry: Mapping[str, Any]) -> str:
    peer = entry.get("left_out_peer") or {}
    if entry.get("cause") == "peer_not_found_in_source":
        named = f"{peer.get('kind')} source ID {peer.get('source_id')}, which is not in the source"
    else:
        named = f"{peer.get('kind')} {_report_record(peer)}, which the plan leaves out"
    text = f"{entry.get('relationship')} refers to {named}"
    return _report_line(entry.get("cause"), entry.get("record") or {}, text)


def _echo_report_summary(plan: PlanResource) -> None:
    """The counts of spec 019 FR-007, the kinds whose deletes are not computed, and the Dropdown check."""
    summary = plan.summary
    typer.echo(
        f"left_out: {summary.left_out} (with a problem: {summary.left_out_with_problem}, "
        f"depending on one: {summary.left_out_dependent})"
    )
    typer.echo(f"problems: {summary.problems}")
    typer.echo(f"kept_without_relationship: {summary.kept}")
    for kind, count in summary.deletes_withheld_by_kind.items():
        typer.echo(_display(f"deletes not computed for {kind}: {count} records without identity"))
    if not summary.dropdown_values_checked:
        typer.echo("Dropdown values were not checked: no destination schema snapshot in this run")


def _report_pages(
    read: Callable[..., PlanReportPageResource], run_id: str, operation: str, **filters: str | None
) -> Iterator[Mapping[str, Any]]:
    """Every entry of one report list, page after page, until `next_offset` is null."""
    given = {name: value for name, value in filters.items() if value is not None}
    offset = 0
    while True:
        page = read(run_id, offset=offset, limit=MAX_REPORT_LIMIT, **given)
        yield from page.entries
        if page.next_offset is None:
            return
        if page.next_offset <= offset:
            raise ProtocolError(operation)
        offset = page.next_offset


# How many problems `plan` prints after its summary (contracts/cli.md).
_FIRST_PROBLEMS = 20


def _echo_first_problems(client: SyncClient, plan: PlanResource) -> None:
    """Print the first problems of a new plan and the command that lists every one."""
    if plan.summary.problems <= 0:
        return
    page = client.get_plan_problems(plan.run_id, offset=0, limit=_FIRST_PROBLEMS)
    for entry in page.entries:
        typer.echo(_problem_line(entry))
    if plan.summary.problems > len(page.entries):
        typer.echo(f"List every problem with `infrahub-sync runs plan {_display(plan.run_id)} --problems`.")


def _echo_plan(plan: PlanResource, *, detail: bool = False, kind: str | None = None) -> None:
    operations = list(plan.operations)
    if kind is not None:
        if not detail:
            raise ClientInputError(_KIND_ARG)
        operations = [operation for operation in operations if operation.kind == kind]
        if not operations:
            raise ClientInputError(_KIND_ARG)
    fields: list[tuple[str, object]] = [
        ("run_id", plan.run_id),
        ("plan_checksum", plan.checksum),
        ("checksum_ok", plan.checksum_ok),
        ("checksum_source", "Sync API saved plan"),
        ("operations", plan.summary.total),
        ("destination_branch", plan.destination_branch),
        ("delete_operations_computed", plan.summary.delete_operations_computed),
    ]
    if plan.schema_fingerprint is not None:
        fields.append(("schema_fingerprint", plan.schema_fingerprint))
    _echo_fields(tuple(fields))
    for note in plan.verification_notes:
        typer.echo(f"verification_note: {_display(note)}")
    _echo_counts("by_action", plan.summary.by_action)
    _echo_report_summary(plan)
    _echo_counts("by_kind", plan.summary.by_kind)
    _delete_disclosure(plan)
    _owner_scope_disclosure(plan)
    if detail:
        # Records are grouped by operation; a plan made without them shows no last-change lines.
        last_changes: dict[str, list[PlanLastChangeResource]] = {}
        for record in plan.last_changes or ():
            last_changes.setdefault(record.operation_id, []).append(record)
        for operation in operations:
            _operation_detail(operation, last_changes.get(operation.operation_id, ()))


def _echo_completed_destination_branch(client: SyncClient, run_id: str) -> None:
    """Report a saved branch without changing the completed write's outcome."""
    try:
        plan = client.get_plan(run_id)
    except SyncClientError:
        typer.echo("destination_branch: <unavailable> (saved plan could not be read)")
        return
    _echo_fields((("destination_branch", plan.destination_branch),))


def _echo_stage_failure(client: SyncClient, run_id: str, *, stage: str) -> None:
    """Print what the service recorded about a failed stage, before the run-terminal error.

    `<stage> failed: <error type>`, then the failure type and system, `cause:` and the other
    spec 019 fields when the run recorded them (see `_echo_failure_record`), and `hint:` when it
    recorded a next action. A record without a next action, from an older Sync API, gets the fixed hint
    for its error type or recovery signal when it has one. The record is the API's, and the API
    stores only text written for the operator. Each value is still capped and printed through
    `_display`, so recorded text cannot reach the terminal as an escape sequence.
    """
    try:
        failure = client.get_results(run_id).results.get(f"{stage}_failure")
    except SyncClientError:
        return
    if isinstance(failure, dict):
        _echo_failure_record(failure, stage=stage, err=True)


def _failure_lines(failure: Mapping[str, Any], record: StageFailureResource | None) -> Iterator[str]:
    """The spec 019 lines of a failure record that follow `cause:` (contracts/cli.md), unescaped."""
    if record is None:
        return
    if record.infrahub_message:
        yield f"Infrahub message: {record.infrahub_message}"
    if record.record is not None:
        reference = record.record
        identity = _identity_text(reference.identity or {})
        source_id = f" source ID {reference.source_id}" if reference.source_id else ""
        yield f"record: {reference.kind}{' ' + identity if identity else ''}{source_id}"
    if record.field:
        yield f"field: {record.field}"
    applied = failure.get("applied_operations")
    if record.operations_total is not None and isinstance(applied, list):
        # The whole run's count, so a stopped resume includes what earlier executions wrote. A
        # record from an older Sync API has only this execution's list (spec 022 research R6).
        written = record.operations_written if record.operations_written is not None else len(applied)
        yield f"written: {written} of {record.operations_total} operations"
    detail = record.failed_operation_detail
    failed_operation = failure.get("failed_operation")
    if detail is not None and isinstance(failed_operation, str):
        identity = _identity_text(detail.identity or {})
        partial = " (may have written part of its change)" if failure.get("may_have_partially_written") is True else ""
        yield f"failed operation: {failed_operation} {detail.action} {detail.kind}{' ' + identity if identity else ''}{partial}"


def _echo_failure_record(failure: Mapping[str, Any], *, stage: str, err: bool) -> None:
    """Print one recorded `<stage>_failure`: the error type, the spec 019 fields, `cause:` and `hint:`.

    The spec 019 fields go between `<stage> failed:` and `hint:` (FR-015, contracts/cli.md): the
    failure type and its system, then the cause, Infrahub's message of a rejected write, the
    record and field a failure names, and the progress of a stopped apply. A record from an
    older Sync API has none of them and prints as before.
    """
    if failure.get("stage") != stage:
        return
    error_type = failure.get("error_type")
    if not (
        isinstance(error_type, str) and len(error_type) <= 128 and error_type.isascii() and error_type.isidentifier()
    ):
        return
    typer.echo(f"{stage} failed: {error_type}", err=err)
    record = stage_failure_resource(dict(failure))
    if record is not None and record.failure_type:
        system = f" ({record.system.role} {record.system.adapter})" if record.system is not None else ""
        typer.echo(f"failure type: {_display(f'{record.failure_type}{system}'[:MAX_FAILURE_CAUSE_TEXT])}", err=err)
    _echo_failed_checks(failure.get("failed_checks"), err=err)
    cause = failure.get("cause")
    if isinstance(cause, str) and cause:
        typer.echo(f"cause: {_display(cause[:MAX_FAILURE_CAUSE_TEXT])}", err=err)
    for line in _failure_lines(failure, record):
        typer.echo(_display(line[: MAX_FAILURE_CAUSE_TEXT * 2]), err=err)
    next_action = failure.get("next_action")
    if isinstance(next_action, str) and next_action:
        typer.echo(f"hint: {_display(next_action[:MAX_FAILURE_CAUSE_TEXT])}", err=err)
        return
    # A record written before the service recorded every next action (an older Sync API)
    # carries only the error type and, for a refused registered plan, its recovery signal.
    hint: str | None = None
    if error_type == "PlanSchemaChangedError":
        hint = SCHEMA_CHANGED_NEXT_ACTION
    elif error_type == "RegisteredPlanVerificationError":
        recovery_action = failure.get("recovery_action")
        if isinstance(recovery_action, str):
            hint = RECOVERY_NEXT_ACTIONS.get(cast("RecoveryAction", recovery_action))
    if hint is not None:
        typer.echo(f"hint: {hint}", err=err)


# The `failure_type` of a `sync` stage that stopped after a plan that leaves out records.
_LEFT_OUT_STOP_FAILURE_TYPE = "left_out_review_required"
_LEFT_OUT_CONFIRMATION_REQUIRED = "left-out-confirmation-required"
# The refusal of a stage on a run that already admitted a write, or that has an unresolved
# submission or a running execution (spec 019 FR-023 keeps the code, decision 4).
_RUN_EXECUTION_CONFLICT = "run-execution-conflict"
_VERIFY_REQUIRED = "verify-required"
_CHECKSUM_CONFLICT = "checksum-conflict"
_APPROVAL_REQUIRED = "approval-required"
_RESUME_REFUSAL_HINTS = {
    _VERIFY_REQUIRED: "run infrahub-sync verify {run_id} first",
    # An interrupted run that was never approved, a `sync` run for example, is approved like a
    # planned one (spec 022 FR-019).
    _APPROVAL_REQUIRED: "run infrahub-sync approve {run_id} --expected-checksum {expected_checksum} first",
}
# The CLI does not know here whether the run can be resumed, so the hint names the line of
# `runs show` that tells (spec 022): a resumable run continues with `verify` and `runs resume`.
_RUN_EXECUTION_CONFLICT_NEXT_ACTION = (
    "If this run already admitted a write, `apply` does not apply its plan again: review the run "
    "with `infrahub-sync runs show`. When it prints `resumable: yes`, run `infrahub-sync verify`, "
    "then `infrahub-sync runs resume`; otherwise create and review a new plan with "
    "`infrahub-sync plan`. If a stage of this run is still running, wait for it to finish."
)


def _echo_left_out_stop(failure: Mapping[str, Any], *, stage: str, err: bool) -> bool:
    """Print a stage result's `failure` for a stop that waits for the left-out review.

    `<stage> stopped: left_out_review_required (<n> records left out)`, then the `hint:` the
    worker recorded (spec 019 FR-012, contracts/cli.md). Returns whether it printed the stop.
    """
    if failure.get("failure_type") != _LEFT_OUT_STOP_FAILURE_TYPE or failure.get("stage") != stage:
        return False
    left_out = failure.get("left_out")
    count = f" ({left_out} records left out)" if type(left_out) is int else ""  # pylint: disable=unidiomatic-typecheck
    typer.echo(f"{stage} stopped: {_LEFT_OUT_STOP_FAILURE_TYPE}{count}", err=err)
    next_action = failure.get("next_action")
    if isinstance(next_action, str) and next_action:
        typer.echo(f"hint: {_display(next_action[:MAX_FAILURE_CAUSE_TEXT])}", err=err)
    return True


def _echo_stopped_stage(client: SyncClient, run_id: str, *, stage: str) -> bool:
    """Print the stop a completed stage recorded in its result's `failure`, if any."""
    try:
        failure = client.get_results(run_id).results.get("failure")
    except SyncClientError:
        return False
    return isinstance(failure, dict) and _echo_left_out_stop(failure, stage=stage, err=True)


def _left_out_refusal_count(client: SyncClient, run_id: str, error: APIError) -> int | None:
    """The count of records the refused apply's plan leaves out: the refusal's, else the plan's."""
    if error.left_out is not None:
        return error.left_out
    try:
        return client.get_plan(run_id).summary.left_out
    except SyncClientError:
        return None


def _checksum_conflict_hint(client: SyncClient, run_id: str, given: str) -> str:
    """The hint of a resume refused for its checksum: the saved plan's checksum and the one given.

    The saved checksum is read with one more request. When that request fails, the hint names
    the command that prints it, so the refusal already printed stays the error this command reports.
    """
    try:
        saved = client.get_plan(run_id).checksum
    except SyncClientError:
        return f"you gave {given}; `infrahub-sync runs plan {run_id}` prints the saved plan's checksum"
    return f"the saved plan's checksum is {saved}; you gave {given}"


def _echo_failed_checks(recorded: object, *, err: bool = True) -> None:
    """Print one `failed_check:` line per pre-write check a failed `verify` or `apply` recorded (SC-004).

    The names come from this repository's own check vocabulary; a recorded value that is not a
    short ASCII identifier is skipped, so the record cannot reach the terminal as free text.
    """
    if not isinstance(recorded, list):
        return
    for name in recorded:
        if isinstance(name, str) and len(name) <= 128 and name.isascii() and name.isidentifier():
            typer.echo(f"failed_check: {name}", err=err)


def _waiting_client(ctx: typer.Context, *, wait_timeout: float, poll_interval: float) -> SyncClient:
    """Refuse a wait duration that is not positive before any I/O, then return the client."""
    _positive_duration(wait_timeout, "wait_timeout")
    _positive_duration(poll_interval, "poll_interval")
    return _client(ctx)


def _submit_and_wait(
    client: SyncClient,
    submit: Callable[[str], RunResource],
    *,
    idempotency_key: str | None,
    wait: bool,
    wait_timeout: float,
    poll_interval: float,
) -> RunResource:
    """Print the retry key, submit the run with it, print the accepted run and wait for it."""
    key = _idempotency_key(idempotency_key)
    _echo_idempotency_key(key)
    accepted = submit(key)
    _echo_run(accepted)
    return _wait(client, accepted, wait=wait, wait_timeout=wait_timeout, poll_interval=poll_interval)


def _admit_run(
    ctx: typer.Context,
    *,
    operation: str,
    config: str,
    version: int,
    branch: str | None,
    reason: str | None,
    idempotency_key: str | None,
    wait: bool,
    wait_timeout: float,
    poll_interval: float,
) -> tuple[SyncClient, RunResource]:
    client = _waiting_client(ctx, wait_timeout=wait_timeout, poll_interval=poll_interval)
    config_id = _config_id(ctx, config)

    def submit(key: str) -> RunResource:
        request = _operation_request(
            operation=operation,
            config_id=config_id,
            version=version,
            branch=branch,
            reason=reason,
        )
        return client.plan(request, key) if operation == "plan" else client.sync(request, key)

    try:
        completed = _submit_and_wait(
            client,
            submit,
            idempotency_key=idempotency_key,
            wait=wait,
            wait_timeout=wait_timeout,
            poll_interval=poll_interval,
        )
    except RunTerminalError as error:
        _echo_stage_failure(client, error.run_id, stage=operation)
        raise
    return client, completed


@app.command("plan")
def plan_cmd(
    ctx: typer.Context,
    *,
    config: str = typer.Option(..., "--config", "--config-id", help=_CONFIG_HELP),
    version: int = typer.Option(..., "--version", help="Registered configuration version."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the plan."),
    branch: str | None = typer.Option(None, "--branch", help="Destination branch admitted by the Sync API."),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="Retry key."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for this run's execution."),  # noqa: FBT003
    wait_timeout: float = typer.Option(DEFAULT_WAIT_TIMEOUT, "--wait-timeout", help="Bounded wait in seconds."),
    poll_interval: float = typer.Option(DEFAULT_POLL_INTERVAL, "--poll-interval", help="Poll interval in seconds."),
) -> None:
    """Create a plan run and review its saved summary after completion."""
    with _client_errors():
        client, completed = _admit_run(
            ctx,
            operation="plan",
            config=config,
            version=version,
            branch=branch,
            reason=reason,
            idempotency_key=idempotency_key,
            wait=wait,
            wait_timeout=wait_timeout,
            poll_interval=poll_interval,
        )
        if wait:
            plan = client.get_plan(completed.run.run_id)
            _echo_plan(plan)
            _echo_first_problems(client, plan)


@app.command("diff", hidden=True, help="Deprecated alias of `plan`; it will be removed in a later release.")
@wraps(plan_cmd)
def diff_cmd(*args: Any, **kwargs: Any) -> None:
    """Deprecated alias of `plan`; it will be removed in a later release.

    `wraps` gives Typer the signature of `plan`, so the alias takes exactly the options
    `plan` takes and cannot fall behind when `plan` gains one.
    """
    typer.echo(_DIFF_DEPRECATION, err=True)
    plan_cmd(*args, **kwargs)


@app.command("sync")
def sync_cmd(
    ctx: typer.Context,
    *,
    config: str = typer.Option(..., "--config", "--config-id", help=_CONFIG_HELP),
    version: int = typer.Option(..., "--version", help="Registered configuration version."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the synchronization."),
    branch: str | None = typer.Option(None, "--branch", help="Destination branch admitted by the Sync API."),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="Retry key."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for this run's execution."),  # noqa: FBT003
    wait_timeout: float = typer.Option(DEFAULT_WAIT_TIMEOUT, "--wait-timeout", help="Bounded wait in seconds."),
    poll_interval: float = typer.Option(DEFAULT_POLL_INTERVAL, "--poll-interval", help="Poll interval in seconds."),
) -> None:
    """Create a confirmed synchronization run."""
    with _client_errors():
        client, completed = _admit_run(
            ctx,
            operation="sync",
            config=config,
            version=version,
            branch=branch,
            reason=reason,
            idempotency_key=idempotency_key,
            wait=wait,
            wait_timeout=wait_timeout,
            poll_interval=poll_interval,
        )
        if wait:
            _echo_run(completed)
            _echo_completed_destination_branch(client, completed.run.run_id)
            # A `sync` whose plan leaves out records ends completed, without writing, and
            # waits for the team to review the plan and apply it (spec 019 FR-012).
            if completed.run.outcome == "left-out-review-required" and _echo_stopped_stage(
                client, completed.run.run_id, stage="sync"
            ):
                raise typer.Exit(1)


@app.command("apply")
def apply_cmd(
    ctx: typer.Context,
    *,
    run_id: str = typer.Argument(..., help="Service-issued run ID whose plan was reviewed."),
    expected_checksum: str = typer.Option(..., "--expected-checksum", help="Checksum printed by `runs plan`."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the apply."),
    branch: str | None = typer.Option(None, "--branch", help="Destination branch admitted by the Sync API."),
    confirm_left_out: bool = typer.Option(
        False,  # noqa: FBT003
        "--confirm-left-out",
        help="Confirm that you reviewed the records the plan leaves out; required when it leaves out any.",
    ),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="Retry key."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for this run's apply execution."),  # noqa: FBT003
    wait_timeout: float = typer.Option(DEFAULT_WAIT_TIMEOUT, "--wait-timeout", help="Bounded wait in seconds."),
    poll_interval: float = typer.Option(DEFAULT_POLL_INTERVAL, "--poll-interval", help="Poll interval in seconds."),
) -> None:
    """Apply a service-owned plan after binding its reviewed checksum."""
    with _client_errors():
        client = _waiting_client(ctx, wait_timeout=wait_timeout, poll_interval=poll_interval)

        def submit(key: str) -> RunResource:
            request = cast(
                "ApplyRunRequest",
                _request(
                    ApplyRunRequest,
                    expected_checksum=expected_checksum,
                    confirm_writes=True,
                    confirm_left_out=confirm_left_out,
                    branch=branch,
                    reason=reason,
                ),
            )
            return client.apply(run_id, request, key)

        try:
            # Only the wait raises RunTerminalError; submitting the apply cannot.
            completed = _submit_and_wait(
                client,
                submit,
                idempotency_key=idempotency_key,
                wait=wait,
                wait_timeout=wait_timeout,
                poll_interval=poll_interval,
            )
        except APIError as error:
            if error.code == _RUN_EXECUTION_CONFLICT:
                # The run already admitted a write or is still running: its plan is never
                # applied again (spec 019 FR-023). Print the refusal as for any API refusal,
                # then the next step.
                label, fields = _error_fields(error)
                typer.echo(f"error: {label}", err=True)
                _echo_fields(fields, err=True)
                typer.echo(f"hint: {_RUN_EXECUTION_CONFLICT_NEXT_ACTION}", err=True)
                raise typer.Exit(1) from None
            if error.code != _LEFT_OUT_CONFIRMATION_REQUIRED:
                raise
            # Refused before any write: the plan leaves out records (spec 019 FR-010).
            left_out = _left_out_refusal_count(client, run_id, error)
            count = "" if left_out is None else f" {left_out}"
            typer.echo(f"apply refused: this plan leaves out{count} records; confirm with --confirm-left-out", err=True)
            raise typer.Exit(1) from None
        except RunTerminalError as error:
            _echo_stage_failure(client, error.run_id, stage="apply")
            raise
        if wait:
            _echo_run(completed)
            _echo_completed_destination_branch(client, completed.run.run_id)


@app.command("approve")
def approve_cmd(
    ctx: typer.Context,
    *,
    run_id: str = typer.Argument(..., help="Service-issued run ID whose saved plan was reviewed."),
    expected_checksum: str = typer.Option(..., "--expected-checksum", help="Checksum printed by `runs plan`."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the approval."),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="Retry key."),
) -> None:
    """Record approval of the named run's saved plan without applying it.

    The run is in phase `planned`, or its write stopped part-way and it can be resumed: a site that
    requires approval then approves it before `runs resume`, for example a `sync` run.
    """
    with _client_errors():
        client = _client(ctx)
        request = cast(
            "ApproveRunRequest", _request(ApproveRunRequest, expected_checksum=expected_checksum, reason=reason)
        )
        key = _idempotency_key(idempotency_key)
        _echo_idempotency_key(key)
        # Approval completes in the request, so there is no execution to wait for.
        approved = client.approve(run_id, request, key)
        _echo_run(approved)
        _echo_fields(
            (
                ("approved_creates", approved.plan_summary.creates),
                ("approved_updates", approved.plan_summary.updates),
                ("approved_deletes_recorded", approved.plan_summary.deletes_recorded),
            )
        )


@app.command("verify")
def verify_cmd(
    ctx: typer.Context,
    *,
    run_id: str = typer.Argument(..., help="Service-issued run ID whose saved plan is verified."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the verification."),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="Retry key."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for this run's verification."),  # noqa: FBT003
    wait_timeout: float = typer.Option(DEFAULT_WAIT_TIMEOUT, "--wait-timeout", help="Bounded wait in seconds."),
    poll_interval: float = typer.Option(DEFAULT_POLL_INTERVAL, "--poll-interval", help="Poll interval in seconds."),
) -> None:
    """Check that the named run's saved plan would apply now, without writing."""
    with _client_errors():
        client = _waiting_client(ctx, wait_timeout=wait_timeout, poll_interval=poll_interval)
        try:
            # Only the wait raises RunTerminalError; submitting the verification cannot.
            completed = _submit_and_wait(
                client,
                lambda key: client.verify(
                    run_id, cast("VerifyRunRequest", _request(VerifyRunRequest, reason=reason)), key
                ),
                idempotency_key=idempotency_key,
                wait=wait,
                wait_timeout=wait_timeout,
                poll_interval=poll_interval,
            )
        except RunTerminalError as error:
            _echo_stage_failure(client, error.run_id, stage="verify")
            raise
        if wait:
            _echo_run(completed)
            _echo_verification_checks(completed)


def _echo_verification_checks(resource: RunResource) -> None:
    """Print one line per pre-write check the run's verification passed, in the order it ran them."""
    verification = resource.run.results.get("verification")
    checks = verification.get("checks") if isinstance(verification, dict) else None
    if not isinstance(checks, list):
        return
    for check in checks:
        if isinstance(check, str):
            typer.echo(_display(f"check: {check} passed"))
    if isinstance(verification, dict):
        _echo_verification_operations(verification)


def _echo_verification_operations(verification: dict[str, Any]) -> None:
    """Print the state of each uncertain operation of an interrupted run (spec 022 FR-006).

    One `operation ID KIND ACTION: STATE` line per reported operation, the count the report
    left out when it was truncated, and, whenever there is a report, the note that a resume
    writes the plan's values over every operation reported as differs.
    """
    operations = verification.get("operations")
    if not isinstance(operations, list):
        return
    for entry in operations:
        if isinstance(entry, dict):
            typer.echo(
                _display(
                    f"operation {entry.get('operation_id')} {entry.get('kind')} "
                    f"{entry.get('action')}: {entry.get('state')}"
                )
            )
    truncated = verification.get("operations_truncated")
    if isinstance(truncated, int) and not isinstance(truncated, bool) and truncated > 0:
        typer.echo(f"operations not reported: {truncated}")
    typer.echo("note: runs resume writes the plan's values over every operation reported as differs")


@app.command("whoami")
def whoami_cmd(ctx: typer.Context) -> None:
    """Show the actor the API token authenticates as and whether it is an administrator."""
    with _client_errors():
        principal = _client(ctx).me()
        _echo_fields(
            (
                ("actor", principal.actor),
                ("administrator", principal.administrator),
                ("approval_required", principal.approval_required),
            )
        )


def _run_row(run: PublicRunResource) -> str:
    configuration = f"{run.config_id}@{run.registry_version}" if run.config_id is not None else "-"
    columns = (
        run.run_id,
        configuration,
        run.destination_branch or "-",
        run.phase,
        run.outcome or "-",
        run.actor or "-",
        run.started_at.isoformat(),
    )
    return _display("  ".join(columns))


@runs_app.command("list")
def runs_list(
    ctx: typer.Context,
    config: str | None = typer.Option(None, "--config", "--config-id", help=_CONFIG_HELP),
    version: int | None = typer.Option(
        None, "--version", min=1, help="Only runs of this version; requires a configuration ID or name."
    ),
    limit: int | None = typer.Option(None, "--limit", min=1, max=MAX_LIMIT, help=_LIMIT_HELP),
    all_pages: bool = typer.Option(False, "--all", help=_ALL_HELP),  # noqa: FBT003
) -> None:
    """List runs, newest first: run, configuration@version, branch, phase, outcome, actor, start time."""
    with _client_errors():
        if version is not None and config is None:
            raise ClientInputError(_VERSION_ARG)
        client = _client(ctx)
        config_id = _config_id(ctx, config) if config is not None else None
        _echo_list(
            "runs",
            all_pages=all_pages,
            every_item=lambda: client.iter_runs(config_id=config_id, registry_version=version, limit=limit),
            first_page=lambda: client.list_runs(config_id=config_id, registry_version=version, limit=limit),
            write=lambda runs: _echo_rows(runs, _run_row),
        )


def _echo_artifact(artifact: ArtifactReferenceResource) -> None:
    _echo_fields(
        (
            ("artifact_id", artifact.artifact_id),
            ("kind", artifact.kind),
            ("media_type", artifact.media_type),
            ("size", artifact.size),
            ("digest", artifact.digest),
            ("created_at", artifact.created_at.isoformat()),
            ("expires_at", artifact.expires_at.isoformat() if artifact.expires_at is not None else None),
        )
    )


@runs_app.command("artifacts")
def runs_artifacts(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Service-issued run ID."),
) -> None:
    """List the artifacts one run recorded."""
    with _client_errors():
        _echo_blocks(_client(ctx).list_artifacts(run_id).artifacts, _echo_artifact)


@runs_app.command("cancel")
def runs_cancel(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Service-issued run ID."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the cancellation."),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="Retry key."),
    yes: bool = typer.Option(False, "--yes", help="Cancel without asking for confirmation."),  # noqa: FBT003
) -> None:
    """Cancel a run. Cancelling a finished plan withdraws it so it cannot be approved or applied;
    cancelling a write can leave its outcome ambiguous.
    """
    with _client_errors():
        if not yes:
            if not _stdin_is_interactive():
                typer.echo(
                    "error: runs cancel needs --yes when standard input is not interactive, "
                    "because cancelling a write can leave an ambiguous outcome",
                    err=True,
                )
                raise typer.Exit(2)
            if not typer.confirm(
                f"Cancel run {_display(run_id)}? A finished plan is withdrawn; "
                "cancelling a write can leave an ambiguous outcome.",
                default=False,
            ):
                typer.echo("not cancelled", err=True)
                raise typer.Exit(1)
        client = _client(ctx)
        key = _idempotency_key(idempotency_key)
        _echo_idempotency_key(key)
        request = cast("CancelRunRequest", _request(CancelRunRequest, reason=reason))
        _echo_run(client.cancel_run(run_id, request, key))


@runs_app.command("resume")
def runs_resume(
    ctx: typer.Context,
    *,
    run_id: str = typer.Argument(..., help="Service-issued run ID whose write was interrupted."),
    expected_checksum: str = typer.Option(..., "--expected-checksum", help="Checksum printed by `runs plan`."),
    confirm_left_out: bool = typer.Option(
        False,  # noqa: FBT003
        "--confirm-left-out",
        help="Confirm that you reviewed the records the plan leaves out; required when it leaves out any.",
    ),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the resume."),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="Retry key."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for this run's resume execution."),  # noqa: FBT003
    wait_timeout: float = typer.Option(DEFAULT_WAIT_TIMEOUT, "--wait-timeout", help="Bounded wait in seconds."),
    poll_interval: float = typer.Option(DEFAULT_POLL_INTERVAL, "--poll-interval", help="Poll interval in seconds."),
) -> None:
    """Resume an interrupted apply from the run's saved position, after a `verify` passed.

    The resume applies the rest of the run's saved plan in a new execution on the same run. It writes
    the plan's values over every operation that `verify` reported as differing from the plan.
    """
    with _client_errors():
        client = _waiting_client(ctx, wait_timeout=wait_timeout, poll_interval=poll_interval)
        key = _idempotency_key(idempotency_key)
        _echo_idempotency_key(key)
        request = cast(
            "ResumeRunRequest",
            _request(
                ResumeRunRequest,
                expected_checksum=expected_checksum,
                confirm_writes=True,
                confirm_left_out=confirm_left_out,
                reason=reason,
            ),
        )
        try:
            accepted = client.resume(run_id, request, key)
        except APIError as error:
            if error.code == _LEFT_OUT_CONFIRMATION_REQUIRED:
                left_out = _left_out_refusal_count(client, run_id, error)
                count = "" if left_out is None else f" {left_out}"
                typer.echo(
                    f"resume refused: this plan leaves out{count} records; confirm with --confirm-left-out", err=True
                )
                raise typer.Exit(1) from None
            template = _RESUME_REFUSAL_HINTS.get(error.code)
            hint = None if template is None else template.format(run_id=run_id, expected_checksum=expected_checksum)
            if hint is None and error.code != _CHECKSUM_CONFLICT:
                raise
            label, fields = _error_fields(error)
            typer.echo(f"error: {label}", err=True)
            _echo_fields(fields, err=True)
            if hint is None:
                # Scenario 3: the error shows the given checksum and the saved plan's checksum.
                hint = _checksum_conflict_hint(client, run_id, expected_checksum)
            typer.echo(_display(f"hint: {hint}"), err=True)
            raise typer.Exit(1) from None
        _echo_run(accepted)
        # The run's position when the resume was accepted is where the resume starts.
        position = accepted.apply_progress.position if accepted.apply_progress is not None else None
        _echo_fields((("start_after", position),))
        try:
            completed = _wait(client, accepted, wait=wait, wait_timeout=wait_timeout, poll_interval=poll_interval)
        except RunTerminalError as error:
            _echo_stage_failure(client, error.run_id, stage="resume")
            raise
        if wait:
            _echo_run(completed)
            _echo_apply_progress(completed)


@runs_app.command("show")
def runs_show(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Service-issued run ID."),
) -> None:
    """Show one run's service record and its selected execution.

    `prefect_flow_run_url` links the flow run in the Prefect UI; it reads <none> when the Sync API has no UI address.
    For a run with a `plan` or `sync` stage, `source_read` and `destination_read` show the read state of each side
    in that stage's latest execution: `state`, `mode`, `started_at`, `ended_at`, `nodes` and `error`, without the
    keys that have no value. A side with no read state to return reads <none>. Neither line is printed when that
    execution has no read state: a run recorded before the read state, a stage the worker has not started, or a
    stage whose read state the worker could not store.
    A failed stage adds its error type and, when the run recorded them, a `cause:` and a `hint:` line.
    A `sync` that stopped after a plan that leaves out records adds a `sync stopped:` and a `hint:` line.
    A run with a write adds a `progress:` line, a `resumable:` line, one `kind` line per kind with its committed,
    uncertain and not reached operations, and one `resume` line per resume of the run. The counts are over the
    plan's positions, recorded deletes included. The uncertain operations follow the saved position: through the
    failed operation when the worker recorded it, and every remaining operation when the worker was killed or its
    last save is not known to have succeeded. While a resume runs, they are the next 500 creates and updates
    and the recorded deletes between them; while a first apply or `sync` runs, none are uncertain. A `resume`
    line counts the creates and updates that resume wrote; the `written:` line of a failed write counts those of
    every execution of the run.
    """
    with _client_errors():
        resource = _client(ctx).get_run(run_id)
    _echo_run(resource)
    _echo_reads(resource)
    _echo_apply_progress(resource)
    # The latest execution of each stage; the orchestration list is in run order.
    latest_by_stage = {execution.purpose: execution for execution in resource.orchestration}
    # A failed stage's cause and next action, as the run's results recorded them (FR-002).
    for key, failure in resource.run.results.items():
        if not key.endswith("_failure") or not isinstance(failure, dict):
            continue
        stage = key.removesuffix("_failure")
        latest = latest_by_stage.get(stage)
        # A verify merges its result into the run's results, so a failed verify's record stays
        # after a later verify succeeds; that record no longer describes the stage.
        if latest is not None and latest.terminal_outcome == "succeeded":
            continue
        _echo_failure_record(failure, stage=stage, err=False)
    # A `sync` that stopped after a plan that leaves out records records no `<stage>_failure`:
    # its stage result carries `failure` (spec 019 FR-012).
    stop = resource.run.results.get("failure")
    if isinstance(stop, dict):
        _echo_left_out_stop(stop, stage="sync", err=False)


@runs_app.command("results")
def runs_results(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Service-issued run ID."),
) -> None:
    """Print the results the service recorded for one run, as JSON."""
    # The API owns what a result says and what is safe to say, so this renders
    # the typed response and adds no structure of its own. `json.dumps` escapes
    # every control character it emits, which keeps recorded provider text from
    # reaching a terminal as an escape sequence.
    with _client_errors():
        resource = _client(ctx).get_results(run_id)
        typer.echo(json.dumps(resource.model_dump(mode="json"), sort_keys=True, indent=2))


@runs_app.command("plan")
def runs_plan(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Service-issued run ID."),
    *,
    detail: bool = typer.Option(False, "--detail", help="Render every saved operation."),  # noqa: FBT003
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Filter the detailed operation list, or the problem, left-out or kept list, by destination kind.",
    ),
    problems: bool = typer.Option(False, "--problems", help="List every problem the plan found."),  # noqa: FBT003
    left_out: bool = typer.Option(False, "--left-out", help="List every record the plan leaves out."),  # noqa: FBT003
    kept: bool = typer.Option(
        False,  # noqa: FBT003
        "--kept",
        help="List every record the plan keeps without one optional relationship.",
    ),
    problem_type: str | None = typer.Option(
        None, "--type", help="List only the problems of this problem type; requires --problems."
    ),
) -> None:
    """Review a saved plan summary, checksum, and operations, or list its problems, left-out or kept records."""
    with _client_errors():
        lists = [name for name, chosen in (("problems", problems), ("left_out", left_out), ("kept", kept)) if chosen]
        if len(lists) > 1 or (lists and detail):
            raise ClientInputError(lists[1] if len(lists) > 1 else "detail")
        if problem_type is not None and not problems:
            raise ClientInputError(_TYPE_ARG)
        client = _client(ctx)
        if problems:
            for entry in _report_pages(
                client.get_plan_problems, run_id, "get_plan_problems", problem_type=problem_type, kind=kind
            ):
                typer.echo(_problem_line(entry))
        elif left_out:
            for entry in _report_pages(client.get_plan_left_out, run_id, "get_plan_left_out", kind=kind):
                typer.echo(_left_out_line(entry))
        elif kept:
            for entry in _report_pages(client.get_plan_kept, run_id, "get_plan_kept", kind=kind):
                typer.echo(_kept_line(entry))
        else:
            _echo_plan(client.get_plan(run_id), detail=detail, kind=kind)


# A run's phase while its first execution is submitted and has not ended.
_UNFINISHED_RUN_PHASE = "accepted"
# Empty reads, one poll interval apart, after which `runs logs --follow` stops once the run has
# ended. The orchestrator stores the last lines of an execution in batches after it ends, so the
# first empty read after the end can come before them (spec 017 quickstart, scenario 3).
_FINAL_EMPTY_READS = 3
# Client operation names a protocol error reports.
_READ_RUN_LOGS = "read_run_logs"
_LIST_EVENTS = "list_events"


def _row(*columns: str) -> str:
    """Join columns with tabs, each made safe for a terminal so no value adds a column."""
    return "\t".join(_display(column) for column in columns)


def _log_row(line: RunLogLine) -> str:
    return _row(line.timestamp.isoformat(), line.stage, line.level, line.message)


def _run_ended(client: SyncClient, page: RunLogPage) -> bool:
    """Report whether the run whose log page this is can write no further line.

    The verdict of the run's latest execution decides, not the phase: an apply or a verify of a
    planned run keeps the phase `planned` while it runs. A run with no execution yet has ended
    only when its phase is no longer `accepted`.
    """
    orchestration = client.get_run(page.run_id).orchestration
    if not orchestration:
        return page.run_phase != _UNFINISHED_RUN_PHASE
    return orchestration[-1].terminal_state is not None


@runs_app.command("logs")
def runs_logs(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Service-issued run ID."),
    stage: str | None = typer.Option(None, "--stage", help=f"Only lines of this stage: {', '.join(LOG_STAGES)}."),
    follow: bool = typer.Option(
        False,  # noqa: FBT003
        "--follow",
        help=f"Read new lines every {DEFAULT_POLL_INTERVAL:g} seconds until the run ends.",
    ),
) -> None:
    """Print the lines Sync wrote during one run, oldest first: time, stage, level, message."""
    with _client_errors():
        client = _client(ctx)
        offset = 0
        # Consecutive empty reads since the run ended; `None` until it has ended.
        empty_after_end: int | None = None
        try:
            while True:
                page = client.read_run_logs(run_id, stage=stage, offset=offset)
                _echo_rows(page.items, _log_row)
                offset += len(page.items)
                if page.has_more:
                    # A page that says more follow but holds none would repeat forever.
                    if not page.items:
                        raise ProtocolError(_READ_RUN_LOGS)
                    continue
                if not follow:
                    return
                # The orchestrator stores a run's last lines a moment after its execution ends, so
                # stop only after `_FINAL_EMPTY_READS` empty reads once the run has ended.
                if page.items:
                    empty_after_end = None if empty_after_end is None else 0
                elif empty_after_end is not None or _run_ended(client, page):
                    empty_after_end = 1 if empty_after_end is None else empty_after_end + 1
                    if empty_after_end >= _FINAL_EMPTY_READS:
                        return
                sleep(DEFAULT_POLL_INTERVAL)
        except KeyboardInterrupt:
            raise typer.Exit(130) from None


def _event_row(event: EventResource) -> str:
    primary = event.primary_resource
    return _row(
        event.occurred_at.isoformat(),
        event.event_type,
        f"{primary.kind}:{primary.id}" if primary is not None else "-",
        event.actor,
        event.outcome,
    )


@app.command("events")
def events_cmd(
    ctx: typer.Context,
    run_id: str | None = typer.Option(None, "--run-id", help="Only events about this run."),
    config: str | None = typer.Option(None, "--config-id", "--config", help=_CONFIG_HELP),
    event_types: list[str] | None = typer.Option(
        None, "--type", help="Only events of this type, such as infrahub_sync.run.planned. Repeat for several."
    ),
    follow: bool = typer.Option(
        False,  # noqa: FBT003
        "--follow",
        help=f"Read new events every {DEFAULT_POLL_INTERVAL:g} seconds until interrupted.",
    ),
) -> None:
    """List typed events, oldest first: time, type, resource, actor, outcome."""
    with _client_errors():
        client = _client(ctx)
        config_id = _config_id(ctx, config) if config is not None else None
        types = tuple(event_types or ())
        if not follow:
            _echo_rows(client.iter_events(run_id=run_id, config_id=config_id, event_types=types), _event_row)
            return
        # Events are listed oldest first and never removed, so the count printed so far is the
        # offset of the first event not yet printed. An event saved late with an earlier time
        # moves the later ones down by one, so this can skip or repeat one event.
        offset = 0
        try:
            while True:
                page = client.list_events(run_id=run_id, config_id=config_id, event_types=types, offset=offset)
                _echo_rows(page.items, _event_row)
                offset += len(page.items)
                if page.has_more:
                    if not page.items:
                        raise ProtocolError(_LIST_EVENTS)
                    continue
                sleep(DEFAULT_POLL_INTERVAL)
        except KeyboardInterrupt:
            raise typer.Exit(130) from None
