"""A plan of valid data keeps its bytes (spec 019 T003, research R3).

Recorded before any spec 019 change: the plan that the existing valid fixtures of
`tests/test_potenda_plan_artifact.py` give, through the real plan path (`Potenda.write_plan`).
The expected bytes are stored in `tests/data/valid_plan_bytes/`, so a change of the derivation,
the encoding or the plan files fails here.

Spec 019 may change only the format version and add the empty problem report. So the test
keeps out of the comparison:

- `run_id` and `created_at`, which differ per run (SC-006);
- `format_version` and `plan_checksum`, which plan format 4 (T027) changes;
- the parquet digests of `source_snapshot`, which depend on the parquet writer's version and
  not on the plan; their paths and row counts are compared.

T027 added `report.jsonl` and the manifest's `report` key; T028 added
`dropdown_values_checked: false`, since these fixtures run without a destination schema
snapshot. Each changed `PLAN_FILES`, `MANIFEST_KEYS` and the recorded manifests only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.writer import PLAN_DIR_NAME
from tests.test_potenda_plan_artifact import (
    build_config,
    build_potenda,
    destination_mirroring_source,
    destination_with_orphan,
    pin_extraction_decisions,
    plan_run_dir,
    qualified_source,
    read_manifest,
    read_operations_bytes,
    run_plan,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.test_potenda_plan_artifact import _FakeAdapter

EXPECTED_DIR = Path(__file__).resolve().parent.parent / "data" / "valid_plan_bytes"

RUN_ID = "20260101T0000-01900003"

# The files of a saved plan of valid data.
PLAN_FILES = ["manifest.json", "operations.jsonl", "report.jsonl"]

# The manifest keys of a saved plan of valid data.
MANIFEST_KEYS = [
    "config_version",
    "created_at",
    "delete_operations_computed",
    "dropdown_values_checked",
    "format_version",
    "operations_count",
    "plan_checksum",
    "report",
    "run_id",
    "source_snapshot",
]

# Keys left out of the comparison; see the module docstring.
UNCOMPARED_KEYS = frozenset({"run_id", "created_at", "format_version", "plan_checksum"})

SCENARIOS: dict[str, Callable[[], _FakeAdapter]] = {
    # Creates over every kind, references at two levels, a cardinality-many reference and one delete.
    "creates_and_delete": destination_with_orphan,
    # An update per kind that has a scalar attribute, each with its destination ID.
    "updates": destination_mirroring_source,
}


def _compared_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    compared = {key: value for key, value in manifest.items() if key not in UNCOMPARED_KEYS}
    compared["source_snapshot"] = [
        {key: value for key, value in entry.items() if key != "digest"} for entry in manifest["source_snapshot"]
    ]
    return compared


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_valid_plan_keeps_its_bytes(scenario: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The saved plan of each valid fixture equals the bytes recorded before spec 019."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    pin_extraction_decisions(monkeypatch, [False, False])
    potenda = build_potenda(
        config=build_config(), source=qualified_source(), destination=SCENARIOS[scenario](), run_id=RUN_ID
    )

    run_plan(potenda)

    run_directory = plan_run_dir(potenda)
    manifest = read_manifest(run_directory)
    assert sorted(path.name for path in (run_directory / PLAN_DIR_NAME).iterdir()) == PLAN_FILES
    assert sorted(manifest) == MANIFEST_KEYS
    assert read_operations_bytes(run_directory) == (EXPECTED_DIR / f"{scenario}.operations.jsonl").read_bytes()
    expected_manifest = json.loads((EXPECTED_DIR / f"{scenario}.manifest.json").read_text(encoding="utf-8"))
    assert _compared_manifest(manifest) == expected_manifest
