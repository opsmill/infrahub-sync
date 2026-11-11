"""Plan the shared problem fixture end to end (spec 019 User Story 1, T009 to T013).

Not a test module: it holds the adapters and the plan driver that the User Story 1 tests share.
No assertion lives here.

The two sides:

- `FixtureSource` is a source adapter that implements only `list_records` and `record_to_data`
  (spec 019 research R2). The base class's read loop (`DiffSyncMixin.model_loader`, T017) lists,
  filters, transforms and admits each record through `load_record`. References resolve through
  the shared helper `peer_unique_id` of `infrahub_sync/adapters/references.py` (T021).
- `FixtureDestination` is the Infrahub adapter itself, with a stand-in SDK client that returns
  the fixture's destination nodes. Its own `model_loader` runs, so the detection of
  `destination_identity_ambiguous` on a full read (T022) is the one a real plan runs.

`plan_fixture` loads both sides through a real `Potenda`, compares them and writes the plan
with `Potenda.write_plan`, the path a `plan` run takes. The tests read the result only from the
saved plan: `plan/manifest.json`, `plan/operations.jsonl` and `plan/report.jsonl` (format 4,
contracts/plan-artifact-format-4.md).

The read uses `DiffSyncMixin.model_loader` and `load_record` (T017), the reference helper
`infrahub_sync.adapters.references.peer_unique_id` (T021) and `adapter.load_report`, the
`LoadReport` of `infrahub_sync/load_report.py` that the read loop writes to.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, get_args

from diffsync import Adapter

from infrahub_sync import DiffSyncMixin, SyncInstance
from infrahub_sync.adapters.infrahub import InfrahubAdapter, InfrahubModel
from infrahub_sync.adapters.references import peer_unique_id
from infrahub_sync.adapters.utils import get_value
from infrahub_sync.cache.paths import cache_root_for
from infrahub_sync.plan.reader import load_plan_artifact
from infrahub_sync.plan.writer import PLAN_DIR_NAME
from infrahub_sync.potenda import Potenda
from infrahub_sync.runtime_schema.models import build_runtime_models
from infrahub_sync.runtime_schema.worker import RuntimeModelPlan, RuntimeSideModels, bind_runtime_models
from tests.plan.conftest import PlanProblemFixture, PlanProblemModel, Variant

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

    from diffsync import DiffSyncModel

    from infrahub_sync import SchemaMappingModel, SyncConfig
    from infrahub_sync.plan.models import PlanManifest, PlannedOperation

RUN_ID = "20261111T120000-01900009"

REPORT_FILE_NAME = "report.jsonl"

# A consumed-schema fingerprint for the run's runtime models; its value is not checked here.
SCHEMA_FINGERPRINT = "0" * 64


def reference_is_required(model: type[DiffSyncModel], field_name: str) -> bool:
    """Tell whether a relationship field needs a peer.

    It does when its annotation does not accept `None`, or when it is one of the mapping's
    identifiers: a record cannot form its identity without that peer.
    """
    model_field = model.model_fields.get(field_name)
    if model_field is None:
        return False
    return field_name in getattr(model, "_identifiers", ()) or type(None) not in get_args(model_field.annotation)


class FixtureSource(DiffSyncMixin, Adapter):
    """In-memory source adapter that implements `list_records` and `record_to_data` only."""

    type = "ProblemFixture"

    def __init__(self, *, config: SyncConfig, records: Mapping[str, list[dict[str, Any]]], order: list[str]) -> None:
        """Hold the raw `records` by kind."""
        super().__init__()
        # An instance attribute, as `Potenda` sets it: the runtime models are bound per instance.
        self.top_level = list(order)  # ty: ignore[invalid-attribute-access]
        self.target = "source"
        self.config = config
        self._records = copy.deepcopy(dict(records))

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        """Return copies of the raw records of `model_name`, before any filter."""
        return copy.deepcopy(self._records.get(model_name, []))

    def record_to_data(
        self, record: Mapping[str, Any], element: SchemaMappingModel, model: type[DiffSyncModel]
    ) -> dict[str, Any]:
        """Convert one raw record to model data; a reference resolves through `peer_unique_id`."""
        source_id = str(record["id"])
        data: dict[str, Any] = {"local_id": source_id}
        for mapped in element.fields:
            if mapped.mapping is None:
                continue
            value = get_value(record, mapped.mapping)
            if mapped.reference is None:
                data[mapped.name] = value
                continue
            if not value:
                continue
            data[mapped.name] = self._peer(
                mapped.reference,
                str(value["id"]),
                owner=(element.name, source_id),
                field=mapped.name,
                required=reference_is_required(model, mapped.name),
            )
        return data

    def _peer(self, peer_kind: str, peer_source_id: str, *, owner: tuple[str, str], field: str, required: bool) -> Any:  # noqa: ANN401
        """Resolve one reference through the shared helper of T021."""
        return peer_unique_id(self, peer_kind, peer_source_id, owner=owner, field=field, required=required)


@dataclass
class _StandInNode:
    """One Infrahub node as the stand-in client returns it."""

    id: str
    kind: str
    data: dict[str, Any]

    def get_kind(self) -> str:
        return self.kind


class _StandInStore:
    """Stand-in for the SDK client's node store."""

    def __init__(self) -> None:
        self.keys: list[tuple[str, str]] = []

    def set(self, *, key: str, node: _StandInNode) -> None:
        self.keys.append((node.get_kind(), key))


class _StandInClient:
    """Stand-in for the SDK client: `all` returns the fixture's nodes of a kind; it cannot write."""

    def __init__(self, nodes: Mapping[str, list[dict[str, Any]]]) -> None:
        self.store = _StandInStore()
        self._nodes = {kind: copy.deepcopy(rows) for kind, rows in nodes.items()}

    def all(self, *, kind: str, **_options: Any) -> list[_StandInNode]:  # noqa: ANN401
        return [_StandInNode(id=row["local_id"], kind=kind, data=dict(row)) for row in self._nodes.get(kind, [])]


class FixtureDestination(InfrahubAdapter):
    """The Infrahub adapter, reading the fixture's destination nodes through a stand-in client.

    The stand-in client has no write method, so a write during the plan fails the test.
    """

    def __init__(
        self, *, config: SyncConfig, nodes: Mapping[str, list[dict[str, Any]]], schema: Mapping[str, Any]
    ) -> None:
        """Bypass `InfrahubAdapter.__init__`, which connects to Infrahub."""
        Adapter.__init__(self)
        self.target = "destination"
        self.config = config
        self.client = _StandInClient(nodes)
        # The identity checks of the plan read the destination schema, as they read Infrahub's.
        self.schema = dict(schema)
        self._peer_unique_ids = {}

    def resolve_lineage(self, *, saved_plan: PlanManifest | None = None) -> Any:  # noqa: ANN401, ARG002
        """Record no lineage: the plan of the fixture writes none."""
        self.lineage = None
        return None

    def resolve_metadata_read(self) -> None:
        """Read no metadata: the stand-in nodes record no last change."""
        self.metadata_read = False

    def infrahub_node_to_diffsync(self, node: Any, *, mapped: Any = None) -> dict[str, Any]:  # noqa: ANN401, ARG002, PLR6301 - overrides the adapter method
        """A stand-in node already holds model data."""
        return dict(node.data)


@dataclass
class PlannedFixture:
    """One plan of the fixture, read back from the saved plan."""

    potenda: Potenda
    source: Any
    destination: Any
    run_dir: Path
    manifest: dict[str, Any]
    report_bytes: bytes
    report: list[dict[str, Any]] = field(default_factory=list)

    @property
    def loaded_manifest(self) -> PlanManifest:
        """The manifest as the plan reader validates it."""
        return load_plan_artifact(self.run_dir).manifest

    @property
    def operations(self) -> list[PlannedOperation]:
        """The planned operations, as the plan reader validates them."""
        return load_plan_artifact(self.run_dir).operations

    def entries(self, entry: str) -> list[dict[str, Any]]:
        """The report lines whose `entry` is `problem`, `left_out` or `kept`."""
        return [line for line in self.report if line.get("entry") == entry]

    @property
    def problems(self) -> list[dict[str, Any]]:
        return self.entries("problem")

    @property
    def left_out(self) -> list[dict[str, Any]]:
        return self.entries("left_out")

    @property
    def kept(self) -> list[dict[str, Any]]:
        return self.entries("kept")


def fixture_instance(fixture: PlanProblemFixture) -> SyncInstance:
    """The fixture configuration as a parsed configuration instance."""
    return SyncInstance(**copy.deepcopy(fixture.config_data), directory=".")


def build_source(
    fixture: PlanProblemFixture,
    records: Mapping[str, list[dict[str, Any]]],
    *,
    config: SyncConfig | None = None,
) -> FixtureSource:
    """A source adapter holding `records`, with the fixture's runtime models bound."""
    configuration = config or fixture_instance(fixture)
    source = FixtureSource(config=configuration, records=records, order=fixture.order)
    bind_runtime_models(source, fixture.build_models(model_base=PlanProblemModel, config=configuration))
    return source


def build_destination(
    fixture: PlanProblemFixture,
    nodes: Mapping[str, list[dict[str, Any]]],
    *,
    config: SyncConfig | None = None,
) -> FixtureDestination:
    """The Infrahub adapter holding `nodes`, with the fixture's runtime models bound."""
    configuration = config or fixture_instance(fixture)
    destination = FixtureDestination(config=configuration, nodes=nodes, schema=fixture.snapshot().kinds)
    destination.top_level = fixture.order  # ty: ignore[invalid-attribute-access]
    bind_runtime_models(destination, fixture.build_models(model_base=InfrahubModel, config=configuration))
    return destination


def _runtime_model_plan(fixture: PlanProblemFixture, configuration: SyncInstance) -> RuntimeModelPlan:
    """The runtime model plan a registered run builds from the destination schema snapshot."""
    snapshot = fixture.snapshot()

    def models(model_base: type[DiffSyncModel]) -> Mapping[str, type[DiffSyncModel]]:
        return build_runtime_models(snapshot=snapshot, configuration=configuration, model_base=model_base)

    return RuntimeModelPlan(
        branch="main",
        schema_fingerprint=SCHEMA_FINGERPRINT,
        destination=RuntimeSideModels(adapter_class=FixtureDestination, models=models(InfrahubModel)),
        source=RuntimeSideModels(adapter_class=FixtureSource, models=models(PlanProblemModel)),
        generic_peers=snapshot.generic_peers,
    )


def build_potenda(  # noqa: PLR0913 - one keyword per part of the fixture a test replaces
    fixture: PlanProblemFixture,
    variant: Variant = Variant.ALL,
    *,
    records: Mapping[str, list[dict[str, Any]]] | None = None,
    destination: Mapping[str, list[dict[str, Any]]] | None = None,
    schema_snapshot: bool = True,
    run_id: str = RUN_ID,
) -> Potenda:
    """A `Potenda` over the fixture's source records of `variant` (or `records`) and its destination.

    `destination` replaces the nodes Infrahub holds; by default they are the fixture's for those
    records. `schema_snapshot=False` builds the run without a destination schema snapshot, as a
    direct run does, so the plan cannot check Dropdown values.
    """
    configuration = fixture_instance(fixture)
    if schema_snapshot:
        configuration._runtime_models = _runtime_model_plan(fixture, configuration)
    raw_records = copy.deepcopy(dict(records)) if records is not None else fixture.source_records(variant)
    nodes = destination if destination is not None else fixture.destination_records(raw_records)
    source = build_source(fixture, raw_records, config=configuration)
    target = build_destination(fixture, nodes, config=configuration)
    run_dir = cache_root_for(configuration.name) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return Potenda(
        source=source,
        destination=target,
        config=configuration,
        top_level=fixture.order,
        run_dir=run_dir,
        run_id=run_id,
        show_progress=False,
        concurrent_load=False,
    )


def read_report(run_dir: Path) -> bytes:
    """The bytes of `plan/report.jsonl`."""
    return (run_dir / PLAN_DIR_NAME / REPORT_FILE_NAME).read_bytes()


def report_lines(report_bytes: bytes) -> list[dict[str, Any]]:
    """The entries of a plan report, one JSON object per line."""
    return [json.loads(line) for line in report_bytes.splitlines() if line]


def report_digest(report_bytes: bytes) -> str:
    """The SHA-256 hex digest the manifest records for the report."""
    return hashlib.sha256(report_bytes).hexdigest()


def load_both_sides(potenda: Potenda) -> None:
    """Load both sides through `Potenda.load_both_sides`."""
    potenda.load_both_sides()


def finish_plan(potenda: Potenda, diff: Any = None) -> PlannedFixture:  # noqa: ANN401 - a DiffSync Diff
    """Write the plan of `diff` (by default, a new comparison of both loaded sides) and read it back."""
    potenda.write_plan(diff if diff is not None else potenda.diff())
    run_dir = potenda.run_dir
    assert run_dir is not None
    manifest = json.loads((run_dir / PLAN_DIR_NAME / "manifest.json").read_text(encoding="utf-8"))
    report_bytes = read_report(run_dir)
    return PlannedFixture(
        potenda=potenda,
        source=potenda.source,
        destination=potenda.destination,
        run_dir=run_dir,
        manifest=manifest,
        report_bytes=report_bytes,
        report=report_lines(report_bytes),
    )


def plan_fixture(  # noqa: PLR0913 - one keyword per part of the fixture a test replaces
    fixture: PlanProblemFixture,
    variant: Variant = Variant.ALL,
    *,
    records: Mapping[str, list[dict[str, Any]]] | None = None,
    destination: Mapping[str, list[dict[str, Any]]] | None = None,
    schema_snapshot: bool = True,
    run_id: str = RUN_ID,
) -> PlannedFixture:
    """Plan `variant` (or `records`) of the fixture end to end; see `build_potenda`."""
    potenda = build_potenda(
        fixture,
        variant,
        records=records,
        destination=destination,
        schema_snapshot=schema_snapshot,
        run_id=run_id,
    )
    load_both_sides(potenda)
    return finish_plan(potenda)


def source_ids(reference: Mapping[str, Any] | None) -> str | None:
    """The source ID of a source record reference."""
    return None if reference is None else reference.get("source_id")


def record_key(entry: Mapping[str, Any]) -> tuple[str, str | None]:
    """The kind and source ID of a report entry's record."""
    return entry["record"]["kind"], entry["record"]["source_id"]


def problem_source_ids(problem: Mapping[str, Any]) -> list[str]:
    """The source IDs of a problem's record and of its other records, sorted."""
    ids = [problem["record"]["source_id"], *(other["source_id"] for other in problem.get("other_records") or [])]
    return sorted(str(value) for value in ids)


def operations_of(operations: list[PlannedOperation], kind: str) -> Iterator[PlannedOperation]:
    """The planned operations of `kind`."""
    return (operation for operation in operations if operation.kind == kind)


__all__ = [
    "RUN_ID",
    "FixtureDestination",
    "FixtureSource",
    "PlannedFixture",
    "build_destination",
    "build_potenda",
    "build_source",
    "finish_plan",
    "fixture_instance",
    "load_both_sides",
    "operations_of",
    "plan_fixture",
    "problem_source_ids",
    "read_report",
    "record_key",
    "reference_is_required",
    "report_digest",
    "report_lines",
    "source_ids",
]
