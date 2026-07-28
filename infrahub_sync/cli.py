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
from infrahub_sync.cache.sidecars import RunFile

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
from infrahub_sync.plan.reader import require_plan_directory
from infrahub_sync.plan.review import read_saved_plan, require_stored_run
from infrahub_sync.utils import (
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
    from infrahub_sync.plan.models import PlannedOperation, PlanSummary
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
# Review mode — rendering a saved plan artifact (FR-006, FR-008, FR-029, AD032, AD056, AD058)
#
# Presentation only. Everything read, filtered or counted below comes from `read_saved_plan`,
# FR-029's single supported reading entry point, so the command-line depth and the in-process
# depth disclose from one source and neither re-implements the other. Output goes to
# **stdout** through `typer.echo` — the framework facility the CLI already uses for help —
# never through the logger, which the live comparison path keeps for itself (AD023, AD032).
#
# The layout below is operator-facing text and **not** a stability contract: wording, field
# order and column widths may change without that being a breaking change (AD030). What is
# **not** free to change is the delete-computation record, the not-executed annotation and
# the never-empty `--kind` rule, which are obligations under FR-006 and FR-015 (AD056, AD058).
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
                f"successfully and record {count} skipped deletes on the run; each delete record "
                f'is marked "(not executed)" in the --detail listing.'
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


def _echo_plan_detail(summary: PlanSummary, records: list[PlannedOperation]) -> None:
    """Echo the per-object depth: one record per operation, disclosures first.

    Each record carries at least the operation identifier, the action, the destination kind
    and the destination identity (AD020) — the review-side source SC-005 compares the apply
    result against — and every `delete` carries its not-executed marker (AD056).
    """
    for note in _delete_disclosure(summary):
        typer.echo("")
        typer.echo(note)
    typer.echo("")
    if not records:
        typer.echo("This plan contains no operations.")
        return
    action_width = max(len(record.action) for record in records)
    kind_width = max(len(record.kind) for record in records)
    for record in records:
        marker = "   (not executed)" if record.action == "delete" else ""
        typer.echo(
            f"{record.operation_id}  {record.action:<{action_width}}  "
            f"{record.kind:<{kind_width}}  {_identity_text(record.identity)}{marker}"
        )


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


def _require_applicable_plan(*, sync_name: str, run_id: str) -> None:
    """Refuse an apply whose run does not exist, or holds no plan artifact (AD026, AD059).

    Both verdicts come from the same functions the review path reaches, so the unknown-run
    enumeration (bounded to the most recent twenty, with the total when it truncates, and a
    stated no-runs message when the sync has never run — AD073) and the re-plan instruction
    are written once and cannot drift between the two commands an operator meets them from.
    """
    try:
        require_plan_directory(require_stored_run(sync_name, run_id))
    except PlanArtifactError as exc:
        print_error_and_abort(str(exc))


def _record_and_abort(run_file: RunFile, exc: PlanArtifactError, record: ApplyRecord) -> NoReturn:
    """Record what the apply did, then report a designed refusal as one error line.

    Every member of the plan-artifact taxonomy names its own remedy (AD059), so an operator
    who meets one has met a decision the tool made on purpose, not a crash — and reads it
    the way `_require_applicable_plan` four lines above already reports its own refusals.

    The recording happens **before** the abort and merges `record` into the summary before
    `save()`, because `RunFile.save()` writes the whole payload from this instance
    (`infrahub_sync/cache/sidecars.py:88-90`) and would otherwise destroy it (AD062, AD069).
    """
    run_file.summary.update(record.as_summary_keys())
    run_file.status = "failed"
    run_file.save()
    print_error_and_abort(str(exc))


@app.command(name="apply")
def apply_cmd(
    ctx: typer.Context,
    name: str = typer.Option(default=None, help="Name of the sync to use"),
    config_file: str = typer.Option(default=None, help="File path to the sync configuration YAML file"),
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
    run_id: str = typer.Option(..., help="Cache run id produced by a previous `diff`."),
    branch: str = typer.Option(default=None, help="Branch to use for the apply."),
) -> None:
    """Apply a previously cached plan against the destination — no source extraction."""
    if sum([bool(name), bool(config_file)]) != 1:
        print_error_and_abort("Please specify exactly one of 'name' or 'config-file'.")
    sync_instance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        print_error_and_abort("Failed to load sync instance.")

    # Refuse **before constructing anything** (AD026). `get_potenda_from_instance` imports
    # and instantiates both adapters and then does `mkdir(parents=True, exist_ok=True)` on
    # the run directory before any check, so an apply naming a run that does not exist would
    # otherwise create the very directory whose absence it is about to report.
    _require_applicable_plan(sync_name=sync_instance.name, run_id=run_id)

    verbosity_level = ctx.obj.get("verbosity", logging.INFO) if ctx.obj else logging.INFO

    with pipeline_lock(sync_instance.name):
        ptd = get_potenda_from_instance(
            sync_instance=sync_instance,
            branch=branch,
            verbosity=verbosity_level,
            run_id=run_id,
        )
        if ptd.run_dir is None:  # get_potenda_from_instance always allocates one
            msg = "get_potenda_from_instance did not allocate a run_dir"
            raise RuntimeError(msg)
        run_file = RunFile(path=ptd.run_dir / "run.json", status="running", mode="apply")
        run_file.save()
        # Check that the cached plan was built against the same schema we
        # would build against now. Plan 2 will provide _resolve_infrahub_schema;
        # until then this check is a no-op.
        try:
            from infrahub_sync.cache import compute_schema_subhash
            from infrahub_sync.cache.sidecars import SchemaHashFile
            from infrahub_sync.utils import _resolve_infrahub_schema  # ty: ignore[unresolved-import]

            schema = _resolve_infrahub_schema(sync_instance, branch=branch)
            current = compute_schema_subhash(sync_instance, schema)
            cached = SchemaHashFile.load(ptd.run_dir / "schema-sub-hash.txt").value
            if cached and cached != current:
                print_error_and_abort(
                    f"Cached plan was built against schema-sub-hash {cached!r} but "
                    f"the destination is now at {current!r}. Re-run `diff` to "
                    "rebuild the plan."
                )
        except ImportError:
            pass  # Plan 2 resolver not available yet

        # This command is the **single writer** of `run.json` (AD069). `apply_plan` returns
        # the record and writes no run file; the merge below has to happen before every
        # `save()`, because `RunFile.save()` writes the whole payload from this instance
        # (`infrahub_sync/cache/sidecars.py:88-90`) and would otherwise destroy the record
        # with the empty summary built above.
        try:
            record = ptd.apply_plan()
            run_file.summary.update(record.as_summary_keys())
            run_file.status = "applied"
        except OperationApplyFailedError as exc:
            # First of the taxonomy arms, because it is a `PlanArtifactError` subclass and
            # the general arm below would otherwise swallow it — and with it the **partial**
            # record the rejection carries, which is what lets FR-025's last-applied pointer
            # survive a partial apply instead of being overwritten with an empty list.
            _record_and_abort(run_file, exc, exc.apply_record)
        except ApplyRecordInvariantError as exc:
            # Raised *after* the loop wrote every non-delete operation, so the record it
            # carries holds the real counts. Merging an empty one here would tell an operator
            # that a run which wrote everything applied nothing, and invite a re-apply
            # against a populated destination.
            _record_and_abort(run_file, exc, exc.apply_record)
        except PlanArtifactError as exc:
            # Every remaining member of the taxonomy is a **designed refusal** that wrote
            # nothing and names its own remedy, so it reaches the operator as that message
            # rather than as a stack trace (AD059). It records the three fields as present
            # and empty rather than absent: "nothing was applied" must be readable from the
            # run, not inferred from a missing key (AD062).
            _record_and_abort(run_file, exc, ApplyRecord())
        except Exception:
            # Outside the taxonomy, so not a designed refusal but a defect: it keeps its
            # traceback, which is the only place its diagnosis lives. The run is recorded
            # first either way.
            run_file.summary.update(ApplyRecord().as_summary_keys())
            run_file.status = "failed"
            run_file.save()
            raise
        except BaseException as exc:
            # An interrupt — Ctrl-C on a long apply. Writes have already landed, and the run
            # has to say so before the interrupt continues on its way; without this arm the
            # sidecar keeps the `running` status and the empty summary it was saved with,
            # claiming nothing was even attempted (AD062). Never swallowed: the bare `raise`
            # is what keeps Ctrl-C stopping the process.
            carried = getattr(exc, "apply_record", None)
            partial = carried if isinstance(carried, ApplyRecord) else ApplyRecord()
            run_file.summary.update(partial.as_summary_keys())
            run_file.status = "failed"
            run_file.save()
            raise
        run_file.finished_at = datetime.now(timezone.utc).isoformat()
        run_file.save()
        if record.skipped_delete_count:
            # A delete-bearing plan completes: it applied every non-delete operation and
            # executed no delete, which is a designed limitation of this release and not a
            # failure (AD055). The count belongs on the completion line as well as in the
            # engine's warning, because this is the last line an operator reads.
            logger.info(
                "Applied run %s: %d operations applied, %d deletes skipped",
                ptd.run_id,
                len(record.applied_operations),
                record.skipped_delete_count,
            )
        else:
            logger.info("Applied run %s", ptd.run_id)


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
