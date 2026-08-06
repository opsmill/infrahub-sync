"""Benchmark task: compare auto-tier + concurrent-load combinations.

Runs the 4-scenario x cold/warm matrix and reports wall-clock per cell.

Scenarios:
  1. baseline       - explicit `order:` list, --no-parallel, --no-concurrent-load
  2. topology-only  - `order:` omitted, --no-parallel, --no-concurrent-load
  3. parallel-only  - `order:` omitted, --parallel,    --no-concurrent-load
  4. parallel+conc  - `order:` omitted, --parallel,    --concurrent-load (default)

Cold/warm:
  cold  - `.infrahub-sync-cache/<sync_name>/` deleted before the measured run
  warm  - a measured run preceded by one un-timed warm-up run

Usage:
  uv run invoke bench.run --name from-netbox --directory examples/

Targets live services (e.g., demo.netbox.dev + a local Infrahub). Each cell
runs `infrahub-sync sync`, so the destination Infrahub will be written to.
Run #2+ against the same destination is mostly a no-op diff - that's expected.
"""

from __future__ import annotations

import csv
import logging
import re
import shlex
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from invoke import Context, task

if TYPE_CHECKING:
    from invoke.runners import Result

from .utils import REPO_BASE

NAMESPACE = "BENCH"
logger = logging.getLogger(__name__)


# Tuple shape: (label, explicit_order, parallel, concurrent_load).
SCENARIOS: list[tuple[str, bool, bool, bool]] = [
    ("baseline (explicit order, serial, sequential loads)", True, False, False),
    ("topology-only (auto-order, serial, sequential loads)", False, False, False),
    ("parallel-only (auto-order, --parallel, sequential loads)", False, True, False),
    ("parallel+concurrent (auto-order, --parallel, concurrent loads)", False, True, True),
]

WARMTHS: tuple[str, ...] = ("cold", "warm")


@dataclass
class CellResult:
    scenario: str
    warmth: str
    elapsed_seconds: float
    exit_code: int
    load_seconds: float | None = None
    sync_seconds: float | None = None
    notes: list[str] = field(default_factory=list)


@task(name="run")
def run_bench(  # noqa: PLR0913, PLR0914, PLR0915, PLR0917
    context: Context,
    name: str = "from-netbox",
    directory: str = "examples/",
    csv_out: str = "bench-results.csv",
    continue_on_error: bool = True,  # noqa: FBT001, FBT002
    exclude: str = "InfraInterfaceL2L3,InfraIPAddress",
    scenarios: str = "",
) -> None:
    """Run the 4-scenario x cold/warm benchmark matrix against a real sync target.

    Args:
        name: SyncConfig name (must match `name:` in a config.yml under `directory`).
        directory: Root directory holding the sync config.
        csv_out: Path for the CSV summary. Rows are streamed as cells finish.
        continue_on_error: Pass --continue-on-error to every sync invocation
            (default on; matches the operator's preferred bench setup today).
        exclude: Comma-separated kinds to drop from schema_mapping (and from
            any explicit order: list). Defaults to interfaces + IP addresses,
            which dominate wall-clock on the netbox example.
        scenarios: Comma-separated substrings; only scenarios whose label
            contains any match are run. Empty (default) runs all four.
    """
    excluded = {e.strip() for e in exclude.split(",") if e.strip()}
    scenario_filters = [s.strip() for s in scenarios.split(",") if s.strip()]
    selected_scenarios = (
        [s for s in SCENARIOS if any(f in s[0] for f in scenario_filters)] if scenario_filters else SCENARIOS
    )
    if scenario_filters and not selected_scenarios:
        msg = f"No scenario label matches filters {scenario_filters!r}"
        raise ValueError(msg)
    repo_root = REPO_BASE
    base_config_path = _find_base_config(repo_root / directory, name)
    base_dir = base_config_path.parent

    base_data = yaml.safe_load(base_config_path.read_text(encoding="utf-8"))
    if excluded:
        base_data = _filter_excluded(base_data, excluded)

    # Tier order is derived from the FILTERED schema_mapping. Write the
    # filtered config to a temp file so _compute_tier_order (which shells
    # out) reads the right shape.
    filtered_config = repo_root / ".bench-filtered-config.yml"
    filtered_config.write_text(yaml.dump(base_data, sort_keys=False), encoding="utf-8")
    try:
        tier_order = _compute_tier_order(context, filtered_config)
    finally:
        filtered_config.unlink(missing_ok=True)

    cache_root = repo_root / ".infrahub-sync-cache" / name
    csv_path = repo_root / csv_out
    results: list[CellResult] = []

    # Open the CSV up-front and stream rows as each cell completes — so a
    # mid-run failure (Infrahub OOM, network blip, etc.) still leaves a
    # readable partial result on disk instead of an empty file.
    _write_csv_header(csv_path)

    print(f"\n[{NAMESPACE}] Benchmarking sync '{name}' against the config at {base_config_path}")
    if excluded:
        print(f"[{NAMESPACE}] Excluding kinds from schema_mapping: {sorted(excluded)}")
    print(f"[{NAMESPACE}] Streaming results to {csv_path} as each cell completes\n")
    for label, explicit_order, parallel, concurrent in selected_scenarios:
        with tempfile.TemporaryDirectory(prefix="infrahub-sync-bench-", dir=str(repo_root)) as tmp_dir:
            tmp_path = Path(tmp_dir)
            _write_scenario_config(
                base_data=base_data,
                base_dir=base_dir,
                tmp_dir=tmp_path,
                explicit_order=explicit_order,
                tier_order=tier_order,
            )
            relative_directory = tmp_path.relative_to(repo_root).as_posix()

            for warmth in WARMTHS:
                # cold = wipe cache. warm = re-run immediately after cold so
                # the cache + Infrahub destination already hold what cold
                # produced. No separate un-timed warm-up run — cold IS the
                # warm-up.
                if warmth == "cold" and cache_root.exists():
                    shutil.rmtree(cache_root)

                start = time.monotonic()
                result = _run_sync(
                    context,
                    name=name,
                    directory=relative_directory,
                    parallel=parallel,
                    concurrent=concurrent,
                    continue_on_error=continue_on_error,
                    capture=True,
                )
                elapsed = time.monotonic() - start

                stdout = result.stdout if result is not None else ""
                exit_code = result.exited if result is not None else -1
                load_s, sync_s, notes = _parse_phase_timings(stdout)
                cell = CellResult(
                    scenario=label,
                    warmth=warmth,
                    elapsed_seconds=elapsed,
                    exit_code=exit_code,
                    load_seconds=load_s,
                    sync_seconds=sync_s,
                    notes=notes,
                )
                results.append(cell)
                _append_csv_row(csv_path, cell)
                print(
                    f"  [{warmth:4s}] {label:60s}  total={elapsed:6.2f}s  exit={cell.exit_code}  (saved to {csv_path.name})"
                )

    # Phase 5 incremental candidate: cold = force full extract; warm =
    # let the engine choose (should pick incremental and skip per-resource
    # loads when the cursor is fresh).
    print(f"\n[{NAMESPACE}] Incremental candidate (cold forces --full-extract; warm lets engine choose)")
    with tempfile.TemporaryDirectory(prefix="infrahub-sync-bench-inc-", dir=str(repo_root)) as tmp_dir:
        tmp_path = Path(tmp_dir)
        _write_scenario_config(
            base_data=base_data,
            base_dir=base_dir,
            tmp_dir=tmp_path,
            explicit_order=False,
            tier_order=tier_order,
        )
        relative_directory = tmp_path.relative_to(repo_root).as_posix()
        if cache_root.exists():
            shutil.rmtree(cache_root)
        for warmth, force_full in (("cold", True), ("warm", False)):
            start = time.monotonic()
            result = _run_sync(
                context,
                name=name,
                directory=relative_directory,
                parallel=True,
                concurrent=True,
                continue_on_error=continue_on_error,
                full_extract=force_full,
                capture=True,
            )
            elapsed = time.monotonic() - start
            load_s, sync_s, notes = _parse_phase_timings(result.stdout if result is not None else "")
            cell = CellResult(
                scenario="incremental (auto-order, --parallel, concurrent, cursor-driven)",
                warmth=warmth,
                elapsed_seconds=elapsed,
                exit_code=result.exited if result is not None else -1,
                load_seconds=load_s,
                sync_seconds=sync_s,
                notes=notes,
            )
            results.append(cell)
            _append_csv_row(csv_path, cell)
            print(f"  [{warmth:4s}] {cell.scenario:60s}  total={elapsed:6.2f}s  exit={cell.exit_code}")

    _print_markdown(results)


def _find_base_config(directory: Path, name: str) -> Path:
    for cfg in directory.rglob("config.yml"):
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and data.get("name") == name:
            return cfg
    msg = f"No config.yml with name={name!r} found under {directory!r}"
    raise FileNotFoundError(msg)


_TIER_ORDER_SCRIPT = """
import sys, yaml
from infrahub_sync import SyncConfig
from infrahub_sync.dependency_graph import compute_tiers, flatten_tiers
data = yaml.safe_load(open(sys.argv[1], encoding='utf-8').read())
cfg = SyncConfig(**data)
tiers, _ = compute_tiers(cfg.schema_mapping)
for name in flatten_tiers(tiers):
    print(name)
"""


def _compute_tier_order(context: Context, base_config: Path) -> list[str]:
    """Use the engine's auto-tier to derive what `order:` should look like.

    Shelled out via `uv run python` so the task works whether invoke
    itself was launched from the project venv or the system pyenv.
    """
    cmd = f"uv run python -c {shlex.quote(_TIER_ORDER_SCRIPT)} {shlex.quote(str(base_config))}"
    result = context.run(cmd, warn=False, hide=True, pty=False)
    if result is None:
        msg = "Tier-order script returned no result."
        raise RuntimeError(msg)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _filter_excluded(data: dict, excluded: set[str]) -> dict:
    """Return a new config dict with `excluded` kinds dropped from
    schema_mapping and (if present) the operator-supplied `order:` list."""
    filtered = dict(data)
    mapping = filtered.get("schema_mapping") or []
    filtered["schema_mapping"] = [sm for sm in mapping if sm.get("name") not in excluded]
    if "order" in filtered:
        filtered["order"] = [k for k in (filtered.get("order") or []) if k not in excluded]
    return filtered


def _write_scenario_config(
    *,
    base_data: dict,
    base_dir: Path,
    tmp_dir: Path,
    explicit_order: bool,
    tier_order: list[str],
) -> Path:
    data = dict(base_data)
    data.pop("order", None)
    if explicit_order:
        data["order"] = list(tier_order)
    out = tmp_dir / "config.yml"
    out.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")

    # The adapter resolver looks for `<adapter_name>/sync_adapter.py` alongside
    # config.yml (the generated per-kind class attrs live there). Copy the
    # source/destination subdirs from the base config's directory so the
    # scenario's temp directory is self-contained.
    for adapter_name in (data.get("source", {}).get("name"), data.get("destination", {}).get("name")):
        if not adapter_name:
            continue
        src = base_dir / adapter_name
        if src.is_dir():
            shutil.copytree(src, tmp_dir / adapter_name)
    return out


def _run_sync(  # noqa: PLR0913
    context: Context,
    *,
    name: str,
    directory: str,
    parallel: bool,
    concurrent: bool,
    continue_on_error: bool,
    capture: bool,
    full_extract: bool = False,
) -> Result | None:
    cmd_parts = [
        "uv run infrahub-sync sync",
        f"--name {name}",
        f"--directory {directory}",
        "--no-diff",
        "--no-show-progress",
        "--parallel" if parallel else "--no-parallel",
        "--concurrent-load" if concurrent else "--no-concurrent-load",
    ]
    if continue_on_error:
        cmd_parts.append("--continue-on-error")
    if full_extract:
        cmd_parts.append("--full-extract")
    cmd = " ".join(cmd_parts)
    return context.run(cmd, warn=True, hide=capture, pty=False)


_LOAD_LINE_RE = re.compile(r"Load:\s+Importing data from")
_SYNC_DONE_RE = re.compile(r"Sync:\s+Completed in\s+([0-9.]+)\s+sec")
_SYNC_TIER_RE = re.compile(r"Sync tier\s+\d+")


def _parse_phase_timings(stdout: str) -> tuple[float | None, float | None, list[str]]:
    """Best-effort phase splits from structlog output.

    Today the engine logs human-readable INFO lines: 'Load: Importing data
    from <adapter>' on each load and 'Sync: Completed in <s> sec' at the
    end. Until the engine emits structured phase timestamps, we extract
    what we can from those lines and surface caveats as `notes`.
    """
    notes: list[str] = []
    load_count = len(_LOAD_LINE_RE.findall(stdout))
    sync_done = _SYNC_DONE_RE.search(stdout)
    sync_seconds = float(sync_done.group(1)) if sync_done else None

    tier_count = len(_SYNC_TIER_RE.findall(stdout))
    if tier_count:
        notes.append(f"{tier_count} tiers logged")
    if not load_count:
        notes.append("no 'Load:' lines parsed")
    if sync_seconds is None:
        notes.append("no 'Sync: Completed' line parsed")
    return None, sync_seconds, notes


_CSV_HEADER = ("scenario", "warmth", "total_seconds", "sync_seconds", "exit_code", "notes")


def _write_csv_header(path: Path) -> None:
    """Open the CSV in write mode and write the header. Truncates any existing file."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(_CSV_HEADER)


def _append_csv_row(path: Path, cell: CellResult) -> None:
    """Append a single cell's row to the CSV and flush so the file is readable
    immediately, even if the benchmark dies mid-run."""
    with path.open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(
            [
                cell.scenario,
                cell.warmth,
                f"{cell.elapsed_seconds:.3f}",
                f"{cell.sync_seconds:.3f}" if cell.sync_seconds is not None else "",
                cell.exit_code,
                "; ".join(cell.notes),
            ]
        )


def _print_markdown(results: list[CellResult]) -> None:
    by_scenario: dict[str, dict[str, CellResult]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario, {})[r.warmth] = r

    print("\n## Benchmark results - total wall-clock per cell\n")
    print("| Scenario | Cold | Warm | Cold/Warm speedup |")
    print("| --- | ---: | ---: | ---: |")
    for scen, warmth_map in by_scenario.items():
        cold = warmth_map.get("cold")
        warm = warmth_map.get("warm")
        cold_s = f"{cold.elapsed_seconds:.2f}s" if cold else "-"
        warm_s = f"{warm.elapsed_seconds:.2f}s" if warm else "-"
        speedup = "-"
        if cold and warm and warm.elapsed_seconds > 0:
            speedup = f"{cold.elapsed_seconds / warm.elapsed_seconds:.2f}x"
        print(f"| {scen} | {cold_s} | {warm_s} | {speedup} |")
    print()
    print("> Notes:")
    print(">")
    print(
        "> - Today `sync` always re-extracts source + destination; cache state does NOT short-circuit the load phase."
    )
    print(
        ">   Cold/warm deltas reflect upstream service warmth + the destination already containing the data from the cold run."
    )
    print(
        ">   `apply` replays a saved plan through the destination's planned-write surface "
        "(`apply_planned_operation`), so cold/warm splits are more dramatic where the adapter implements it."
    )
    print(
        "> - `--parallel` changes write ORDERING (hard tier barrier); wall-clock impact comes from concurrent loads, not from"
    )
    print(">   per-kind parallelism (diffsync is still single-threaded today).")
    print("> - Network jitter against demo.netbox.dev is significant. Run the matrix several times for stable numbers.")
