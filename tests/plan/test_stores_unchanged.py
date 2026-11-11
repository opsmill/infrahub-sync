"""The problem pass and the selection step change neither store nor the DiffSync diff (spec 019 T012, research R3).

DiffSync compares the stores as read. The problem pass reads the load reports, the stores, the
destination schema and the derived operations; the selection step removes operations and
relationship values from the plan only. So after `Potenda.write_plan` both stores and the diff
hold what they held before it, and a plan of valid data keeps its bytes.

Spec 019 T017 to T028 implement the behavior. Until then each test is an expected failure: the
fixture source needs the base-class read loop, and `DiffSyncMixin.model_loader` raises
`NotImplementedError`.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.plan.problem_plan import build_potenda, finish_plan, load_both_sides

if TYPE_CHECKING:
    from pathlib import Path

    from diffsync import Adapter

    from tests.plan.conftest import PlanProblemFixture

pytestmark = pytest.mark.xfail(
    strict=True,
    raises=NotImplementedError,
    reason="spec 019 US1 not implemented: DiffSyncMixin.model_loader has no default read loop (T017)",
)


def _store(adapter: Adapter, kinds: list[str]) -> dict[str, list[str]]:
    """Every stored record of `kinds`, as sorted canonical JSON of its model data."""
    return {
        kind: sorted(json.dumps(item.model_dump(), sort_keys=True, default=str) for item in adapter.get_all(kind))
        for kind in kinds
    }


def test_the_stores_and_the_diff_are_unchanged_by_the_problem_pass(
    plan_problems: PlanProblemFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both stores and the diff are the same before and after the plan leaves records out."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    potenda = build_potenda(plan_problems)
    load_both_sides(potenda)
    source_before = _store(potenda.source, plan_problems.order)
    destination_before = _store(potenda.destination, plan_problems.order)
    diff = potenda.diff()
    diff_before: dict[str, Any] = copy.deepcopy(diff.dict())

    planned = finish_plan(potenda, diff)

    # The plan left records out and kept others, so the problem pass and the selection step ran.
    assert planned.left_out
    assert planned.kept
    assert _store(potenda.source, plan_problems.order) == source_before
    assert _store(potenda.destination, plan_problems.order) == destination_before
    assert diff.dict() == diff_before
    assert potenda.diff().dict() == diff_before
