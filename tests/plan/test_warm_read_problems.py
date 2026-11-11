"""A warm source read reports the same problems as a cold read (spec 019 task T081).

A warm read takes the records the previous successful run admitted from its source snapshot
and reads only the records changed since the saved cursor (`Potenda.load_one_side`). Each
test plans the fixture twice: a first run that is retained, then a warm run. It compares the
warm run's plan problems with those of a cold read of the same source records.

`WarmFixtureSource` reads the changed records as the bundled NetBox and Nautobot adapters do
(`list_changed_since`: filter, transform and convert each record), and advertises a safe cursor
for every kind, which no bundled adapter does today.
"""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.cache.paths import cache_root_for
from infrahub_sync.potenda import Potenda, SideLoadError
from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.plan.conftest import PlanProblemModel, Variant
from tests.plan.problem_plan import (
    FixtureSource,
    _runtime_model_plan,
    build_destination,
    finish_plan,
    fixture_instance,
    plan_fixture,
    problem_source_ids,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from infrahub_sync import SyncConfig
    from tests.plan.conftest import PlanProblemFixture
    from tests.plan.problem_plan import PlannedFixture

pytestmark = [pytest.mark.usefixtures("_cache_dir")]

# T081 is blocked: a warm source read cannot match a cold read without a cache format change.
# The source snapshot holds no source record ID (`local_id` is written for side B only), so a
# changed record cannot resolve an unchanged peer nor tell a duplicate from a repeated record,
# and the records a previous run left out are in neither the snapshot nor the changed records.
BLOCKED = "spec 019 T081 is blocked: the source snapshot holds no source record ID"

SCHEMA_SUBHASH = "matching-schema"
SAFE_CURSOR = CursorState(CursorTier.TIMESTAMP, "2026-11-11T00:00:00+00:00", safe=True)


@pytest.fixture
def _cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


class WarmFixtureSource(FixtureSource):
    """The fixture source with a safe cursor for every kind and a list of changed records."""

    def __init__(
        self,
        *,
        config: SyncConfig,
        records: Mapping[str, list[dict[str, Any]]],
        changed: Mapping[str, list[dict[str, Any]]],
        order: list[str],
    ) -> None:
        """Hold every raw record and the raw records changed since the saved cursor."""
        super().__init__(config=config, records=records, order=order)
        self._changed = copy.deepcopy(dict(changed))

    def cursor_tier_for(self, model_name: str) -> CursorTier:  # noqa: ARG002, PLR6301  # pylint: disable=unused-argument
        """Every kind reads by a timestamp cursor."""
        return CursorTier.TIMESTAMP

    def safe_cursor_before_load(self, model_name: str) -> CursorState:  # noqa: ARG002, PLR6301  # pylint: disable=unused-argument
        """Every kind has a safe cursor, so a retained run makes the next read warm."""
        return SAFE_CURSOR

    def list_changed_since(self, model_name: str, cursor: CursorState) -> Iterator[dict[str, Any]]:  # noqa: ARG002  # pylint: disable=unused-argument
        """Filter, transform and convert the records changed since `cursor`, as NetBox and Nautobot do."""
        element = next((entry for entry in self.config.schema_mapping if entry.name == model_name), None)
        if element is None:
            return
        model: type[PlanProblemModel] = getattr(self, model_name)
        records = copy.deepcopy(self._changed.get(model_name, []))
        filtered = model.filter_records(records=records, schema_mapping=element)
        for record in model.transform_records(records=filtered, schema_mapping=element):
            data = self.record_to_data(record, element, model)
            if data is not None:
                yield data


def _potenda(
    fixture: PlanProblemFixture,
    *,
    records: Mapping[str, list[dict[str, Any]]],
    changed: Mapping[str, list[dict[str, Any]]],
    run_id: str,
) -> Potenda:
    """A `Potenda` whose source can read warm; the destination holds the nodes of `records`."""
    configuration = fixture_instance(fixture)
    configuration._runtime_models = _runtime_model_plan(fixture, configuration)
    source = WarmFixtureSource(config=configuration, records=records, changed=changed, order=fixture.order)
    bind_runtime_models(source, fixture.build_models(model_base=PlanProblemModel, config=configuration))
    target = build_destination(fixture, fixture.destination_records(dict(records)), config=configuration)
    cache_root = cache_root_for(configuration.name)
    run_dir = cache_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return Potenda(
        source=source,
        destination=target,
        config=configuration,
        top_level=fixture.order,
        run_dir=run_dir,
        run_id=run_id,
        cache_root=cache_root,
        schema_subhash=SCHEMA_SUBHASH,
        show_progress=False,
        concurrent_load=False,
    )


def _retain(potenda: Potenda) -> None:
    """Record the run as successful, with its cursors, so the next run reads warm."""
    assert potenda.run_dir is not None
    potenda.persist_cursors_for_run(side="A")
    potenda.persist_cursors_for_run(side="B")
    (potenda.run_dir / "run.json").write_text(json.dumps({"status": "dry-run"}), encoding="utf-8")
    (potenda.run_dir / "schema-sub-hash.txt").write_text(SCHEMA_SUBHASH, encoding="utf-8")


def _warm_plan(
    fixture: PlanProblemFixture,
    *,
    first: Mapping[str, list[dict[str, Any]]],
    records: Mapping[str, list[dict[str, Any]]],
    changed: Mapping[str, list[dict[str, Any]]],
) -> PlannedFixture:
    """Retain a run over `first`, then plan a warm run over `records` that reads `changed`."""
    previous = _potenda(fixture, records=first, changed={}, run_id="20261111T100000-warm0001")
    previous.load_both_sides()
    previous.write_plan(previous.diff())
    _retain(previous)
    warm = _potenda(fixture, records=records, changed=changed, run_id="20261111T110000-warm0002")
    warm.load_both_sides()
    assert warm._side_full_extract["A"] is False, "the second read of the source must be warm"
    return finish_plan(warm)


def _problems(planned: PlannedFixture) -> list[tuple[str, str, list[str]]]:
    """Each plan problem as its type, kind and source IDs, sorted."""
    return sorted(
        (problem["problem_type"], problem["record"]["kind"], problem_source_ids(problem))
        for problem in planned.problems
    )


def _left_out(planned: PlannedFixture) -> list[tuple[str, str | None]]:
    """The kind and source ID of each record the plan leaves out, sorted."""
    return sorted((entry["record"]["kind"], entry["record"]["source_id"]) for entry in planned.left_out)


def _with(records: Mapping[str, list[dict[str, Any]]], extra: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    merged = copy.deepcopy(dict(records))
    for kind, rows in extra.items():
        merged[kind] = [*merged.get(kind, []), *copy.deepcopy(rows)]
    return merged


def _changed_groups(fixture: PlanProblemFixture) -> dict[str, list[dict[str, Any]]]:
    """The records of the full fixture that the valid records do not hold."""
    valid = fixture.source_records(Variant.NO_PROBLEMS)
    every = fixture.source_records(Variant.ALL)
    return {kind: [row for row in rows if row not in valid[kind]] for kind, rows in every.items()}


@pytest.mark.xfail(strict=True, raises=SideLoadError, reason=BLOCKED)
def test_warm_read_of_changed_problem_records_reports_the_cold_problems(plan_problems: PlanProblemFixture) -> None:
    """The problem records and their dependents arrive as changed records after a clean run."""
    every = plan_problems.source_records(Variant.ALL)
    cold = plan_fixture(plan_problems, Variant.ALL, run_id="20261111T090000-cold0001")
    warm = _warm_plan(
        plan_problems,
        first=plan_problems.source_records(Variant.NO_PROBLEMS),
        records=every,
        changed=_changed_groups(plan_problems),
    )
    assert _problems(warm) == _problems(cold)
    assert _left_out(warm) == _left_out(cold)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=BLOCKED)
def test_warm_read_with_no_changes_reports_the_cold_problems(plan_problems: PlanProblemFixture) -> None:
    """Nothing changed since a run that found the fixture's problems."""
    every = plan_problems.source_records(Variant.ALL)
    cold = plan_fixture(plan_problems, Variant.ALL, run_id="20261111T090000-cold0001")
    warm = _warm_plan(plan_problems, first=every, records=every, changed={})
    assert _problems(warm) == _problems(cold)
    assert _left_out(warm) == _left_out(cold)


@pytest.mark.xfail(strict=True, raises=SideLoadError, reason=BLOCKED)
def test_changed_record_resolves_an_unchanged_peer(plan_problems: PlanProblemFixture) -> None:
    """A new device at the unchanged site `paris` (source ID 1) is planned, with no problem."""
    valid = plan_problems.source_records(Variant.NO_PROBLEMS)
    device = {"id": 37, "name": "leaf-7", "serial": "L7", "position": 7, "role": "leaf", "site": {"id": 1}}
    records = _with(valid, {"DcimDevice": [device]})
    cold = plan_fixture(plan_problems, records=records, run_id="20261111T090000-cold0001")
    warm = _warm_plan(plan_problems, first=valid, records=records, changed={"DcimDevice": [device]})
    assert _problems(warm) == _problems(cold) == []
    assert any(item.local_id == "37" for item in warm.source.get_all("DcimDevice"))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=BLOCKED)
def test_changed_record_with_the_identity_of_an_unchanged_record_is_a_duplicate(
    plan_problems: PlanProblemFixture,
) -> None:
    """A new site named `paris`, the name of the unchanged site 1, is a duplicate source identity."""
    valid = plan_problems.source_records(Variant.NO_PROBLEMS)
    site = {"id": 7, "name": "paris", "status": "active"}
    records = _with(valid, {"LocationSite": [site]})
    cold = plan_fixture(plan_problems, records=records, run_id="20261111T090000-cold0001")
    warm = _warm_plan(plan_problems, first=valid, records=records, changed={"LocationSite": [site]})
    assert ("duplicate_source_identity", "LocationSite", ["1", "7"]) in _problems(cold)
    assert _problems(warm) == _problems(cold)
