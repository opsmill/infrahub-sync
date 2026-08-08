"""Cache directory layout + run_id allocation."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path, PurePath


def _require_safe_segment(value: str, field: str) -> str:
    """Reject path segments that could escape the cache root.

    `sync_name` comes from `config.yml` and `run_id` from `--run-id` on
    whichever command supplies one — `diff` and `sync` allocate into a run,
    `apply` names an existing one. Both are joined into a `Path`, so a value
    like `..` or `/etc` would let an attacker (or a typo) write outside the
    intended root.
    """
    p = PurePath(value)
    if p.is_absolute() or len(p.parts) != 1 or p.parts[0] in {".", ".."}:
        msg = f"{field} must be a single relative path segment (got {value!r})"
        raise ValueError(msg)
    return value


def cache_root_for(sync_name: str) -> Path:
    """Return the per-pipeline cache root.

    Defaults to `<cwd>/.infrahub-sync-cache/<sync_name>/`. Override with the
    `INFRAHUB_SYNC_CACHE_DIR` environment variable to point at a shared
    location (e.g., an NFS mount used by a fleet of runners). The override is
    expanded (`~`) and rejected if it contains `..` traversal segments, so a
    misconfigured value can't silently redirect the cache outside its root.

    This is the SINGLE derivation point for every cache path, so absolutizing a
    relative override here is what makes the run directory — and therefore
    `RunResult.artifact_path`, which crosses a process boundary to a caller that
    cannot recover the serving process's cwd — absolute everywhere. `absolute()`
    rather than `resolve()`: it prepends the cwd without resolving symlinks, so
    the final path segment (the `run_id` a `RunResult` invariant compares against)
    is preserved exactly.
    """
    _require_safe_segment(sync_name, "sync_name")
    base = os.environ.get("INFRAHUB_SYNC_CACHE_DIR")
    if base:
        base_path = Path(base).expanduser()
        if ".." in base_path.parts:
            msg = f"INFRAHUB_SYNC_CACHE_DIR must not contain '..' traversal segments (got {base!r})"
            raise ValueError(msg)
        return base_path.absolute() / sync_name
    return Path.cwd() / ".infrahub-sync-cache" / sync_name


def generate_run_id() -> str:
    """Return a sortable, low-collision run identifier.

    Format: `YYYYMMDDTHHMM-<8 hex>`. Sortable by time (the prefix), unique
    across processes (the suffix is 32 bits of randomness).
    """
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
    return f"{now}-{secrets.token_hex(4)}"


def run_dir(sync_name: str, run_id: str) -> Path:
    """Concatenate the cache root with the run identifier."""
    _require_safe_segment(run_id, "run_id")
    return cache_root_for(sync_name) / run_id
