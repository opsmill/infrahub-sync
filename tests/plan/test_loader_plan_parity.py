"""Plans of valid data stay the same when the record loader changes (spec 009 T002, T029).

The baseline under `tests/data/loader_parity/` was written by this module with the code as it
was before the shared record loader of spec 009 (research R3). Each case plans one valid
fixture of `tests/test_potenda_plan_artifact.py` through the real `Potenda` plan path and
compares the saved operations, re-encoded through `canonical_json_bytes`, with the baseline
byte for byte. `tests/test_potenda_tiers.py` holds no fixture that is planned: its two tests
only build a `Potenda`, so it adds no case here.

To write the baseline again, set `INFRAHUB_SYNC_WRITE_LOADER_PARITY=1`. Do that only when a
change to the plan is intended, and say so in the pull request.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pytest

from infrahub_sync.plan.canonical import canonical_json_bytes
from tests.test_potenda_plan_artifact import (
    _FakeAdapter,
    build_config,
    build_potenda,
    derivation_destination,
    destination_mirroring_source,
    destination_with_orphan,
    plan_run_dir,
    qualified_source,
    read_operations_bytes,
    run_plan,
)

if TYPE_CHECKING:
    from collections.abc import Callable

BASELINE_DIRECTORY = Path(__file__).resolve().parent.parent / "data" / "loader_parity"
WRITE_BASELINE_VARIABLE = "INFRAHUB_SYNC_WRITE_LOADER_PARITY"


class ParityCase(NamedTuple):
    """One valid fixture pair: the baseline file name and the two sides to plan."""

    name: str
    source: Callable[[], _FakeAdapter]
    destination: Callable[[], _FakeAdapter]


CASES: tuple[ParityCase, ...] = (
    ParityCase("creates_into_empty_destination", qualified_source, lambda: _FakeAdapter("destination")),
    ParityCase("updates_against_stale_destination", qualified_source, destination_mirroring_source),
    ParityCase("no_difference_against_identical_destination", qualified_source, derivation_destination),
    ParityCase("creates_and_one_delete", qualified_source, destination_with_orphan),
)


@pytest.fixture(autouse=True)
def _cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the engine's run directory and sidecar files under the test's own directory."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


def canonical_operations(operations_bytes: bytes) -> bytes:
    """Re-encode each saved operation through the plan's canonical encoding, one per line."""
    lines = [line for line in operations_bytes.splitlines() if line.strip()]
    return b"".join(canonical_json_bytes(json.loads(line)) + b"\n" for line in lines)


def plan_operations(case: ParityCase) -> bytes:
    """Plan the case with the current code and return its canonical operations."""
    potenda = build_potenda(
        config=build_config(),
        source=case.source(),
        destination=case.destination(),
        run_id="20261015T0900-00000009",
    )
    run_plan(potenda)
    return canonical_operations(read_operations_bytes(plan_run_dir(potenda)))


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_plan_of_valid_data_matches_the_loader_baseline(case: ParityCase) -> None:
    """The canonical operations of a valid fixture equal the baseline taken before the loader change."""
    actual = plan_operations(case)
    baseline = BASELINE_DIRECTORY / f"{case.name}.jsonl"

    if os.environ.get(WRITE_BASELINE_VARIABLE) == "1":
        BASELINE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        baseline.write_bytes(actual)

    assert baseline.is_file(), f"no baseline for {case.name}; set {WRITE_BASELINE_VARIABLE}=1 to write it"
    assert actual == baseline.read_bytes()
