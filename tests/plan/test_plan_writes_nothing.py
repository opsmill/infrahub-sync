"""A plan writes nothing to the destination, whether or not it finds problems (spec 019 T079).

Old specification 009 stated this as FR-004. `feature/v3-develop` proves it for deletes
(`tests/test_effective_diffsync_flags.py::test_plan_records_delete_without_dispatching_model_delete`)
and for a review (`tests/plan/test_review.py`), but no test planned creates, updates and
problems together and checked that the destination received no write. This test plans the
fixture of spec 019 with and without its problem records, through the real `InfrahubAdapter`,
and records every write path the apply uses: the SDK client's write methods, the adapter's
`apply_planned_operation`, and the DiffSync model hooks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel
from tests.plan.conftest import Variant
from tests.plan.problem_plan import build_potenda, finish_plan, load_both_sides

if TYPE_CHECKING:
    from pathlib import Path

    from tests.plan.conftest import PlanProblemFixture

# The SDK client methods a destination write goes through.
_CLIENT_WRITE_METHODS = frozenset({"create", "execute_graphql", "delete", "save", "upsert", "update"})


class _RecordingClient:
    """Wraps the stand-in SDK client and records each write method the adapter looks up."""

    def __init__(self, client: Any, writes: list[str]) -> None:  # noqa: ANN401 - the stand-in client
        self._client = client
        self._writes = writes

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - forwards any client attribute
        if name in _CLIENT_WRITE_METHODS:
            self._writes.append(f"client.{name}")
        return getattr(self._client, name)


@pytest.mark.parametrize("variant", [Variant.ALL, Variant.NO_PROBLEMS], ids=["with-problems", "no-problems"])
def test_a_plan_writes_nothing_to_the_destination(
    plan_problems: PlanProblemFixture, variant: Variant, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan holds operations, and the destination received no write while it was made."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    writes: list[str] = []

    def record(name: str) -> Any:  # noqa: ANN401 - a replacement for any write hook
        def recorded(*_args: Any, **_kwargs: Any) -> None:  # noqa: ANN401
            writes.append(name)

        return recorded

    monkeypatch.setattr(InfrahubAdapter, "apply_planned_operation", record("adapter.apply_planned_operation"))
    monkeypatch.setattr(InfrahubModel, "create", classmethod(record("model.create")))
    monkeypatch.setattr(InfrahubModel, "update", record("model.update"))
    monkeypatch.setattr(InfrahubModel, "delete", record("model.delete"))

    potenda = build_potenda(plan_problems, variant)
    destination = potenda.destination
    assert isinstance(destination, InfrahubAdapter)
    destination.client = _RecordingClient(destination.client, writes)  # ty: ignore[invalid-assignment]
    load_both_sides(potenda)
    planned = finish_plan(potenda)

    assert planned.operations, "the plan must hold operations, or it proves nothing about writes"
    if variant is Variant.ALL:
        assert planned.problems, "the plan with the problem records must list problems"
    else:
        assert not planned.problems
    assert writes == []
