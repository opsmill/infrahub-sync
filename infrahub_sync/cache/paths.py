"""Cache directory layout + run_id allocation."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path, PurePath


def _require_safe_segment(value: str, field: str) -> str:
    """Reject path segments that could escape the cache root.

    `sync_name` comes from `config.yml` and `run_id` comes from `--run-id`
    on the apply command; both are joined into a `Path`, so a value like
    `..` or `/etc` would let an attacker (or a typo) write outside the
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
    location (e.g., an NFS mount used by a fleet of runners).
    """
    _require_safe_segment(sync_name, "sync_name")
    base = os.environ.get("INFRAHUB_SYNC_CACHE_DIR")
    if base:
        return Path(base) / sync_name
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
