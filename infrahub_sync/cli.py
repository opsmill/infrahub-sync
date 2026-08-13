from __future__ import annotations

import logging
import textwrap
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any, NoReturn, cast

import typer
from infrahub_sdk import InfrahubClientSync
from infrahub_sdk.exceptions import ServerNotResponsiveError

from infrahub_sync.adapters.infrahub import ConvergenceIdentityError
from infrahub_sync.execution import (
    RunConcurrencyError,
    collect_secret_values,
    execute_run,
    redact,
    sanitize_exception_chain,
)

# Imported at module level rather than deferred: `infrahub_sync.utils` below already pulls
# the engine, which pulls this package, so deferring these would buy no import time and only
# hide where the command's behavior comes from.
from infrahub_sync.plan.errors import (
    PlanArtifactError,
    PlanGenerationExistsError,
    UnknownPlanKindError,
    UnsafeRunIdentifierError,
)
from infrahub_sync.product_store.standalone import StandaloneProductRecordError, execute_standalone
from infrahub_sync.utils import (
    PlanApplier,
    find_missing_schema_model,
    get_all_sync,
    get_infrahub_config,
    get_instance,
    get_potenda_from_instance,
    render_adapter,
)

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from infrahub_sdk.schema import GenericSchema, NodeSchema

    from infrahub_sync import SyncInstance
    from infrahub_sync.plan.models import PlannedOperation, PlanSummary, RelationshipReference
    from infrahub_sync.plan.review import SavedPlan
    from infrahub_sync.potenda import Potenda

VERBOSITY_MAP = {"quiet": logging.WARNING, "default": logging.INFO, "verbose": logging.DEBUG}

app = typer.Typer()
logger = logging.getLogger(__name__)


class Verbosity(str, Enum):
    quiet = "quiet"
    default = "default"
    verbose = "verbose"


def _setup_logging(level: int = logging.INFO) -> None:
    """Configure logging for the infrahub_sync package when used via CLI."""
    pkg_logger = logging.getLogger("infrahub_sync")
    pkg_logger.setLevel(level)
    if not pkg_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))
        pkg_logger.addHandler(handler)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    verbosity: Verbosity = typer.Option(Verbosity.default, "--verbosity", help="Log verbosity level"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Shorthand for --verbosity verbose"),  # noqa: FBT003
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Shorthand for --verbosity quiet"),  # noqa: FBT003
) -> None:
    """Infrahub-sync: synchronize data between infrastructure sources and destinations."""
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = VERBOSITY_MAP[verbosity.value]
    _setup_logging(level=level)
    ctx.ensure_object(dict)
    ctx.obj["verbosity"] = level
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def print_error_and_abort(message: str, sync_instance: SyncInstance | None = None) -> NoReturn:
    """Log one operator-facing refusal after redacting resolved credentials."""
    logger.error("%s", redact(message, collect_secret_values(sync_instance)))
    raise typer.Abort


# ---------------------------------------------------------------------------------------
# Review mode — rendering a saved plan artifact
#
# Presentation only: everything read, filtered or counted below comes from `read_saved_plan`,
# so the command-line depth and the in-process depth disclose from one source (FR-029).
# Output goes to **stdout** through `typer.echo`, never through the logger, which the live
# comparison path keeps for itself (AD032).
#
# The layout is operator-facing text and **not** a stability contract (AD030). What is not
# free to change is the delete-computation record, the not-executed annotation and the
# never-empty `--kind` rule (FR-006, FR-015, AD056).
# ---------------------------------------------------------------------------------------

REVIEW_WIDTH = 92


def _note(text: str) -> str:
    """Wrap `text` as a `NOTE` block, hanging-indented under its own label."""
    return textwrap.fill(text, width=REVIEW_WIDTH, initial_indent="NOTE  ", subsequent_indent="      ")


def _delete_disclosure(summary: PlanSummary) -> list[str]:
    """Return AD056's disclosures, worded identically at both review depths.

    Both are obligations rather than formatting choices. Without the first, a plan whose
    whole delete class was never computed renders identically to a plan that genuinely has
    no deletes, and FR-015's "explicit and reviewable" claim — AD024's entire justification
    for omitting deletes from this release — is delivered by nothing. Without the second, a
    reviewer approves a plan without seeing, in the same output they approve, which of its
    operations will not be written.
    """
    notes: list[str] = []
    if not summary.delete_operations_computed:
        notes.append(
            _note(
                "Delete operations were NOT computed for this plan: the destination side was "
                "loaded incrementally, which cannot enumerate it completely. This plan may be "
                "missing deletes that exist. Re-run with a full destination extract to compute "
                "them."
            )
        )
    if summary.deletes_not_executed:
        count = summary.deletes_not_executed
        notes.append(
            _note(
                f"{count} delete operation(s) are recorded in this plan and NONE will be executed "
                f"against the destination by this release. Applying this plan will complete "
                f"successfully and record {count} skipped deletes on the run. Every delete record "
                f'the --detail listing shows carries a "(not executed)" marker — a --kind filter '
                f"may narrow the listing so that none of them are shown."
            )
        )
    return notes


def _identity_value_text(value: Any) -> str:
    """Render one destination-identity value, unfolding a nested peer reference (AD043)."""
    if isinstance(value, Mapping):
        peer_identity = value.get("identity")
        if "peer_kind" in value and isinstance(peer_identity, Mapping):
            return f"{value['peer_kind']}({_identity_text(peer_identity)})"
        return "{" + ", ".join(f"{key}={_identity_value_text(value[key])}" for key in sorted(value)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_identity_value_text(item) for item in value) + "]"
    return str(value)


def _identity_text(identity: Mapping[str, Any]) -> str:
    """Render a destination identity as `name=value` pairs, key-sorted."""
    return " ".join(f"{key}={_identity_value_text(identity[key])}" for key in sorted(identity))


# The redaction policy for the values `--detail` renders: a payload field whose
# **name** suggests a credential renders as `REDACTION_PLACEHOLDER` at every nesting level, and is
# still listed so the reviewer sees it is being written; nothing else is suppressed. A value longer
# than `DETAIL_VALUE_LIMIT` is *elided* with its length stated — a readability bound, rendered
# distinguishably from a redaction. Field names are the only signal, and the one that matters:
# FR-018 keeps the configuration's `settings`, where credentials enter (AD018), out of the artifact.
REDACTED_FIELD_MARKERS: tuple[str, ...] = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "private_key",
)
REDACTION_PLACEHOLDER = "<redacted: field name matches the redaction policy>"
DETAIL_VALUE_LIMIT = 200


def _redacted(value: Any, *, field: str) -> Any:
    """Apply the redaction policy to one payload value, recursing into mappings and lists.

    Recursive because the policy is about field names and a nested mapping carries its own: a
    field the policy allows can hold a sub-field it does not.
    """
    if any(marker in field.replace("-", "_").lower() for marker in REDACTED_FIELD_MARKERS):
        return REDACTION_PLACEHOLDER
    if isinstance(value, Mapping):
        return {key: _redacted(value[key], field=str(key)) for key in value}
    if isinstance(value, (list, tuple)):
        return [_redacted(item, field=field) for item in value]
    return value


def _payload_value_text(value: Any, *, field: str) -> str:
    """Render one payload value under the redaction policy, eliding an overlong one."""
    text = _identity_value_text(_redacted(value, field=field))
    if len(text) <= DETAIL_VALUE_LIMIT:
        return text
    return f"{text[:DETAIL_VALUE_LIMIT]}… (elided, {len(text)} characters)"


def _reference_text(reference: RelationshipReference) -> str:
    """Render one relationship field as its peer kind and each peer's identity.

    An **empty** cardinality-many peer set is spelled out rather than rendered as an empty list,
    because it is a value the apply writes — the replace-set write empties the relationship —
    and not the absence of one (FR-028.2).
    """
    peers = " | ".join(_identity_text(peer) for peer in reference.peers)
    many = f"many, {len(reference.peers)} peer(s)" if reference.peers else "many, empty peer set"
    shape = many if reference.cardinality == "many" else "one"
    rendered = f"{reference.field} -> {reference.peer_kind} ({shape})"
    return f"{rendered}: {peers}" if peers else rendered


def _echo_desired_state(record: PlannedOperation, *, indent: str) -> None:
    """Echo the desired destination state one operation would write.

    The canonical payload field by field, then every relationship field with its peer kind and each
    peer's identity. **Desired state, not a diff**: the artifact records what the apply will write
    and nothing about what the destination currently has, so presenting these lines as changes would
    invent a comparison the plan never made. A delete carries neither, so nothing is echoed under it.
    """
    for field in sorted(record.payload or {}):
        typer.echo(f"{indent}{field} = {_payload_value_text((record.payload or {})[field], field=field)}")
    for reference in record.relationships or ():
        typer.echo(f"{indent}{_reference_text(reference)}")


def _echo_plan_header(*, plan: SavedPlan, summary: PlanSummary, sync_name: str, run_id: str) -> None:
    """Echo the two header lines both depths share, plus any verification verdict.

    A plan whose checksum does not verify is **rendered anyway**, with the verdict prominent
    and the reader's own note carried verbatim beneath it (AD031): withholding a plan an
    operator is trying to understand is the opposite of a review.
    """
    typer.echo(f"Plan {run_id}  ({sync_name})")
    typer.echo(
        f"checksum: {'OK' if plan.checksum_ok else 'FAILED'}   "
        f"format: {plan.manifest.format_version}   "
        f"operations: {summary.total}   "
        f"deletes computed: {'yes' if summary.delete_operations_computed else 'NO'}"
    )
    # The **full** checksum, not just the verdict above: an approval naming
    # it binds to these exact bytes through `apply --expected-checksum`.
    typer.echo(f"plan checksum: {plan.manifest.plan_checksum}")
    for note in plan.verification_notes:
        typer.echo(textwrap.fill(note, width=REVIEW_WIDTH, initial_indent="          ", subsequent_indent="          "))


def _echo_counts(label: str, counts: Mapping[str, int]) -> None:
    """Echo one count table as a single wrapped line, hanging-indented under its label."""
    body = "   ".join(f"{name} {count}" for name, count in counts.items())
    indent = " " * (len(label) + 2)
    typer.echo(textwrap.fill(body, width=REVIEW_WIDTH, initial_indent=f"{label}  ", subsequent_indent=indent))


def _echo_plan_summary(summary: PlanSummary) -> None:
    """Echo the default depth: a count per action, a count per kind, then the disclosures."""
    typer.echo("")
    if not summary.total:
        # Stated explicitly rather than printed as an empty table, so a zero-operation plan
        # is never mistaken for output that failed to render (FR-022).
        typer.echo("This plan contains no operations.")
    else:
        _echo_counts("By action   ", summary.by_action)
        _echo_counts("By kind     ", summary.by_kind)
    for note in _delete_disclosure(summary):
        typer.echo("")
        typer.echo(note)


DESIRED_STATE_LEGEND = (
    "Beneath each record is the desired destination state that record would write: its canonical payload field "
    "by field, then each relationship field with its peer kind and the identity of every peer. These are the "
    "values as they will be at the destination afterwards — a desired state and not a diff, since the plan "
    "records nothing about what the destination holds now. A field whose name suggests a credential is listed "
    "with its value redacted; an overlong value is elided and says so."
)

DESIRED_STATE_INDENT = " " * 6


def _echo_plan_detail(summary: PlanSummary, records: list[PlannedOperation]) -> None:
    """Echo the per-object depth: one record per operation, disclosures first.

    Each record carries at least the operation identifier, the action, the destination kind
    and the destination identity (AD020) — the review-side source SC-005 compares the apply
    result against — and every `delete` carries its not-executed marker (AD056). Beneath each
    record sits the desired destination state it would write: two
    operations differing only in a payload value rendered identically before that, so a
    reviewer approved an object's *presence* in a plan without seeing the *change* proposed.
    """
    for note in _delete_disclosure(summary):
        typer.echo("")
        typer.echo(note)
    typer.echo("")
    if not records:
        typer.echo("This plan contains no operations.")
        return
    typer.echo(textwrap.fill(DESIRED_STATE_LEGEND, width=REVIEW_WIDTH))
    typer.echo("")
    action_width = max(len(record.action) for record in records)
    kind_width = max(len(record.kind) for record in records)
    for record in records:
        marker = "   (not executed)" if record.action == "delete" else ""
        typer.echo(
            f"{record.operation_id}  {record.action:<{action_width}}  "
            f"{record.kind:<{kind_width}}  {_identity_text(record.identity)}{marker}"
        )
        _echo_desired_state(record, indent=DESIRED_STATE_INDENT)


def _select_review_records(plan: SavedPlan, *, run_id: str, kind: str | None) -> list[PlannedOperation]:
    """Narrow the plan to `--kind`, turning an empty declared-kind result into FR-006's error.

    The never-empty rule lives **here**, in the renderer, and not in the reader (AD058). The
    reader returns `[]` for a kind the configuration declares but the plan holds no operation
    for, because FR-029 requires a programmatic caller to consume the result as data and
    forcing one to catch an exception to learn a count is a presentation rule leaking into
    the data interface. At this depth the same `[]` would print as nothing at all, which is
    indistinguishable from a mistyped kind — so it is refused, listing what the plan holds.
    """
    records = plan.operations(kind=kind)
    if kind is None or records:
        return records
    held = sorted({operation.kind for operation in plan.operations()})
    held_text = ", ".join(held) if held else "<none: the plan contains no operations>"
    msg = (
        f"The plan of run {run_id!r} holds no operation for destination kind {kind!r}. This "
        f"synchronization declares that kind, so the filter itself is valid, but nothing in this "
        f"plan changes an object of it. The plan holds operations for: {held_text}."
    )
    raise UnknownPlanKindError(msg)


def _check_review_options(*, from_plan: str | None, detail: bool, kind: str | None) -> None:
    """Enforce the two option-combination rules, which are rules and not just help text.

    A prerequisite that an option's help string states and nothing checks is the same defect
    class as an option silently ignored: the operator is told a constraint exists and then
    gets no signal when they break it (AD061).
    """
    if from_plan is None:
        if detail:
            print_error_and_abort("--detail requires --from-plan <run-id>: there is no saved plan to expand.")
        if kind is not None:
            print_error_and_abort(
                "--kind requires --from-plan <run-id> and --detail: there is no saved plan to narrow."
            )
    elif kind is not None and not detail:
        print_error_and_abort("--kind requires --detail: without it there is no per-object listing to narrow.")


def _review_saved_plan(
    *,
    sync_instance: SyncInstance,
    run_id: str,
    detail: bool,
    kind: str | None,
    ignored_run_id: str | None = None,
    product_cache_location: str | None = None,
) -> None:
    """Render the plan artifact stored for `run_id` and write nothing (FR-008, AD021, AD031).

    Every read happens before the first line is echoed, so a refusal never arrives half way
    through a rendered plan.

    `ignored_run_id` is `--run-id` when it was passed alongside `--from-plan`. It is the one
    ignored option this mode warns about rather than ignoring silently: it is the only other
    option that names a run, so saying nothing would leave the operator unable to tell which
    run they just reviewed. Nothing was created for it — this whole path sits above the
    allocation — which is why it is a warning and not an error.
    """
    if ignored_run_id is not None:
        logger.warning(
            "--run-id %r is ignored in review mode and nothing was created for it: --from-plan "
            "selected run %r, which is the run being reviewed.",
            ignored_run_id,
            run_id,
        )
    try:
        plan = execute_standalone(
            sync_instance,
            operation="verify",
            run_id=run_id,
            product_cache_location=product_cache_location,
            _core_executor=execute_run,
        )
        summary = plan.summary()
        records = _select_review_records(plan, run_id=run_id, kind=kind) if detail else []
    except (PlanArtifactError, StandaloneProductRecordError) as exc:
        print_error_and_abort(str(exc))

    _echo_plan_header(plan=plan, summary=summary, sync_name=sync_instance.name, run_id=run_id)
    if detail:
        _echo_plan_detail(summary, records)
    else:
        _echo_plan_summary(summary)


def _cli_potenda_factory(**kwargs: Any) -> Potenda:
    """Build the engine, keeping the CLI's prefixed abort at the construction site.

    `execute_run` deliberately does not catch factory failures, so this wrapper is
    where the CLI's own adapter construction error handling lives. The module global is resolved
    at call time so patches on `infrahub_sync.cli.get_potenda_from_instance` still
    intercept construction.
    """
    try:
        return get_potenda_from_instance(**kwargs)
    except (ImportError, ValueError) as exc:
        sync_instance = cast("SyncInstance", kwargs["sync_instance"])
        print_error_and_abort(f"Failed to initialize the Sync Instance: {exc}", sync_instance)


def _cli_plan_applier_factory(*args: Any, **kwargs: Any) -> PlanApplier:
    """Construct an applier while retaining the CLI's construction-only refusal."""
    sync_instance = cast("SyncInstance", args[0] if args else kwargs["sync_instance"])
    try:
        return PlanApplier.open_existing(*args, **kwargs)
    except (ImportError, ValueError) as exc:
        print_error_and_abort(f"Failed to initialize the destination for the apply: {exc}", sync_instance)


@app.command(name="list")
def list_projects(
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
) -> None:
    """List all available SYNC projects."""
    for item in get_all_sync(directory=directory):
        logger.info("%s | %s >> %s | %s", item.name, item.source.name, item.destination.name, item.directory)


@app.command(name="diff")
def diff_cmd(
    ctx: typer.Context,
    name: str = typer.Option(default=None, help="Name of the sync to use"),
    config_file: str = typer.Option(default=None, help="File path to the sync configuration YAML file"),
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
    branch: str = typer.Option(default=None, help="Branch to use for the diff."),
    show_progress: bool | None = typer.Option(default=None, help="Show a progress bar (default: auto-detect terminal)"),
    adapter_path: list[str] = typer.Option(
        default=None,
        help="Paths to look for adapters. Can be specified multiple times.",
    ),
    run_id: str | None = typer.Option(
        default=None,
        help=(
            "Re-use a specific cache run id for the live comparison. To review a saved plan "
            "instead, pass --from-plan <run-id>."
        ),
    ),
    concurrent_load: bool = typer.Option(
        default=True,
        help=("Load source and destination concurrently. Disable when a custom adapter isn't thread-safe."),
    ),
    full_extract: bool = typer.Option(
        True,  # noqa: FBT003
        "--full-extract/--no-full-extract",
        help=(
            "Re-extract every resource from scratch (default). Pass --no-full-extract to enable "
            "the cursor-driven incremental path on warm runs — see docs/reference/incremental-extraction."
        ),
    ),
    from_plan: str | None = typer.Option(
        default=None,
        help=(
            "Review the saved plan artifact for this run id instead of comparing live systems. "
            "Constructs no adapter, extracts nothing, and takes no lock."
        ),
    ),
    detail: bool = typer.Option(
        False,  # noqa: FBT003
        "--detail",
        help="Expand the plan summary to one record per operation. Requires --from-plan.",
    ),
    kind: str | None = typer.Option(
        default=None,
        help="Narrow --detail to a single destination kind. Requires --from-plan and --detail.",
    ),
    product_cache_location: str | None = typer.Option(
        default=None,
        help="Absolute local product-cache path. When set, publish the durable ProductRun and plan-review artifact.",
    ),
) -> None:
    """Calculate and print the differences between the source and the destination systems for a given project."""
    _check_review_options(from_plan=from_plan, detail=detail, kind=kind)

    if sum([bool(name), bool(config_file)]) != 1:
        print_error_and_abort("Please specify exactly one of 'name' or 'config-file'.")

    sync_instance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        print_error_and_abort("Failed to load sync instance.")

    if from_plan is not None:
        # Review mode branches **above** `pipeline_lock` and **above**
        # `get_potenda_from_instance`, so no lock is taken, no adapter is constructed,
        # nothing is extracted, and no run directory is created or modified — the last of
        # which is what stops a mistyped run id rendering as a valid zero-operation plan
        # (FR-008, AD021, AD031).
        _review_saved_plan(
            sync_instance=sync_instance,
            run_id=from_plan,
            detail=detail,
            kind=kind,
            ignored_run_id=run_id,
            product_cache_location=product_cache_location,
        )
        return

    # Add adapter paths from CLI to the sync instance if specified
    if adapter_path is not None:
        if sync_instance.adapters_path:
            sync_instance.adapters_path.extend(adapter_path)
        else:
            sync_instance.adapters_path = adapter_path

    verbosity_level = ctx.obj.get("verbosity", logging.INFO) if ctx.obj else logging.INFO

    try:
        execute_standalone(
            sync_instance,
            operation="plan",
            product_cache_location=product_cache_location,
            _core_executor=execute_run,
            confirm_writes=False,
            branch=branch,
            show_progress=show_progress,
            verbosity=verbosity_level,
            run_id=run_id,
            concurrent_load=concurrent_load,
            full_extract=full_extract,
            potenda_factory=_cli_potenda_factory,
        )
    except (
        PlanGenerationExistsError,
        UnsafeRunIdentifierError,
        RunConcurrencyError,
        StandaloneProductRecordError,
    ) as exc:
        # The core marks run.json failed. Keep the saved-plan command's narrow
        # one-line refusal for the residual writer-stage race.
        print_error_and_abort(str(exc))


@app.command(name="sync")
def sync_cmd(
    ctx: typer.Context,
    name: str = typer.Option(default=None, help="Name of the sync to use"),
    config_file: str = typer.Option(default=None, help="File path to the sync configuration YAML file"),
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
    branch: str = typer.Option(default=None, help="Branch to use for the sync."),
    diff: bool = typer.Option(
        default=True,
        help="Print the differences between the source and the destination before syncing",
    ),
    show_progress: bool | None = typer.Option(default=None, help="Show a progress bar (default: auto-detect terminal)"),
    adapter_path: list[str] = typer.Option(
        default=None,
        help="Paths to look for adapters. Can be specified multiple times.",
    ),
    parallel: bool = typer.Option(
        default=True,
        help="Sync tier-by-tier using the auto-computed dep graph. Requires order: to be omitted from config.yml.",
    ),
    allow_rowcount_drop: bool = typer.Option(
        default=False,
        help="Skip the rowcount drop guardrail. Use only when you know the source intentionally shrank.",
    ),
    continue_on_error: bool = typer.Option(
        default=False,
        help=(
            "Log and skip peer relationships whose identifier values are missing instead of aborting. "
            "Useful when source data is partial; review the warnings before relying on the result."
        ),
    ),
    concurrent_load: bool = typer.Option(
        default=True,
        help=("Load source and destination concurrently. Disable when a custom adapter isn't thread-safe."),
    ),
    full_extract: bool = typer.Option(
        True,  # noqa: FBT003
        "--full-extract/--no-full-extract",
        help=(
            "Re-extract every resource from scratch (default). Pass --no-full-extract to enable "
            "the cursor-driven incremental path on warm runs — see docs/reference/incremental-extraction."
        ),
    ),
    product_cache_location: str | None = typer.Option(
        default=None,
        help="Absolute local product-cache path. When set, publish the durable ProductRun and plan-review artifact.",
    ),
) -> None:
    """Synchronize the data between source and the destination systems for a given project or configuration file."""
    if sum([bool(name), bool(config_file)]) != 1:
        print_error_and_abort("Please specify exactly one of 'name' or 'config-file'.")

    sync_instance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        print_error_and_abort("Failed to load sync instance.")

    # Add adapter paths from CLI to the sync instance if specified
    if adapter_path is not None:
        if sync_instance.adapters_path:
            sync_instance.adapters_path.extend(adapter_path)
        else:
            sync_instance.adapters_path = adapter_path

    verbosity_level = ctx.obj.get("verbosity", logging.INFO) if ctx.obj else logging.INFO

    try:
        execute_standalone(
            sync_instance,
            operation="sync",
            product_cache_location=product_cache_location,
            _core_executor=execute_run,
            confirm_writes=True,  # the explicit human CLI invocation IS the confirmation
            branch=branch,
            show_progress=show_progress,
            verbosity=verbosity_level,
            concurrent_load=concurrent_load,
            full_extract=full_extract,
            allow_rowcount_drop=allow_rowcount_drop,
            continue_on_error=continue_on_error,
            print_diff=diff,
            parallel=parallel,
            potenda_factory=_cli_potenda_factory,
            _serial_load_error=lambda exc: print_error_and_abort(str(exc), sync_instance),
            _parallel_sync_error=lambda exc: print_error_and_abort(str(exc), sync_instance),
        )
    except (ConvergenceIdentityError, RunConcurrencyError, StandaloneProductRecordError) as exc:
        print_error_and_abort(str(exc))


@app.command(name="apply")
def apply_cmd(
    ctx: typer.Context,
    name: str = typer.Option(default=None, help="Name of the sync to use"),
    config_file: str = typer.Option(default=None, help="File path to the sync configuration YAML file"),
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
    run_id: str = typer.Option(..., help="Cache run id produced by a previous `diff`."),
    branch: str = typer.Option(default=None, help="Branch to use for the apply."),
    allow_destination_change: bool = typer.Option(
        default=False,
        help=(
            "Apply even though the live destination endpoint or branch differs from the one "
            "the plan was computed against. Without it, a destination mismatch refuses before "
            "any write."
        ),
    ),
    expected_checksum: str | None = typer.Option(
        default=None,
        help=(
            "Apply only if the stored plan hashes to this checksum — the `plan checksum` line "
            "of the review output. A mismatch refuses before the destination is contacted."
        ),
    ),
    product_cache_location: str | None = typer.Option(
        default=None,
        help="Absolute local product-cache path used by the plan. When set, extend its durable ProductRun.",
    ),
) -> None:
    """Apply a previously cached plan against the destination — no source extraction."""
    if sum([bool(name), bool(config_file)]) != 1:
        print_error_and_abort("Please specify exactly one of 'name' or 'config-file'.")
    sync_instance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        print_error_and_abort("Failed to load sync instance.")

    verbosity_level = ctx.obj.get("verbosity", logging.INFO) if ctx.obj else logging.INFO
    unexpected_error: Exception | None = None
    unexpected_traceback = None
    try:
        result = execute_standalone(
            sync_instance,
            operation="apply",
            product_cache_location=product_cache_location,
            _core_executor=execute_run,
            confirm_writes=True,
            run_id=run_id,
            branch=branch,
            verbosity=verbosity_level,
            allow_destination_change=allow_destination_change,
            expected_checksum=expected_checksum,
            _plan_applier_factory=_cli_plan_applier_factory,
        )
    except (PlanArtifactError, RunConcurrencyError, StandaloneProductRecordError) as exc:
        print_error_and_abort(str(exc), sync_instance)
    except typer.Abort:
        # A construction-only factory refusal already rendered its one-line message.
        raise
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        secrets = collect_secret_values(sync_instance)
        sanitized_message = redact(str(exc), secrets)
        logger.error(  # noqa: TRY400
            "Apply of run %s failed with an unexpected error, which is a defect rather than a "
            "destination refusal: %s: %s. The traceback below is the diagnosis; the run records "
            "what was applied before it, and the failing operation may have written part of its "
            "change. Do not re-plan on the assumption the destination is at fault.",
            run_id,
            type(exc).__name__,
            sanitized_message,
        )
        unexpected_error = RuntimeError(f"{type(exc).__name__}: {sanitized_message}")
        unexpected_error.__cause__ = sanitize_exception_chain(exc, secrets)
        unexpected_error.__suppress_context__ = True
        unexpected_traceback = exc.__traceback__
    if unexpected_error is not None:
        raise unexpected_error.with_traceback(unexpected_traceback)
    summary = result.summary
    skipped = summary["delete"]
    applied = summary["create"] + summary["update"]
    if skipped:
        logger.warning("Applied run %s: %d operations applied, %d deletes skipped", run_id, applied, skipped)
    else:
        logger.info("Applied run %s", run_id)


@app.command(name="generate")
def generate(
    name: str = typer.Option(default=None, help="Name of the sync to use"),
    config_file: str = typer.Option(default=None, help="File path to the sync configuration YAML file"),
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
    branch: str = typer.Option(default=None, help="Branch to use for the sync."),
    adapter_path: list[str] = typer.Option(
        default=None,
        help="Paths to look for adapters. Can be specified multiple times.",
    ),
) -> None:
    """Generate all the Python files for a given sync based on the configuration."""

    if sum([bool(name), bool(config_file)]) != 1:
        print_error_and_abort("Please specify exactly one of 'name' or 'config_file'.")

    sync_instance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        print_error_and_abort(f"Unable to find the sync {name}. Use the list command to see the sync available")

    # Add adapter paths from CLI to the sync instance if specified
    if adapter_path:
        if sync_instance.adapters_path:
            sync_instance.adapters_path.extend(adapter_path)
        else:
            sync_instance.adapters_path = adapter_path

    # Check if the destination is infrahub
    infrahub_address = ""
    # Determine if infrahub is in source or destination
    # We are using the destination as the "constraint", if there is 2 infrahubs instance
    sdk_config = None
    if sync_instance.destination.name == "infrahub" and sync_instance.destination.settings:
        infrahub_address = sync_instance.destination.settings.get("url") or ""
        sdk_config = get_infrahub_config(settings=sync_instance.destination.settings, branch=branch)
    elif sync_instance.source.name == "infrahub" and sync_instance.source.settings:
        infrahub_address = sync_instance.source.settings.get("url") or ""
        sdk_config = get_infrahub_config(settings=sync_instance.source.settings, branch=branch)

    # Initialize InfrahubClientSync if address and config are available
    client = InfrahubClientSync(address=infrahub_address, config=sdk_config)

    try:
        schema = client.schema.all()
    except ServerNotResponsiveError as exc:
        print_error_and_abort(str(exc))

    # SDK returns *SchemaAPI variants; structurally compatible with the (NodeSchema | GenericSchema) shape utils expects.
    typed_schema = cast("MutableMapping[str, NodeSchema | GenericSchema]", schema)
    missing_schema_models = find_missing_schema_model(sync_instance=sync_instance, schema=typed_schema)
    if missing_schema_models:
        print_error_and_abort(f"One or more model model are not present in the Schema - {missing_schema_models}")

    rendered_files = render_adapter(sync_instance=sync_instance, schema=typed_schema)
    for template, output_path in rendered_files:
        logger.info("Rendered template %s to %s", template, output_path)
