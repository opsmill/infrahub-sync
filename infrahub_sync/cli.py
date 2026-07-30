from __future__ import annotations

import logging
import textwrap
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from timeit import default_timer as timer
from typing import TYPE_CHECKING, Any, NoReturn, cast

import typer
from infrahub_sdk import InfrahubClientSync
from infrahub_sdk.exceptions import ServerNotResponsiveError

from infrahub_sync.cache.locks import pipeline_lock
from infrahub_sync.cache.paths import run_dir as stored_run_dir
from infrahub_sync.cache.sidecars import RunFile
from infrahub_sync.plan.checksum import compute_plan_checksum

# Imported at module level rather than deferred: `infrahub_sync.utils` below already pulls
# the engine, which pulls this package, so deferring these would buy no import time and only
# hide where the command's behavior comes from.
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    OperationApplyFailedError,
    PlanArtifactError,
    UnknownPlanKindError,
)
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.plan.reader import read_plan_artifact_bytes, require_plan_directory
from infrahub_sync.plan.review import read_saved_plan, require_stored_run
from infrahub_sync.plan.verify import manifest_mapping_or_none
from infrahub_sync.plan.writer import require_uncommitted_plan
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
    from pathlib import Path

    from infrahub_sdk.schema import GenericSchema, NodeSchema

    from infrahub_sync import SyncInstance
    from infrahub_sync.plan.models import PlannedOperation, PlanSummary, RelationshipReference
    from infrahub_sync.plan.review import SavedPlan

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


def print_error_and_abort(message: str) -> NoReturn:
    logger.error("%s", message)
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
        plan = read_saved_plan(sync_name=sync_instance.name, run_id=run_id, config=sync_instance)
        summary = plan.summary()
        records = _select_review_records(plan, run_id=run_id, kind=kind) if detail else []
    except PlanArtifactError as exc:
        print_error_and_abort(str(exc))

    _echo_plan_header(plan=plan, summary=summary, sync_name=sync_instance.name, run_id=run_id)
    if detail:
        _echo_plan_detail(summary, records)
    else:
        _echo_plan_summary(summary)


def _require_a_free_run_id(*, sync_name: str, run_id: str | None) -> None:
    """Refuse re-planning into a run id whose plan generation is committed.

    Here as well as in the writer, because extraction rewrites the run's `A/` snapshots that the
    committed plan's manifest binds itself to: a re-plan reaching only the writer's refusal would
    already have invalidated the plan it was refused for. A value resolving to no run directory is
    left to the initialization arm below, which reports it already.
    """
    if run_id is None:
        return
    try:
        directory = stored_run_dir(sync_name, run_id)
    except ValueError:
        return
    try:
        require_uncommitted_plan(directory, run_id=run_id)
    except PlanArtifactError as exc:
        print_error_and_abort(str(exc))


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
        )
        return

    # A plan generation is immutable once committed, so `--run-id` naming a run that already holds
    # one is refused before the lock, the adapters and the extraction that would overwrite the
    # snapshots that plan is bound to.
    _require_a_free_run_id(sync_name=sync_instance.name, run_id=run_id)

    # Add adapter paths from CLI to the sync instance if specified
    if adapter_path is not None:
        if sync_instance.adapters_path:
            sync_instance.adapters_path.extend(adapter_path)
        else:
            sync_instance.adapters_path = adapter_path

    verbosity_level = ctx.obj.get("verbosity", logging.INFO) if ctx.obj else logging.INFO

    with pipeline_lock(sync_instance.name):
        try:
            ptd = get_potenda_from_instance(
                sync_instance=sync_instance,
                branch=branch,
                show_progress=show_progress,
                verbosity=verbosity_level,
                run_id=run_id,
                concurrent_load=concurrent_load,
            )
        except ValueError as exc:
            print_error_and_abort(f"Failed to initialize the Sync Instance: {exc}")

        ptd.force_full_extract = full_extract
        if ptd.run_dir is None:  # get_potenda_from_instance always allocates one
            msg = "get_potenda_from_instance did not allocate a run_dir"
            raise RuntimeError(msg)
        run_file = RunFile(path=ptd.run_dir / "run.json", status="running", mode="diff")
        run_file.save()

        try:
            ptd.load_both_sides()
            mydiff = ptd.diff()
            ptd.write_plan(mydiff)
            logger.info("\n%s", mydiff.str())
            run_file.status = "dry-run"
            run_file.summary = {"resources": len(ptd.top_level)}
        except Exception:
            run_file.status = "failed"
            run_file.save()
            raise

        run_file.finished_at = datetime.now(timezone.utc).isoformat()
        run_file.save()
        logger.info("Cached run %s at %s", ptd.run_id, ptd.run_dir)


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

    with pipeline_lock(sync_instance.name):
        try:
            ptd = get_potenda_from_instance(
                sync_instance=sync_instance,
                branch=branch,
                show_progress=show_progress,
                verbosity=verbosity_level,
                continue_on_error=continue_on_error,
                concurrent_load=concurrent_load,
            )
        except ValueError as exc:
            print_error_and_abort(f"Failed to initialize the Sync Instance: {exc}")

        ptd.force_full_extract = full_extract
        if ptd.run_dir is None:  # get_potenda_from_instance always allocates one
            msg = "get_potenda_from_instance did not allocate a run_dir"
            raise RuntimeError(msg)
        run_file = RunFile(path=ptd.run_dir / "run.json", status="running", mode="sync")
        run_file.save()

        try:
            if parallel and not ptd.tiers:
                logger.warning(
                    "--parallel ignored because order: is set in config.yml; "
                    "remove order: to enable tier-by-tier execution",
                )

            if parallel and ptd.tiers:
                try:
                    ptd.sync_in_tiers(parallel=True, allow_rowcount_drop=allow_rowcount_drop)
                except ValueError as exc:
                    run_file.status = "failed"
                    run_file.save()
                    print_error_and_abort(str(exc))
                run_file.summary = {"resources": len(ptd.top_level), "mode": "parallel"}
            else:
                try:
                    ptd.load_both_sides()
                except ValueError as exc:
                    run_file.status = "failed"
                    run_file.save()
                    print_error_and_abort(str(exc))
                ptd.check_rowcount_guardrail(allow_drop=allow_rowcount_drop)
                mydiff = ptd.diff()
                ptd.write_plan(mydiff)
                if mydiff.has_diffs():
                    if diff:
                        logger.info("\n%s", mydiff.str())
                    start_synctime = timer()
                    ptd.sync(diff=mydiff)
                    end_synctime = timer()
                    logger.info("Sync: Completed in %s sec", end_synctime - start_synctime)
                else:
                    logger.info("No difference found. Nothing to sync")
                ptd.persist_baseline_counts()
                run_file.summary = {"resources": len(ptd.top_level), "mode": "serial"}

            run_file.status = "applied"
        except Exception:
            run_file.status = "failed"
            run_file.save()
            raise

        run_file.finished_at = datetime.now(timezone.utc).isoformat()
        run_file.save()
        logger.info("Sync run %s at %s", ptd.run_id, ptd.run_dir)


def _require_applicable_plan(*, sync_name: str, run_id: str) -> Path:
    """Refuse an apply whose run does not exist, or holds no plan artifact (AD026, AD059).

    Both verdicts come from the same functions the review path reaches, so the unknown-run
    enumeration (bounded to the most recent twenty, with the total when it truncates, and a
    stated no-runs message when the sync has never run — AD073) and the re-plan instruction
    are written once and cannot drift between the two commands an operator meets them from.

    Returns the located run directory, so the approval check below reads the stored artifact without
    resolving the run a second time.
    """
    try:
        directory = require_stored_run(sync_name, run_id)
        require_plan_directory(directory)
    except PlanArtifactError as exc:
        print_error_and_abort(str(exc))
    return directory


def _stored_plan_checksum(run_directory: Path) -> str | None:
    """Recompute the stored plan's checksum from its bytes, or `None` when it cannot be.

    Recomputed rather than read out of the manifest: the approval check asks whether these are the
    bytes that were approved, and the manifest's recorded value is only a claim about them — one
    the pre-apply verifier is what tests. An artifact too incomplete to hash returns `None` and is
    left to that verifier, which names the tear — as is a manifest whose bytes will not decode or
    parse, which is why the mapping step is the verifier's own `manifest_mapping_or_none` and not a second
    copy of it here: the copy caught `JSONDecodeError` alone, and non-UTF-8 manifest bytes
    raise `UnicodeDecodeError` from `json.loads`, which left this refusal as a bare traceback.
    """
    artifact = read_plan_artifact_bytes(run_directory)
    if artifact.operations_bytes is None:
        return None
    mapping = manifest_mapping_or_none(artifact.manifest_bytes)
    if mapping is None:
        return None
    return compute_plan_checksum(mapping, artifact.operations_bytes)


def _require_expected_checksum(*, run_directory: Path, run_id: str, expected: str | None) -> None:
    """Refuse an apply whose stored plan is not the plan the operator approved.

    The operator's half of the immutability guarantee: immutable generations stop a plan being
    replaced under its run id, and `--expected-checksum` lets an approval name the exact bytes
    it approved — for a plan reviewed on one host and applied on another, or a pipeline that
    carries the checksum forward rather than the run id alone.

    In the command, and therefore **before the destination is constructed**: the comparison needs
    nothing but the stored artifact. Surrounding whitespace and hex case are tolerated; nothing else
    about the value is interpreted.
    """
    if expected is None:
        return
    try:
        actual = _stored_plan_checksum(run_directory)
    except PlanArtifactError as exc:
        print_error_and_abort(str(exc))
    if actual is None or actual == expected.strip().lower():
        return
    print_error_and_abort(
        f"The plan stored for run {run_id!r} is not the plan this apply approved: --expected-checksum "
        f"names {expected.strip().lower()!r} and the stored artifact hashes to {actual!r}. Nothing was "
        f"written and no destination was contacted. Next action: review the stored plan with "
        f"`diff --from-plan {run_id}` and approve its checksum, or apply the run whose checksum you "
        f"already approved."
    )


def _record_and_abort(run_file: RunFile, exc: PlanArtifactError, record: ApplyRecord) -> NoReturn:
    """Record what the apply did, then report a designed refusal as one error line.

    Every member of the plan-artifact taxonomy names its own remedy (AD059), so an operator
    who meets one has met a decision the tool made on purpose, not a crash — and reads it
    the way `_require_applicable_plan` four lines above already reports its own refusals.

    The recording happens **before** the abort and merges `record` into the summary before
    `save()`, because `RunFile.save()` writes the whole payload from this instance
    (`infrahub_sync.cache.sidecars`) and would otherwise destroy it (AD062, AD069).
    """
    run_file.summary.update(record.as_summary_keys())
    run_file.status = "failed"
    run_file.save()
    print_error_and_abort(str(exc))


def _record_carried(run_file: RunFile, exc: BaseException) -> None:
    """Record whatever the engine attached to `exc`, then leave the exception to its caller.

    The counterpart of `_record_and_abort` for the exceptions the engine deliberately does
    **not** convert into the taxonomy (`Potenda.OPERATIONAL_APPLY_FAILURES` is the boundary):
    an interrupt, and any defect. Both may leave destination writes behind, so the record
    rides on the exception as an `apply_record` attribute and is merged here before `save()`,
    which writes the whole payload from this instance (AD062, AD069). An exception carrying
    nothing records the keys present and empty rather than absent, for the same reason.

    This function does not raise: the caller re-raises, so the exception keeps its own
    traceback — which for a defect is the only place its diagnosis lives.
    """
    carried = getattr(exc, "apply_record", None)
    partial = carried if isinstance(carried, ApplyRecord) else ApplyRecord()
    run_file.summary.update(partial.as_summary_keys())
    run_file.status = "failed"
    run_file.save()


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
) -> None:
    """Apply a previously cached plan against the destination — no source extraction."""
    if sum([bool(name), bool(config_file)]) != 1:
        print_error_and_abort("Please specify exactly one of 'name' or 'config-file'.")
    sync_instance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        print_error_and_abort("Failed to load sync instance.")

    # Refuse **before constructing anything** (AD026): an apply naming a run that does not
    # exist, or whose run holds no plan, is refused before even the destination adapter is
    # imported — the applier below constructs the destination and locates the stored run
    # without creating anything, so this refusal leaves no trace of the attempt.
    run_directory = _require_applicable_plan(sync_name=sync_instance.name, run_id=run_id)

    # The approval binding, also before anything is constructed: an apply that names the
    # checksum it approved must not reach a destination with a plan that is not it.
    _require_expected_checksum(run_directory=run_directory, run_id=run_id, expected=expected_checksum)

    verbosity_level = ctx.obj.get("verbosity", logging.INFO) if ctx.obj else logging.INFO

    with pipeline_lock(sync_instance.name):
        # Apply-specific assembly: the destination only — apply never reads the source, so a
        # host with destination credentials applies a plan without the source's dependencies
        # — and no sidecar writes: the stored run's files are the immutable provenance of
        # the plan under apply. The plan's destination binding is the supported apply-time
        # guard against a drifted destination.
        applier = PlanApplier.open_existing(
            sync_instance,
            run_id=run_id,
            branch=branch,
            verbosity=verbosity_level,
        )
        run_file = RunFile(path=applier.run_dir / "run.json", status="running", mode="apply")
        run_file.save()

        # This command is the **single writer** of `run.json` (AD069). `apply_plan` returns
        # the record and writes no run file; the merge below has to happen before every
        # `save()`, because `RunFile.save()` writes the whole payload from this instance
        # (`infrahub_sync.cache.sidecars`) and would otherwise destroy the record
        # with the empty summary built above.
        try:
            record = applier.apply_plan(allow_destination_change=allow_destination_change)
            run_file.summary.update(record.as_summary_keys())
            run_file.status = "applied"
        except (OperationApplyFailedError, ApplyRecordInvariantError) as exc:
            # The two record-carrying members of the taxonomy, ahead of the general arm below
            # because they are `PlanArtifactError` subclasses and it would otherwise swallow
            # them — and with them the record each carries. A rejection carries the **partial**
            # one, which is what lets FR-025's last-applied pointer survive a partial apply
            # instead of being overwritten with an empty list; the invariant error is raised
            # *after* the loop wrote every non-delete operation, so its record holds the real
            # counts, and merging an empty one would tell an operator that a run which wrote
            # everything applied nothing.
            _record_and_abort(run_file, exc, exc.apply_record)
        except PlanArtifactError as exc:
            # Every remaining member of the taxonomy is a **designed refusal** that wrote
            # nothing and names its own remedy, so it reaches the operator as that message
            # rather than as a stack trace (AD059). It records every summary key as present
            # and empty rather than absent: "nothing was applied" must be readable from the
            # run, not inferred from a missing key (AD062).
            _record_and_abort(run_file, exc, ApplyRecord())
        except Exception as exc:
            # Outside the taxonomy **and** outside the engine's operational boundary, so not a
            # designed refusal but a defect — this code's, or an SDK shape change's. It keeps
            # its traceback, which is the only place its diagnosis lives, and it is *not*
            # dressed up as a destination rejection: the operator is told the destination is
            # not the thing to repair, and that the apply may have left writes behind.
            # The engine attaches the partial record even here, so this arm persists it rather
            # than the empty one it used to record.
            # `logger.error` and not `logger.exception`: the `raise` below carries the traceback
            # to the operator already, and logging it here would print it twice.
            logger.error(  # noqa: TRY400
                "Apply of run %s failed with an unexpected error, which is a defect rather than a "
                "destination refusal: %s: %s. The traceback below is the diagnosis; the run records "
                "what was applied before it, and the failing operation may have written part of its "
                "change. Do not re-plan on the assumption the destination is at fault.",
                applier.run_id,
                type(exc).__name__,
                exc,
            )
            _record_carried(run_file, exc)
            raise
        except BaseException as exc:
            # An interrupt — Ctrl-C on a long apply. Not a defect and not a refusal, so it gets
            # no error line of its own: the operator caused it and knows what they did. Writes
            # have already landed, and the run has to say so before the interrupt continues on
            # its way; without this arm the sidecar keeps the `running` status and the empty
            # summary it was saved with, claiming nothing was even attempted (AD062). Never
            # swallowed: the bare `raise` is what keeps Ctrl-C stopping the process.
            _record_carried(run_file, exc)
            raise
        run_file.finished_at = datetime.now(timezone.utc).isoformat()
        run_file.save()
        if record.skipped_delete_count:
            # A delete-bearing plan completes, which is a designed limitation of this release
            # and not a failure (AD055) — so the count belongs on the last line an operator
            # reads as well as in the engine's warning, and at `WARNING` for the same reason
            # that one is pinned there (AD089). The level follows the count it reports: the
            # branch below carries none and stays at `INFO`, which keeps `--quiet` silent on
            # an apply with nothing to disclose.
            logger.warning(
                "Applied run %s: %d operations applied, %d deletes skipped",
                applier.run_id,
                len(record.applied_operations),
                record.skipped_delete_count,
            )
        else:
            logger.info("Applied run %s", applier.run_id)


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
