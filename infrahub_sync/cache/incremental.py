"""Helpers for incremental (changed-since) extraction.

Pure functions only — engine wiring lives in `potenda/__init__.py`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable  # noqa: TC003
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.cache.parquet_io import SNAPSHOT_INTERNAL_COLUMNS, read_table
from infrahub_sync.cache.sidecars import CursorsFile

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


_SUCCESS_STATUSES = frozenset({"applied", "dry-run"})


def previous_successful_run_dir(cache_root: Path) -> Path | None:
    """Return the most recent `<run_id>/` whose run.json status is
    'applied' or 'dry-run'. Returns None when no such run exists.
    """
    if not cache_root.exists():
        return None
    candidates: list[Path] = []
    for run_dir in cache_root.iterdir():
        if not run_dir.is_dir():
            continue
        run_file = run_dir / "run.json"
        if not run_file.exists():
            continue
        try:
            payload = json.loads(run_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("status") in _SUCCESS_STATUSES:
            candidates.append(run_dir)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def should_use_incremental(
    *,
    prev_run_dir: Path | None,
    current_subhash: str,
    force_full: bool,
    runs_since_full: int = 0,
    cadence: int = 0,
) -> bool:
    """Gate the incremental path. False = full extract.

    Bails out when: the caller asked for a full extract, no prior run exists,
    the cadence threshold is reached, or the schema-subhash changed
    (mapping or destination schema moved under us, so prior snapshot is
    no longer trustworthy).

    ``cadence=0`` disables the cadence check entirely (0 is falsy), and that is
    what every current caller gets: nothing supplies ``runs_since_full`` or
    ``cadence``, because the run counter they compared against is gone. The two
    parameters stay for the caller a durable counter would give this gate.
    """
    if force_full:
        logger.info("Incremental disabled: the caller requested a full extract")
        return False
    if prev_run_dir is None:
        logger.info("Incremental disabled: no prior successful run")
        return False
    if cadence and runs_since_full >= cadence:
        logger.info(
            "Incremental disabled: cadence reached (%d/%d runs since full)",
            runs_since_full,
            cadence,
        )
        return False
    subhash_path = prev_run_dir / "schema-sub-hash.txt"
    if not subhash_path.exists():
        logger.info("Incremental disabled: prior run has no schema-sub-hash.txt")
        return False
    prev_subhash = subhash_path.read_text(encoding="utf-8").strip()
    if prev_subhash != current_subhash:
        logger.info(
            "Incremental disabled: schema-subhash changed (prev=%s, now=%s)",
            prev_subhash,
            current_subhash,
        )
        return False
    return True


def load_cursors(path: Path, *, side: str) -> dict[str, CursorState]:
    """Load ``{model_name: CursorState}`` for the given side.

    Returns an empty dict when the file does not exist or the side has no
    entries yet.  ``side`` must be ``"A"`` or ``"B"``.
    """
    if side not in {"A", "B"}:
        msg = f"side must be 'A' or 'B', got {side!r}"
        raise ValueError(msg)
    raw = CursorsFile.load_or_default(path).cursors.get(side, {})
    out: dict[str, CursorState] = {}
    for model_name, packed in raw.items():
        tier_name, _, value = packed.partition(":")
        tier = CursorTier[tier_name]
        out[model_name] = CursorState(tier=tier, value=value or None)
    return out


def persist_cursors(
    path: Path,
    *,
    side: str,
    cursors: dict[str, CursorState],
) -> None:
    """Merge ``cursors`` into the sidecar for the given side and save.

    Existing entries for other sides (or other models in the same side) are
    preserved.  ``side`` must be ``"A"`` or ``"B"``.
    """
    if side not in {"A", "B"}:
        msg = f"side must be 'A' or 'B', got {side!r}"
        raise ValueError(msg)
    sidecar = CursorsFile.load_or_default(path)
    bucket = sidecar.cursors.setdefault(side, {})
    for model_name, state in cursors.items():
        bucket[model_name] = f"{state.tier.name}:{state.value or ''}"
    sidecar.save()


def hydrate_from_parquet(
    *,
    run_dir: Path,
    side: str,
    resource: str,
    add_row: Callable[[str, dict], None],
) -> tuple[int, datetime | None]:
    """Replay ``<run_dir>/<side>/<resource>.parquet`` into the adapter.

    Calls ``add_row(resource, payload)`` for each non-tombstoned row.
    Returns ``(rows_loaded, max_extract_ts)``. When the file is missing
    returns ``(0, None)``.
    """
    parquet_path = run_dir / side / f"{resource}.parquet"
    if not parquet_path.exists():
        return 0, None

    table = read_table(str(parquet_path))
    if table.num_rows == 0:
        return 0, None

    cols = [c for c in table.column_names if c not in SNAPSHOT_INTERNAL_COLUMNS]
    pylist = table.select(cols).to_pylist()
    extract_ts_col = table.column("_extract_ts").to_pylist()
    tombstones = table.column("_tombstone").to_pylist()

    rows_loaded = 0
    max_ts: datetime | None = None
    for payload, ts, tomb in zip(pylist, extract_ts_col, tombstones, strict=True):
        if tomb:
            continue
        add_row(resource, payload)
        rows_loaded += 1
        if ts is not None and (max_ts is None or ts > max_ts):
            max_ts = ts
    return rows_loaded, max_ts
