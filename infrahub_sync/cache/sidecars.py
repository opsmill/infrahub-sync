"""JSON (and one .txt) sidecars carried alongside the Parquet snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


def _atomic_write_text(path: Path, payload: str) -> None:
    """Write `payload` to `path` via tmp+rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        Path(tmp_name).replace(path)
    except BaseException:
        # Best-effort cleanup of the tmp file on any failure.
        Path(tmp_name).unlink(missing_ok=True)
        raise


@dataclass
class CursorsFile:
    """Per-resource, per-side cursors (since-timestamp, page token, or
    Infrahub diff anchor)."""

    path: Path
    cursors: dict[str, dict[str, str]] = field(default_factory=lambda: {"A": {}, "B": {}})

    @classmethod
    def load_or_default(cls, path: Path) -> CursorsFile:
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(path=path, cursors=data)

    def save(self) -> None:
        _atomic_write_text(self.path, json.dumps(self.cursors, indent=2, sort_keys=True))


@dataclass
class RunFile:
    path: Path
    status: str = "pending"  # pending | running | dry-run | applied | failed
    mode: str = ""  # diff | sync | apply
    summary: dict[str, Any] = field(default_factory=dict)
    finished_at: str | None = None

    KEYS: ClassVar[tuple[str, ...]] = ("status", "mode", "summary", "finished_at")

    @classmethod
    def load_or_default(cls, path: Path) -> RunFile:
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        # Use `k in data` (not `is not None`) so a genuinely-stored null is kept
        # rather than silently reset to the dataclass default — a stored
        # `{"status": null}` should surface as corruption, not masquerade as "pending".
        return cls(path=path, **{k: data[k] for k in cls.KEYS if k in data})

    def save(self) -> None:
        payload = {k: getattr(self, k) for k in self.KEYS}
        _atomic_write_text(self.path, json.dumps(payload, indent=2, sort_keys=True))


@dataclass
class SchemaHashFile:
    path: Path
    value: str = ""

    @classmethod
    def load(cls, path: Path) -> SchemaHashFile:
        if not path.exists():
            return cls(path=path, value="")
        return cls(path=path, value=path.read_text(encoding="utf-8").strip())

    def save(self) -> None:
        _atomic_write_text(self.path, self.value)
