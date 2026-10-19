"""Unit tests for infrahub_sync.cache.paths."""

from __future__ import annotations

import re

from infrahub_sync.cache.paths import (
    cache_root_for,
    generate_run_id,
    run_dir,
)


def test_cache_root_defaults_to_cwd_dot_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("INFRAHUB_SYNC_CACHE_DIR", raising=False)
    root = cache_root_for("from-netbox")
    assert root == tmp_path / ".infrahub-sync-cache" / "from-netbox"


def test_cache_root_honors_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "custom"))
    root = cache_root_for("from-netbox")
    assert root == tmp_path / "custom" / "from-netbox"


def test_generate_run_id_format() -> None:
    rid = generate_run_id()
    # ISO-ish: 20260512T1530-<8 hex>
    assert re.fullmatch(r"\d{8}T\d{4}-[0-9a-f]{8}", rid), rid


def test_run_dir_concatenates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    rd = run_dir("from-netbox", "20260512T1530-abc12345")
    assert rd == tmp_path / "from-netbox" / "20260512T1530-abc12345"
