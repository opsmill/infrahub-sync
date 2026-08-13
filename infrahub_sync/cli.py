from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from timeit import default_timer as timer
from typing import TYPE_CHECKING, NoReturn, cast

import typer
from infrahub_sdk import InfrahubClientSync
from infrahub_sdk.exceptions import ServerNotResponsiveError

from infrahub_sync.cache.locks import pipeline_lock
from infrahub_sync.cache.sidecars import RunFile
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
    run_id: str | None = typer.Option(default=None, help="Re-use a specific cache run id."),
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
    """Calculate and print the differences between the source and the destination systems for a given project."""
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
                ptd.sync_in_tiers(parallel=True, allow_rowcount_drop=allow_rowcount_drop)
                run_file.summary = {"resources": len(ptd.top_level), "mode": "parallel"}
            else:
                ptd.load_both_sides()
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
        except ValueError as exc:
            run_file.status = "failed"
            run_file.save()
            print_error_and_abort(str(exc))
        except Exception:
            run_file.status = "failed"
            run_file.save()
            raise

        run_file.finished_at = datetime.now(timezone.utc).isoformat()
        run_file.save()
        logger.info("Sync run %s at %s", ptd.run_id, ptd.run_dir)


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
        try:
            ptd.apply_plan()
            run_file.status = "applied"
        except Exception:
            run_file.status = "failed"
            run_file.save()
            raise
        run_file.finished_at = datetime.now(timezone.utc).isoformat()
        run_file.save()
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
