"""Command-line client for the Sync HTTP API."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from enum import Enum
from math import isfinite
from pathlib import Path  # noqa: TC003 - Typer resolves command annotations at runtime.
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import typer
import yaml
from pydantic import ValidationError

from infrahub_sync.client import (
    APIError,
    ApplyRunRequest,
    ClientInputError,
    CompatibilityError,
    ConfigMutationRequest,
    ConfigsAPIError,
    CreateRunRequest,
    PlanResource,
    ProtocolError,
    RegisteredConfigurationResource,
    RegisteredVersionResource,
    RunResource,
    RunTerminalError,
    RunWaitTimeoutError,
    SyncClient,
    SyncClientError,
    TransportError,
    ValidationReportResource,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from infrahub_sync.client.models import (
        ConfigurationSummaryResource,
        ConfigurationVersionResource,
        PlanOperationResource,
    )

DEFAULT_WAIT_TIMEOUT = 30 * 60.0
DEFAULT_POLL_INTERVAL = 2.0
VERBOSITY_MAP = {"quiet": logging.WARNING, "default": logging.INFO, "verbose": logging.DEBUG}
_REQUEST_ARG = "request"
_PACKAGE_ARG = "package"
_KIND_ARG = "kind"

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
        typer.echo(f"{name}: {rendered}", err=err)


def _error_fields(error: SyncClientError) -> tuple[str, tuple[tuple[str, object], ...]]:
    if isinstance(error, ClientInputError):
        result = "client-input", (("argument", error.argument),)
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
    if isinstance(value, bool) or not isfinite(value) or value <= 0:
        raise ClientInputError(argument)


def _echo_idempotency_key(key: str) -> None:
    # The key is emitted before I/O so an uncertain transport result can be retried deliberately.
    _echo_fields((("idempotency_key", key),))


def _echo_config_summary(resource: ConfigurationSummaryResource) -> None:
    _echo_fields(
        (
            ("config_id", resource.config_id),
            ("created_at", resource.created_at.isoformat()),
        )
    )


def _echo_config_version(resource: ConfigurationVersionResource) -> None:
    _echo_fields(
        (
            ("config_id", resource.config_id),
            ("registry_version", resource.registry_version),
            ("package_checksum", resource.package_checksum),
            ("created_at", resource.created_at.isoformat()),
            ("declared_content", json.dumps(resource.declared_content, sort_keys=True, separators=(",", ":"))),
        )
    )


def _echo_registered_config(resource: RegisteredConfigurationResource) -> None:
    _echo_config_summary(resource.configuration)
    _echo_config_version(resource.version)


def _echo_registered_version(resource: RegisteredVersionResource) -> None:
    _echo_config_version(resource.version)
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
            "finding: "
            f"code={finding.code} severity={finding.severity} location={finding.location} message={finding.message}"
        )


@configs_app.command("register")
def configs_register(
    ctx: typer.Context,
    package: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    reason: str = typer.Option(..., "--reason", help="Audit reason for the mutation."),
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
    config_id: str = typer.Argument(...),
    package: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    reason: str = typer.Option(..., "--reason", help="Audit reason for the mutation."),
    idempotency_key: str | None = typer.Option(
        None,
        "--idempotency-key",
        help="Retry key. A new key is generated and printed when omitted.",
    ),
) -> None:
    """Create the next immutable version of a registered configuration."""
    with _client_errors():
        key = _idempotency_key(idempotency_key)
        _echo_idempotency_key(key)
        request = cast(
            "ConfigMutationRequest",
            _request(ConfigMutationRequest, package=_package(package), reason=reason),
        )
        _echo_registered_version(_client(ctx).create_config_version(config_id, request, key))


@configs_app.command("list")
def configs_list(ctx: typer.Context) -> None:
    """List registered configurations."""
    with _client_errors():
        for index, resource in enumerate(_client(ctx).list_configs()):
            if index:
                typer.echo("")
            _echo_config_summary(resource)


@configs_app.command("show")
def configs_show(
    ctx: typer.Context,
    config_id: str = typer.Argument(...),
    version: int | None = typer.Option(None, "--version", help="Show one immutable registry version."),
) -> None:
    """Show a configuration or one of its versions."""
    with _client_errors():
        client = _client(ctx)
        if version is None:
            _echo_config_summary(client.get_config(config_id))
        else:
            _echo_config_version(client.get_config_version(config_id, version))


@configs_app.command("versions")
def configs_versions(ctx: typer.Context, config_id: str = typer.Argument(...)) -> None:
    """List immutable versions of a registered configuration."""
    with _client_errors():
        for index, resource in enumerate(_client(ctx).list_config_versions(config_id)):
            if index:
                typer.echo("")
            _echo_config_version(resource)


@configs_app.command("validate")
def configs_validate(
    ctx: typer.Context,
    config_id: str = typer.Argument(...),
    version: int = typer.Argument(...),
    offset: int = typer.Option(0, "--offset", help="Finding offset."),
    limit: int = typer.Option(256, "--limit", help="Maximum findings to return."),
) -> None:
    """Validate a registered configuration version."""
    with _client_errors():
        _echo_validation(_client(ctx).validate_config(config_id, version, offset=offset, limit=limit))


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
            ("execution_state", selected.state if selected is not None else None),
        )
    )


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
    reason: str,
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
    typer.echo(f"{name}: " + ", ".join(f"{key}={value}" for key, value in counts.items()))


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


def _operation_detail(operation: PlanOperationResource) -> None:
    marker = " (not executed)" if operation.action == "delete" else ""
    typer.echo(
        f"{operation.operation_id} {operation.action} {operation.kind} {_identity_text(operation.identity)}{marker}"
    )
    payload = operation.payload
    if payload is not None:
        typer.echo(f"  payload: {json.dumps(payload, sort_keys=True, separators=(',', ':'))}")
    relationships = operation.relationships
    if relationships:
        typer.echo(f"  relationships: {json.dumps(relationships, sort_keys=True, separators=(',', ':'))}")


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
        ("delete_operations_computed", plan.summary.delete_operations_computed),
    ]
    if plan.schema_fingerprint is not None:
        fields.append(("schema_fingerprint", plan.schema_fingerprint))
    _echo_fields(tuple(fields))
    for note in plan.verification_notes:
        typer.echo(f"verification_note: {note}")
    _echo_counts("by_action", plan.summary.by_action)
    _echo_counts("by_kind", plan.summary.by_kind)
    _delete_disclosure(plan)
    if detail:
        for operation in operations:
            _operation_detail(operation)


def _admit_run(
    ctx: typer.Context,
    *,
    operation: str,
    config_id: str,
    version: int,
    branch: str | None,
    reason: str,
    idempotency_key: str | None,
    wait: bool,
    wait_timeout: float,
    poll_interval: float,
) -> tuple[SyncClient, RunResource]:
    _positive_duration(wait_timeout, "wait_timeout")
    _positive_duration(poll_interval, "poll_interval")
    client = _client(ctx)
    key = _idempotency_key(idempotency_key)
    _echo_idempotency_key(key)
    request = _operation_request(
        operation=operation,
        config_id=config_id,
        version=version,
        branch=branch,
        reason=reason,
    )
    accepted = client.plan(request, key) if operation == "plan" else client.sync(request, key)
    _echo_run(accepted)
    return client, _wait(
        client,
        accepted,
        wait=wait,
        wait_timeout=wait_timeout,
        poll_interval=poll_interval,
    )


@app.command("diff")
def diff_cmd(
    ctx: typer.Context,
    config_id: str = typer.Option(..., "--config-id", help="Registered configuration identity."),
    version: int = typer.Option(..., "--version", help="Registered configuration version."),
    reason: str = typer.Option(..., "--reason", help="Audit reason for the plan."),
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
            config_id=config_id,
            version=version,
            branch=branch,
            reason=reason,
            idempotency_key=idempotency_key,
            wait=wait,
            wait_timeout=wait_timeout,
            poll_interval=poll_interval,
        )
        if wait:
            _echo_plan(client.get_plan(completed.run.run_id))


@app.command("sync")
def sync_cmd(
    ctx: typer.Context,
    config_id: str = typer.Option(..., "--config-id", help="Registered configuration identity."),
    version: int = typer.Option(..., "--version", help="Registered configuration version."),
    reason: str = typer.Option(..., "--reason", help="Audit reason for the synchronization."),
    branch: str | None = typer.Option(None, "--branch", help="Destination branch admitted by the Sync API."),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="Retry key."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for this run's execution."),  # noqa: FBT003
    wait_timeout: float = typer.Option(DEFAULT_WAIT_TIMEOUT, "--wait-timeout", help="Bounded wait in seconds."),
    poll_interval: float = typer.Option(DEFAULT_POLL_INTERVAL, "--poll-interval", help="Poll interval in seconds."),
) -> None:
    """Create a confirmed synchronization run."""
    with _client_errors():
        _client_value, completed = _admit_run(
            ctx,
            operation="sync",
            config_id=config_id,
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


@app.command("apply")
def apply_cmd(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Service-issued run ID whose plan was reviewed."),
    expected_checksum: str = typer.Option(..., "--expected-checksum", help="Checksum printed by `runs plan`."),
    reason: str = typer.Option(..., "--reason", help="Audit reason for the apply."),
    branch: str | None = typer.Option(None, "--branch", help="Destination branch admitted by the Sync API."),
    idempotency_key: str | None = typer.Option(None, "--idempotency-key", help="Retry key."),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for this run's apply execution."),  # noqa: FBT003
    wait_timeout: float = typer.Option(DEFAULT_WAIT_TIMEOUT, "--wait-timeout", help="Bounded wait in seconds."),
    poll_interval: float = typer.Option(DEFAULT_POLL_INTERVAL, "--poll-interval", help="Poll interval in seconds."),
) -> None:
    """Apply a service-owned plan after binding its reviewed checksum."""
    with _client_errors():
        _positive_duration(wait_timeout, "wait_timeout")
        _positive_duration(poll_interval, "poll_interval")
        client = _client(ctx)
        key = _idempotency_key(idempotency_key)
        _echo_idempotency_key(key)
        request = cast(
            "ApplyRunRequest",
            _request(
                ApplyRunRequest,
                expected_checksum=expected_checksum,
                confirm_writes=True,
                branch=branch,
                reason=reason,
            ),
        )
        accepted = client.apply(run_id, request, key)
        _echo_run(accepted)
        try:
            completed = _wait(
                client,
                accepted,
                wait=wait,
                wait_timeout=wait_timeout,
                poll_interval=poll_interval,
            )
        except RunTerminalError as error:
            failure = client.get_results(error.run_id).results.get("apply_failure")
            if isinstance(failure, dict):
                stage = failure.get("stage")
                error_type = failure.get("error_type")
                if (
                    stage == "apply"
                    and isinstance(error_type, str)
                    and len(error_type) <= 128
                    and error_type.isascii()
                    and error_type.isidentifier()
                ):
                    typer.echo(f"{stage} failed: {error_type}", err=True)
                    if error_type == "PlanSchemaChangedError":
                        typer.echo("hint: create and review a new plan before applying again", err=True)
            raise
        if wait:
            _echo_run(completed)


@runs_app.command("plan")
def runs_plan(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Service-issued run ID."),
    detail: bool = typer.Option(False, "--detail", help="Render every saved operation."),  # noqa: FBT003
    kind: str | None = typer.Option(None, "--kind", help="Filter the detailed operation list by destination kind."),
) -> None:
    """Review a saved plan summary, checksum, and operations."""
    with _client_errors():
        _echo_plan(_client(ctx).get_plan(run_id), detail=detail, kind=kind)
