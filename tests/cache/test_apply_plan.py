"""Potenda.apply_plan reads plan.parquet and dispatches writes; no source
extraction happens."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from infrahub_sync.cache.parquet_io import write_plan
from infrahub_sync.potenda import Potenda

if TYPE_CHECKING:
    from pathlib import Path


def test_apply_plan_dispatches_per_row(tmp_path: Path) -> None:
    rows = [
        {
            "action": "create",
            "resource": "BuiltinTag",
            "source_id": "prod",
            "dest_id": "",
            "attribute": "",
            "old_value": "",
            "new_value": '{"name":"prod"}',
            "owner": "",
            "skip_reason": "",
            "conflict_class": "",
        },
    ]
    write_plan(run_dir=tmp_path, rows=rows)

    dst = MagicMock()
    ptd = Potenda(
        source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
        destination=dst,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["BuiltinTag"],
        run_dir=tmp_path,
    )
    ptd.apply_plan()
    dst.apply_cached_row.assert_called_once()
    kwargs = dst.apply_cached_row.call_args.kwargs
    assert kwargs["resource"] == "BuiltinTag"
    assert kwargs["action"] == "create"
