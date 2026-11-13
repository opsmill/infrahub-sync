"""Command-line client for the Sync HTTP API."""

# One module keeps every command on the one Typer app and its shared client boundary.
# pylint: disable=too-many-lines

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from enum import Enum
from functools import wraps
from math import isfinite
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING, Any, NoReturn, TypeVar, cast
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
    RECOVERY_NEXT_ACTIONS,
    SCHEMA_CHANGED_NEXT_ACTION,
    ListPage,
)
from infrahub_sync.configuration.conversion import (
    ConversionInputError,
    ConversionResult,
    convert_v2_configuration,
    visible_v2_variables,
)
from infrahub_sync.configuration.conversion_report import next_step_lines, refusal_lines, report_lines
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_MESSAGE, UNSUPPORTED_STORE_REASON

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence

    from infrahub_sync.client.models import (
        ArtifactReferenceResource,
        ConfigurationSummaryResource,
        ConfigurationVersionResource,
        ConfigurationVersionSummaryResource,
        EventResource,
        PlanLastChangeResource,
        PlanOperationResource,
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
    offset: int = typer.Option(0, "--offset", help="Finding offset."),
    limit: int = typer.Option(256, "--limit", help="Maximum findings to return."),
) -> None:
    """Validate a registered configuration version."""
    with _client_errors():
        config_id = _config_id(ctx, config)
        _echo_validation(_client(ctx).validate_config(config_id, version, offset=offset, limit=limit))


# --- configs convert: a local command that needs no Sync API ------------------------------


def _stdin_is_terminal() -> bool:
    """Return whether standard input is a terminal; tests replace this to cover both paths."""
    return sys.stdin.isatty()


def _echo_lines(lines: list[str], *, err: bool = False) -> None:
    for line in lines:
        typer.echo(_display(line), err=err)


def _convert_fail(message: str, code: int) -> NoReturn:
    """Print ``message`` to standard error and stop with exit status ``code``."""
    typer.echo(_display(message), err=True)
    raise typer.Exit(code)


def _read_v2_configuration(path: Path) -> object:
    """Read and parse ``path``; a YAML error names only the line and column (research R9)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _convert_fail(f"error: cannot read {path}", 2)
    try:
        return yaml.safe_load(text)
    except yaml.MarkedYAMLError as error:
        # The problem text can quote the failing token, which may be a credential.
        mark = error.problem_mark or error.context_mark
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        _convert_fail(f"error: {path.name} is not valid YAML{where}", 2)
    except yaml.YAMLError:
        _convert_fail(f"error: {path.name} is not valid YAML", 2)


class _PackageDumper(yaml.SafeDumper):
    """Write every value in full: a YAML anchor in config.yml does not become ``&id001`` in the package."""

    def ignore_aliases(self, data: Any) -> bool:  # noqa: ARG002 - PyYAML's signature.
        return True


def _create_exclusively(output: Path, text: str) -> None:
    """Create ``output`` with ``text``, failing with ``FileExistsError`` when it exists.

    A write that fails removes the file it created, so no partial package is left behind.
    """
    handle = output.open("x", encoding="utf-8")
    try:
        with handle:
            handle.write(text)
    except OSError:
        output.unlink(missing_ok=True)
        raise


def _write_package(output: Path, package: Mapping[str, Any], *, replace: bool) -> None:
    """Write through a temporary file in the target directory, then move it in place (research R8).

    With ``replace``, the move is a rename that replaces ``output``. Without it, the move is a
    hard link, which fails with ``FileExistsError`` when ``output`` was created after the
    command checked for it, for example while the question waited for an answer. On a file
    system without hard links, the file is created exclusively instead, which keeps that rule.
    """
    text = yaml.dump(
        dict(package), Dumper=_PackageDumper, sort_keys=False, default_flow_style=False, allow_unicode=True
    )
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed before the move below.
        "w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.", suffix=".tmp", delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
        temporary.chmod(0o644)
        if replace:
            temporary.replace(output)
        else:
            try:
                os.link(temporary, output)
            except FileExistsError:
                raise
            except OSError:
                _create_exclusively(output, text)
    finally:
        temporary.unlink(missing_ok=True)


def _is_same_file(first: Path, second: Path) -> bool:
    """Return whether two paths name the same file; a path that does not exist names none."""
    try:
        return first.samefile(second)
    except OSError:
        return False


def _with_live_schema(path: Path, result: ConversionResult) -> tuple[ConversionResult, Mapping[str, Any]]:
    """Fill IP namespaces from the live Infrahub schema; a filled package that fails a check is refused."""
    # Imported here: the schema reader pulls in the Infrahub SDK, which no other convert path needs.
    # pylint: disable-next=import-outside-toplevel
    from infrahub_sync.configuration.conversion_live_schema import with_live_schema

    filled = with_live_schema(result)
    if filled.package is None:
        _echo_lines(refusal_lines(path.name, filled.refusals), err=True)
        raise typer.Exit(1)
    return filled, filled.package


@configs_app.command("convert")
def configs_convert(
    path: Path = typer.Argument(..., help="The v1 or v2 config.yml to read."),
    *,
    output: Path | None = typer.Option(
        None, "--output", help="Where to write the package. Defaults to package.yml next to PATH."
    ),
    force: bool = typer.Option(False, "--force", help="Replace an existing output file."),  # noqa: FBT003
    yes: bool = typer.Option(False, "--yes", help="Write without asking."),  # noqa: FBT003
    source_url: str | None = typer.Option(
        None, "--source-url", help="The source address. Overrides the file and the v2 variables."
    ),
    destination_url: str | None = typer.Option(
        None, "--destination-url", help="The Infrahub address. Overrides the file and the v2 variables."
    ),
    live_schema: bool = typer.Option(
        False,  # noqa: FBT003
        "--live-schema",
        help="Read the Infrahub schema, without a token, and map an unmapped IP namespace to 'default'.",
    ),
) -> None:
    """Convert a v1 or v2 config.yml into a V3 configuration package. Needs no Sync API.

    \b
    The command never prints or reads a credential value. It opens no network
    connection unless --live-schema is given; then it reads the Infrahub schema
    without a token and maps each IP namespace that a uniqueness constraint
    needs and config.yml does not map to the 'default' namespace, where v2
    wrote it. It prints a report, asks before it writes, and writes nothing
    without a terminal unless --yes is given.

    \b
    The report names each credential setting that v2 used with the
    environment variable the Sync worker reads in V3, each address with its origin (a flag, a v2
    variable, or config.yml), each dropped key with its reason, and notes.
    It warns when config.yml still holds a literal credential, when the
    shipped Compose file does not pass a credential variable to the Sync
    worker, when an address is localhost or 127.0.0.1, and when an address
    came from a generic variable name.

    \b
    Exit status 1, nothing written, when the configuration has any of:
    - a source or destination name that is not a bundled adapter
    - a custom adapter (INFP-654, planned for 3.0)
    - a destination other than Infrahub
    - a setting the bundled adapter does not accept
    - an auth_method value v2 did not accept
    - a placeholder v2 did not read
    - an address with a user name or password
    - a setting with no value

    \b
    Exit status 1 also when:
    - the package fails a registration check
    - the output file exists and --force is not given
    - --output names the input file, even with --force
    - there is no terminal and --yes is not given
    - the package cannot be written

    \b
    Exit status 2 when:
    - PATH cannot be read
    - PATH is not valid YAML
    - PATH is not a v1 or v2 configuration
    - the directory of the output file does not exist
    """
    data = _read_v2_configuration(path)
    target = output if output is not None else path.parent / "package.yml"
    if not target.parent.is_dir():
        _convert_fail(f"error: the directory of {target} does not exist", 2)
    if _is_same_file(target, path):
        if output is None:
            _convert_fail(f"error: the default output {target} is the input file; pass --output with another path", 1)
        _convert_fail(f"error: --output {target} is the input file {path}; choose another path", 1)
    if target.exists() and not force:
        _convert_fail(f"{target} exists; pass --force to replace it", 1)
    try:
        result = convert_v2_configuration(
            cast("Mapping[str, Any]", data),
            visible_v2_variables(os.environ),
            source_url=source_url,
            destination_url=destination_url,
        )
    except ConversionInputError as error:
        _convert_fail(f"error: {path.name} cannot be converted: {error}", 2)
    if result.package is None:
        _echo_lines(refusal_lines(path.name, result.refusals), err=True)
        raise typer.Exit(1)
    package = result.package
    if live_schema:
        result, package = _with_live_schema(path, result)
    _echo_lines([*report_lines(path.name, cast("dict[str, Any]", data)["name"], result), ""])
    if not yes:
        if not _stdin_is_terminal():
            _convert_fail("nothing written; run again with --yes", 1)
        if not typer.confirm(f"Write {target}?", default=False):
            typer.echo("nothing written")
            raise typer.Exit(0)
    try:
        _write_package(target, package, replace=force)
    except FileExistsError:
        _convert_fail(f"{target} exists; pass --force to replace it", 1)
    except OSError as error:
        # strerror describes the failure; the filename attributes are not printed.
        reason = f": {error.strerror}" if error.strerror else ""
        _convert_fail(f"error: cannot write {target}{reason}", 1)
    _echo_lines([f"Wrote {target}", *next_step_lines(str(target), result)])


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

    `<stage> failed: <error type>`, then `cause:` when the run recorded an operator cause (a
    missing permission, a refused token or a lineage refusal), and `hint:` when it recorded a
    next action. A record without a next action, from an older Sync API, gets the fixed hint
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


def _echo_failure_record(failure: Mapping[str, Any], *, stage: str, err: bool) -> None:
    """Print one recorded `<stage>_failure`: the error type, then `cause:` and `hint:` when recorded."""
    if failure.get("stage") != stage:
        return
    error_type = failure.get("error_type")
    if not (
        isinstance(error_type, str) and len(error_type) <= 128 and error_type.isascii() and error_type.isidentifier()
    ):
        return
    typer.echo(f"{stage} failed: {error_type}", err=err)
    _echo_failed_checks(failure.get("failed_checks"), err=err)
    cause = failure.get("cause")
    if isinstance(cause, str) and cause:
        typer.echo(f"cause: {_display(cause[:MAX_FAILURE_CAUSE_TEXT])}", err=err)
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
            _echo_plan(client.get_plan(completed.run.run_id))


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


@app.command("apply")
def apply_cmd(
    ctx: typer.Context,
    *,
    run_id: str = typer.Argument(..., help="Service-issued run ID whose plan was reviewed."),
    expected_checksum: str = typer.Option(..., "--expected-checksum", help="Checksum printed by `runs plan`."),
    reason: str | None = typer.Option(None, "--reason", help="Optional audit reason for the apply."),
    branch: str | None = typer.Option(None, "--branch", help="Destination branch admitted by the Sync API."),
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
    """Record approval of the named run's saved plan without applying it."""
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
    """
    with _client_errors():
        resource = _client(ctx).get_run(run_id)
    _echo_run(resource)
    _echo_reads(resource)
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
    detail: bool = typer.Option(False, "--detail", help="Render every saved operation."),  # noqa: FBT003
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Filter the detailed operation list by destination kind; requires --detail.",
    ),
) -> None:
    """Review a saved plan summary, checksum, and operations."""
    with _client_errors():
        _echo_plan(_client(ctx).get_plan(run_id), detail=detail, kind=kind)


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
