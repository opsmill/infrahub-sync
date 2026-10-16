"""Plan the problem fixture of `problem_fixtures` end to end (spec 009 User Story 1).

`plan_fixture` loads the fixture source and an in-memory destination through a real
`Potenda`, compares them, runs the problem pass and the selection step, and writes the plan
artifact. The destination records no writes: it has no write surface, and the plan stage
never calls one.

Not a test module: no assertions live here.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from infrahub_sync import SyncInstance
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.potenda import Potenda
from infrahub_sync.runtime_schema.domain import normalize_destination_schema
from infrahub_sync.runtime_schema.models import build_runtime_models
from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.plan.problem_fixtures import (
    ORDER,
    ProblemFixtureAdapter,
    ProblemFixtureModel,
    Variant,
    build_config,
    destination_records,
    schema_record,
    source_records,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from diffsync import DiffSyncModel

    from infrahub_sync import SchemaMappingModel
    from infrahub_sync.plan.models import PlanManifest, PlannedOperation, PlanReport

RUN_ID = "20261015T120000-problem"


class FixtureDestination(ProblemFixtureAdapter):
    """The destination side: its records are model data, and every write is recorded."""

    type = "Infrahub"

    def __init__(
        self,
        config: Any,  # noqa: ANN401 - a SyncConfig
        records: Mapping[str, list[dict[str, Any]]],
        schema: Mapping[str, Any],
    ) -> None:
        """Hold `records` as model data by kind."""
        super().__init__(target="destination", adapter=config.destination, config=config, records=records)
        self.writes: list[str] = []
        # The identity checks read the destination's schema, as they read Infrahub's.
        self.schema = dict(normalize_destination_schema(schema).kinds)

    def record_to_data(  # noqa: PLR6301 - overrides the fixture adapter's method
        self,
        record: Mapping[str, Any],
        element: SchemaMappingModel,  # noqa: ARG002
        model: type[DiffSyncModel],
    ) -> dict[str, Any]:
        """A destination record already holds model data."""
        return {name: value for name, value in record.items() if name in model.model_fields}

    def create(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Record a write; the plan stage must never reach it."""
        self.writes.append("create")

    def update(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Record a write; the plan stage must never reach it."""
        self.writes.append("update")

    def delete(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Record a write; the plan stage must never reach it."""
        self.writes.append("delete")


@dataclass
class PlannedFixture:
    """One plan of the fixture: the engine, both sides, the report and what was written."""

    potenda: Potenda
    source: ProblemFixtureAdapter
    destination: FixtureDestination
    report: PlanReport
    manifest: PlanManifest
    operations: list[PlannedOperation]
    run_dir: Path


def fixture_config() -> SyncInstance:
    """The fixture configuration as a parsed configuration instance."""
    return SyncInstance(**build_config().model_dump(), directory=".")


def plan_fixture(  # noqa: PLR0913 - one keyword per part of the fixture a test replaces
    run_dir: Path,
    variant: Variant = Variant.ALL,
    *,
    records: Mapping[str, list[dict[str, Any]]] | None = None,
    destination: Mapping[str, list[dict[str, Any]]] | None = None,
    source_class: type[ProblemFixtureAdapter] = ProblemFixtureAdapter,
    config: SyncInstance | None = None,
    schema: Mapping[str, Any] | None = None,
) -> PlannedFixture:
    """Plan `variant` (or `records`) against `destination` (the fixture's by default).

    `config` and `schema` replace the fixture configuration and destination schema record.
    """
    configuration = config or fixture_config()
    raw_schema = schema if schema is not None else schema_record()
    snapshot = normalize_destination_schema(raw_schema)

    def models() -> dict[str, type[DiffSyncModel]]:
        return build_runtime_models(snapshot=snapshot, configuration=configuration, model_base=ProblemFixtureModel)

    source = source_class(
        target="source",
        adapter=configuration.source,
        config=configuration,
        records=copy.deepcopy(dict(records)) if records is not None else source_records(variant),
    )
    bind_runtime_models(source, models())
    target = FixtureDestination(
        configuration, destination if destination is not None else destination_records(), raw_schema
    )
    bind_runtime_models(target, models())
    potenda = Potenda(
        source=source,
        destination=target,
        config=configuration,
        top_level=list(ORDER),
        run_dir=run_dir,
        run_id=RUN_ID,
        show_progress=False,
        concurrent_load=False,
    )
    potenda.schema_record = snapshot
    potenda.load()
    potenda.write_plan(potenda.diff())
    assert potenda.last_plan_report is not None
    loaded = load_plan_artifact(run_dir)
    return PlannedFixture(
        potenda=potenda,
        source=source,
        destination=target,
        report=potenda.last_plan_report,
        manifest=loaded.manifest,
        operations=loaded.operations,
        run_dir=run_dir,
    )


__all__ = ["RUN_ID", "FixtureDestination", "PlannedFixture", "fixture_config", "plan_fixture"]
