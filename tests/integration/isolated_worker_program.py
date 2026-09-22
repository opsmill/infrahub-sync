"""The isolated-worker program run as a subprocess by the handoff integration test.

``test_isolated_worker_handoff_integration.py`` runs each plan/verify/apply/sync stage in
its own interpreter, on real PostgreSQL and real S3-compatible storage. This module is that
interpreter's entire program: invoked as ``python isolated_worker_program.py <input-file>``,
it reads one serialized JSON input document, doubles the two surfaces this environment
cannot provide, and runs the product's real ``service_sync_run`` flow.

No product code is imported at module scope, so importing this file (for example to resolve
its path) never touches the product; every product import happens inside ``main()`` or a
method body, after the child process has already received its environment from the parent.

Seams the caller (``_run_worker`` in the integration test) relies on, enumerated here so a
future change cannot silently drop one:

- Monkeypatches, applied to ``infrahub_sync.service.flow`` and ``infrahub_sync.execution``
  inside the child, all five of them: ``stage_scratch``, ``build_runtime_model_plan``,
  ``_prefect_flow_run_id``, ``_require_current_worker_identity``, and
  ``get_potenda_from_instance``. Only the last of these is an extraction surface
  (``ExtractingEngine`` stands in for the source/destination the environment has none of);
  the other four exist because this environment has no Prefect server and no real scratch
  root to compare against.
- Environment canaries the parent sets and later inspects: ``INFRAHUB_SYNC_CACHE_DIR``
  (a retired setting that must stay unused) and a working directory that is deliberately
  not the stage root (a stage that reaches for either is what the test catches).
- ``INFRAHUB_SYNC_CONFIG_DIRECTORY``, ``INFRAHUB_API_TOKEN``, and ``NETBOX_TOKEN``, the
  environment the child needs to resolve its configuration and construct adapters.
- The serialized JSON input document (read from the file path given as ``sys.argv[1]``):
  ``report``, ``stage``, ``run_id``, ``binding``, ``flow_run_id``, ``worker_id``,
  ``fingerprint``, ``expected_checksum``, and ``confirm_writes``.
- The report file the child writes back (once mid-run, once at exit), whose schema the
  parent reads directly: ``stage``, ``pid``, ``dispatched``, ``scratch``, ``cwd``,
  ``input_path``, ``destination_constructed``, ``engine_built_in``, ``planned_in``,
  ``outcome``, ``ok``, ``error_type``, ``error``.
- The exit code: ``0`` if the run succeeded, ``1`` otherwise.
- The parent's subprocess timeout, which this module has no part in beyond finishing before
  the parent gives up.
- ``sys.path`` gains the repository root before any product import, since the child's
  working directory is deliberately not the repository checkout.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from infrahub_sync.plan.models import PlannedOperation

_REPORT: dict[str, Any] = {}


def _write_report(report_path: Path) -> None:
    """Serialize the current worker report to the parent-provided path."""
    report_path.write_text(json.dumps(_REPORT), encoding="utf-8")


class RecordingDestination:
    """The one adapter an apply constructs; no destination service exists here."""

    def __init__(self, **_kwargs: Any) -> None:  # noqa: ANN401 - the adapter's own kwargs shape.
        """Record that the apply stage constructed its destination adapter."""
        _REPORT["destination_constructed"] = True

    def new_peer_resolver(self) -> object:  # noqa: PLR6301 - fixed adapter signature.
        """Return the inert peer resolver required by the adapter interface."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401, ARG002, PLR6301
        """Record one dispatched operation and return its synthetic node identifier."""
        _REPORT["dispatched"].append(operation.operation_id)
        return "node-" + str(len(_REPORT["dispatched"]))


class ExtractingEngine:
    """The engine surface a plan touches, standing in for extraction only.

    It is constructed by the real ``execute_run``, in the run directory the real
    ``execute_run`` derived, and it writes the artifact the product's own writer produces.
    Everything around it -- the pipeline lock, the run sidecar, reading the saved plan back
    -- is the product's.
    """

    def __init__(
        self,
        run_directory: Path,
        config_version: Any,  # noqa: ANN401 - the product's own config-version type.
        *,
        configuration_binding: tuple[str, int, str],
        schema_fingerprint: str,
    ) -> None:
        """Store the run context needed to produce the plan artifact."""
        self.run_dir = run_directory
        self.run_id = run_directory.name
        self.top_level = ["BuiltinTag"]
        self.tiers = None
        self.force_full_extract = False
        self.cache_root = run_directory.parent
        self._config_version = config_version
        self._configuration_binding = configuration_binding
        self._schema_fingerprint = schema_fingerprint

    def load_both_sides(self) -> None:
        """The one boundary this environment cannot provide."""

    def diff(self) -> Any:  # noqa: ANN401, PLR6301 - stands in for the product's own diff type.
        """Return the empty difference used by this controlled extraction."""
        from types import SimpleNamespace

        return SimpleNamespace(rows=[], has_diffs=lambda: False, str=lambda: "extracted-diff")

    def _diff_to_rows(self, _diff: Any) -> list[Any]:  # noqa: ANN401, PLR6301
        """Convert the controlled empty difference into no report rows."""
        return []

    def write_plan(self, _diff: Any) -> None:  # noqa: ANN401
        """Write the source snapshots and the plan artifact this run hands on."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        from infrahub_sync.plan.checksum import source_snapshot_records
        from infrahub_sync.plan.models import PlannedOperation, SourceSnapshotRecord
        from infrahub_sync.plan.writer import write_plan_artifact
        from tests.plan.artifact_fixtures import operation_record

        (self.run_dir / "A").mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table({"name": ["prod"], "_extract_ts": ["2026-09-04T12:00:00+00:00"]}),
            self.run_dir / "A" / "tag.parquet",
        )
        write_plan_artifact(
            run_dir=self.run_dir,
            run_id=self.run_id,
            config_version=self._config_version,
            source_snapshot=[SourceSnapshotRecord(**record) for record in source_snapshot_records(self.run_dir)],
            deletes_computed=True,
            operations=[PlannedOperation.model_validate(operation_record(identity={"name": "prod"}))],
            configuration_binding=self._configuration_binding,
            schema_fingerprint=self._schema_fingerprint,
        )
        _REPORT["planned_in"] = str(self.run_dir)


def main() -> None:  # noqa: PLR0915 - one linear worker script, kept readable as a whole rather than split apart.
    """Read the serialized input, run one stage, and write the report back."""
    input_path = Path(sys.argv[1])
    payload = json.loads(input_path.read_text(encoding="utf-8"))

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    import infrahub_sync.execution as execution_module
    from infrahub_sync.plan.config_version import resolve_config_version
    from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
    from infrahub_sync.service import flow as service_flow
    from infrahub_sync.service.scratch import stage_scratch as real_stage_scratch

    report_path = Path(payload["report"])
    stage = payload["stage"]
    run_id = payload["run_id"]
    binding = tuple(payload["binding"])
    flow_run_id = payload["flow_run_id"]
    worker_id = payload["worker_id"]
    fingerprint = payload["fingerprint"]
    expected_checksum = payload.get("expected_checksum")
    confirm_writes = bool(payload.get("confirm_writes", False))

    _REPORT.update(
        {
            "stage": stage,
            "pid": os.getpid(),
            "dispatched": [],
            "scratch": None,
            "cwd": str(Path.cwd()),
            "input_path": str(input_path),
        }
    )

    def scratch(stage_name: str) -> Any:  # noqa: ANN401 - the product's own scratch context-manager type.
        """Record the private root this stage works in, then behave exactly as usual."""
        from contextlib import contextmanager

        @contextmanager
        def wrapped() -> Any:  # noqa: ANN401
            """Yield the real stage scratch while recording its private root."""
            with real_stage_scratch(stage_name) as value:
                _REPORT["scratch"] = str(value.root)
                _write_report(report_path)
                yield value

        return wrapped()

    def engine_factory(**kwargs: Any) -> ExtractingEngine:  # noqa: ANN401 - the product's own factory kwargs shape.
        """Build the extraction stand-in inside the directory the real engine chose."""
        from infrahub_sync.cache.paths import run_dir

        instance = kwargs["sync_instance"]
        base = Path(kwargs["base_directory"])
        assert base.is_absolute(), base
        directory = run_dir(instance.name, str(kwargs["run_id"]), base_directory=base)
        directory.mkdir(parents=True, exist_ok=True)
        _REPORT["engine_built_in"] = str(directory)
        return ExtractingEngine(
            directory,
            resolve_config_version(instance),
            configuration_binding=binding,
            schema_fingerprint=fingerprint,
        )

    def models(**_kwargs: Any) -> RuntimeModelPlan:  # noqa: ANN401 - the product's own model-builder kwargs shape.
        """Return the runtime model plan for the recording destination."""
        return RuntimeModelPlan(
            branch="main",
            schema_fingerprint=fingerprint,
            destination=RuntimeSideModels(adapter_class=RecordingDestination, models={}),
            source=None,
        )

    # `setattr` rather than plain assignment: these replace the product's own typed
    # functions with narrower stand-ins, exactly as `monkeypatch.setattr` does in the
    # in-process unit suites, but this script has no pytest fixture to call it with.
    setattr(service_flow, "stage_scratch", scratch)  # noqa: B010
    setattr(service_flow, "build_runtime_model_plan", models)  # noqa: B010
    setattr(service_flow, "_prefect_flow_run_id", lambda: flow_run_id)  # noqa: B010
    setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)  # noqa: B010
    setattr(execution_module, "get_potenda_from_instance", engine_factory)  # noqa: B010

    os.environ["PREFECT__WORKER_ID"] = worker_id

    try:
        result = service_flow.service_sync_run.fn(
            run_id,
            stage,
            binding[0],
            binding[1],
            binding[2],
            None,
            expected_checksum,
            confirm_writes,
        )
        _REPORT["outcome"] = result["outcome"]
        _REPORT["ok"] = True
    except BaseException as failure:  # noqa: BLE001 - the parent reads type/message for any child failure.
        _REPORT["ok"] = False
        _REPORT["error_type"] = type(failure).__name__
        _REPORT["error"] = str(failure)

    _write_report(report_path)
    sys.exit(0 if _REPORT["ok"] else 1)


if __name__ == "__main__":
    main()
