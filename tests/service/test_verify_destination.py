"""`verify` runs every pre-write check `apply` runs, against the destination, and writes nothing.

The parity cases (SC-004) break one check at a time and drive the real `verify` and `apply`
stages over the same retained plan: both fail, and both name the same check. The stage
cases drive the whole worker flow and read what the run records.

`execute_run` and `PlanApplier` are the real ones. Only the schema read (through
`build_runtime_model_plan`) and the destination adapter class are doubles, so every check
between the checkpoint and the first write runs as it does in production.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from prefect.client.schemas.objects import State

from infrahub_sync.adapters.infrahub import InfrahubAdapter
from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.errors import ImportAccountMismatchError, ReviewedPayloadFieldMissingError, failed_checks
from infrahub_sync.plan.models import DestinationBindingRecord, PlanManifest, PlannedOperation, SourceSnapshotRecord
from infrahub_sync.plan.ownership import WriteDispatchTracker
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME, write_plan_artifact
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.runtime_schema import DestinationSchemaUnavailableError, RuntimeModelPlan, RuntimeSideModels
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.scratch import stage_scratch
from tests.configuration.validation_packages import package
from tests.plan.artifact_fixtures import tamper_with_operations, tamperable_operation
from tests.service.execution_fixtures import append_execution, bind_granting_guard, publish_plan_directory

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from infrahub_sync.adapters.infrahub import PeerResolver, ResolvedLineage

FLOW_RUN_ID = "5d0c1e9a-7b2f-4c1d-9e3a-2b4c6d8e0f12"
WORKER_ID = "0f9e8d7c-6b5a-4c3d-8e2f-1a0b9c8d7e6f"
RUN_ID = "verify-destination"
SCHEMA_FINGERPRINT = "d" * 64
RECORDED_URL = "https://destination.invalid"
NETBOX_CANARY = "verify-netbox-canary"
INFRAHUB_CANARY = "verify-infrahub-canary"

# Every check a registered verify reports on an Infrahub-like destination that records lineage
# and its destination binding, in the order the data model lists them.
ALL_CHECKS = [
    "checkpoint",
    "run_binding",
    "plan_checksum",
    "source_snapshot",
    "config_version",
    "format_version",
    "destination_schema",
    "destination_binding",
    "write_surface",
    "planned_fields",
    "lineage",
]


class _Destination(InfrahubAdapter):
    """The destination double: the real Infrahub class, so the planned-field check runs on it.

    Constructed the way `PlanApplier.open_existing` constructs a destination, with nothing
    behind it. Every write is recorded, so "nothing was written" is a list that stays empty.
    """

    writes: list[str]
    live_url = RECORDED_URL
    missing_field = False
    refuse_lineage = False

    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401 # pylint: disable=super-init-not-called
        del kwargs
        self.writes = type(self).writes

    @property
    def destination_binding(self) -> DestinationBindingRecord:
        return DestinationBindingRecord(url=self.live_url, branch="main")

    def validate_planned_payload_fields(self, operations: object) -> None:
        del operations
        if self.missing_field:
            msg = "The live schema of kind 'BuiltinTag' has no field 'description'."
            raise ReviewedPayloadFieldMissingError(msg)

    def resolve_lineage(self, *, saved_plan: PlanManifest | None = None) -> ResolvedLineage:
        del saved_plan
        if self.refuse_lineage:
            msg = "The import account 'other' is not the account 'sync' the plan recorded."
            raise ImportAccountMismatchError(msg)
        return cast("ResolvedLineage", None)

    def new_peer_resolver(self) -> PeerResolver:  # noqa: PLR6301
        return cast("PeerResolver", object())

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: object) -> str:
        del peers
        self.writes.append(operation.operation_id)
        return "node"


class _Surfaceless:
    """A destination with no planned-write surface."""

    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
        del kwargs


@dataclass
class _Harness:
    tmp_path: Path
    projection: Any
    binding: tuple[str, int, str]
    run_dir: Path
    writes: list[str]
    # The adapter class the next stage constructs; a case may swap it or set its switches.
    destination: Any
    live_fingerprint: str = SCHEMA_FINGERPRINT
    schema_unavailable: bool = False

    @property
    def recorded_checksum(self) -> str:
        return str(self._manifest()["plan_checksum"])

    def _manifest(self) -> dict[str, Any]:
        return json.loads((self.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME).read_bytes())

    def rewrite_manifest(self, **changes: Any) -> None:  # noqa: ANN401
        """Re-record the manifest with `changes`, its own checksum repaired, so only they differ."""
        mapping = {**self._manifest(), **changes}
        plan_dir = self.run_dir / PLAN_DIR_NAME
        mapping["plan_checksum"] = compute_plan_checksum(mapping, (plan_dir / OPERATIONS_FILE_NAME).read_bytes())
        (plan_dir / MANIFEST_FILE_NAME).write_bytes(canonical_json_bytes(mapping))


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, purpose: str = "verify") -> _Harness:
    """Register one configuration and retain one plan against it, bound to its destination."""
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN", NETBOX_CANARY)
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", INFRAHUB_CANARY)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    projection = local_product_projection(tmp_path / "product")
    registered = projection.create_configuration(package())
    binding = (registered.config_id, registered.registry_version, registered.package_checksum)
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="plan",
            configuration_reference=f"{binding[0]}@{binding[1]}",
            config_id=binding[0],
            registry_version=binding[1],
            package_checksum=binding[2],
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="planned",
        )
    )
    append_execution(
        projection,
        RUN_ID,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose=purpose, attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )
    stored = projection.lookup_configuration_version(binding[0], binding[1]).value
    assert stored is not None
    runtime = resolve_runtime_instance(
        ConfigurationPackage.model_validate(stored.declared_content), directory=str(tmp_path)
    )
    runtime._configuration_binding = binding
    run_dir = tmp_path / "authored" / runtime.name / RUN_ID
    write_plan_artifact(
        run_dir=run_dir,
        run_id=RUN_ID,
        config_version=resolve_config_version(runtime),
        source_snapshot=[],
        deletes_computed=True,
        operations=[PlannedOperation.model_validate(tamperable_operation())],
        configuration_binding=binding,
        schema_fingerprint=SCHEMA_FINGERPRINT,
        destination_binding=DestinationBindingRecord(url=RECORDED_URL, branch="main"),
    )
    writes: list[str] = []
    destination = type("Destination", (_Destination,), {"writes": writes})
    harness = _Harness(
        tmp_path=tmp_path,
        projection=projection,
        binding=binding,
        run_dir=run_dir,
        writes=writes,
        destination=destination,
    )

    def build_runtime_model_plan(**kwargs: Any) -> RuntimeModelPlan:  # noqa: ANN401
        assert kwargs["scope"] == "destination", "verify and apply build the destination scope only"
        if harness.schema_unavailable:
            raise DestinationSchemaUnavailableError(reason="timeout", branch="main")
        return RuntimeModelPlan(
            branch="main",
            schema_fingerprint=harness.live_fingerprint,
            destination=RuntimeSideModels(adapter_class=harness.destination, models={}),
            source=None,
        )

    monkeypatch.setattr(service_flow, "build_runtime_model_plan", build_runtime_model_plan)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
    return harness


def _execute(harness: _Harness, stage: Literal["verify", "apply"], *, approved: str) -> dict[str, Any]:
    """Drive one stage at the production boundary, before remote exception sanitization."""
    publish_plan_directory(harness.projection, RUN_ID, harness.run_dir)
    with stage_scratch(stage) as scratch:
        result, _ = service_flow._execute_stage(
            RUN_ID,
            stage,
            *harness.binding,
            None,
            approved if stage == "apply" else None,
            confirm_writes=stage == "apply",
            secrets=[],
            config_directory=str(harness.tmp_path),
            projection=harness.projection,
            tracker=WriteDispatchTracker(),
            scratch=scratch,
            flow_run_id=FLOW_RUN_ID,
        )
    return result


# ======================================================================================
# SC-004: verify and apply fail on the same named check
# ======================================================================================


def _break_run_binding(harness: _Harness) -> None:
    harness.rewrite_manifest(run_id="another-run")


def _break_configuration_binding(harness: _Harness) -> None:
    # The plan names another version of the registered configuration than the run does.
    harness.rewrite_manifest(registry_version=harness.binding[1] + 1)


def _break_plan_checksum(harness: _Harness) -> None:
    tamper_with_operations(harness.run_dir)


def _break_source_snapshot(harness: _Harness) -> None:
    record = SourceSnapshotRecord(path="A/resource0.parquet", digest="0" * 64, row_count=1)
    harness.rewrite_manifest(source_snapshot=[record.model_dump(mode="json")])


def _break_config_version(harness: _Harness) -> None:
    harness.rewrite_manifest(config_version="0123456789abcdef")


def _break_format_version(harness: _Harness) -> None:
    harness.rewrite_manifest(format_version=99)


def _break_format_version_declaration(harness: _Harness) -> None:
    # Not an integer, so no writer can be identified from it.
    harness.rewrite_manifest(format_version="3")


def _drop_format_version(harness: _Harness) -> None:
    manifest_path = harness.run_dir / PLAN_DIR_NAME / MANIFEST_FILE_NAME
    mapping = json.loads(manifest_path.read_bytes())
    del mapping["format_version"]
    operations = (harness.run_dir / PLAN_DIR_NAME / OPERATIONS_FILE_NAME).read_bytes()
    mapping["plan_checksum"] = compute_plan_checksum(mapping, operations)
    manifest_path.write_bytes(canonical_json_bytes(mapping))


def _tear_operations(harness: _Harness) -> None:
    # One more operation line than the manifest records; the manifest itself is untouched.
    operations_path = harness.run_dir / PLAN_DIR_NAME / OPERATIONS_FILE_NAME
    operations_path.write_bytes(operations_path.read_bytes() * 2)


def _break_last_changes(harness: _Harness) -> None:
    # The manifest records a `last_changes.jsonl` that the plan directory does not hold.
    harness.rewrite_manifest(
        last_changes={"path": "last_changes.jsonl", "sha256": "0" * 64, "count": 1, "metadata_read": True}
    )


def _break_destination_schema(harness: _Harness) -> None:
    harness.live_fingerprint = "e" * 64


def _break_destination_binding(harness: _Harness) -> None:
    harness.destination.live_url = "https://another-destination.invalid"


def _break_write_surface(harness: _Harness) -> None:
    harness.destination = _Surfaceless


def _break_planned_fields(harness: _Harness) -> None:
    harness.destination.missing_field = True


def _break_lineage(harness: _Harness) -> None:
    harness.destination.refuse_lineage = True


# Each case: the check both stages must name, and how the fixture is broken for it.
_BREAKS: dict[str, tuple[str, Callable[[_Harness], None]]] = {
    "run_binding": ("run_binding", _break_run_binding),
    "configuration_binding": ("run_binding", _break_configuration_binding),
    "plan_checksum": ("plan_checksum", _break_plan_checksum),
    "source_snapshot": ("source_snapshot", _break_source_snapshot),
    "config_version": ("config_version", _break_config_version),
    "format_version": ("format_version", _break_format_version),
    # A damaged plan file: the reader would refuse these too, without naming a check.
    "malformed_format_version": ("format_version", _break_format_version_declaration),
    "missing_format_version": ("format_version", _drop_format_version),
    "torn_operations": ("torn_operations", _tear_operations),
    "last_changes": ("last_changes", _break_last_changes),
    "destination_schema": ("destination_schema", _break_destination_schema),
    "destination_binding": ("destination_binding", _break_destination_binding),
    "write_surface": ("write_surface", _break_write_surface),
    "planned_fields": ("planned_fields", _break_planned_fields),
    "lineage": ("lineage", _break_lineage),
}


@pytest.mark.parametrize("case", list(_BREAKS))
def test_verify_and_apply_fail_on_the_same_named_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    # Approved before the break, as an operator approves the plan they reviewed.
    approved = harness.recorded_checksum
    check, breaks = _BREAKS[case]
    breaks(harness)
    if check != "plan_checksum":
        # A re-recorded manifest is the plan the operator would now be applying.
        approved = harness.recorded_checksum

    with pytest.raises(Exception) as verified:
        _execute(harness, "verify", approved=approved)
    with pytest.raises(Exception) as applied:
        _execute(harness, "apply", approved=approved)

    assert failed_checks(verified.value) == (check,), repr(verified.value)
    assert failed_checks(applied.value) == (check,), repr(applied.value)
    assert harness.writes == []


def test_every_check_passes_on_an_unchanged_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control for the parity cases: the same fixture, unbroken, verifies and then applies."""
    harness = _harness(tmp_path, monkeypatch)

    verified = _execute(harness, "verify", approved=harness.recorded_checksum)
    assert harness.writes == []
    _execute(harness, "apply", approved=harness.recorded_checksum)

    assert verified["checks"] == ALL_CHECKS
    assert verified["destination_checked"] is True
    assert len(harness.writes) == 1


# ======================================================================================
# The verify stage, through the worker flow
# ======================================================================================


def _stored(harness: _Harness) -> ProductRun:
    stored = harness.projection.lookup_run(RUN_ID).value
    assert stored is not None
    return stored


def _verify(harness: _Harness) -> Any:  # noqa: ANN401 - a result dict, or a Failed state
    publish_plan_directory(harness.projection, RUN_ID, harness.run_dir)
    return service_sync_run.fn(RUN_ID, "verify", *harness.binding)


def test_a_passing_verify_merges_its_checks_and_leaves_the_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)

    _verify(harness)

    stored = _stored(harness)
    assert stored.results["verification"]["checks"] == ALL_CHECKS
    assert stored.results["verification"]["destination_checked"] is True
    assert stored.phase == "planned"
    assert harness.writes == []


def test_a_failing_verify_merges_its_failure_and_leaves_the_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    _break_destination_binding(harness)

    with pytest.raises(RuntimeError):
        _verify(harness)

    stored = _stored(harness)
    assert stored.results["verify_failure"]["failed_checks"] == ["destination_binding"]
    assert "verification" not in stored.results
    assert stored.phase == "planned"
    assert harness.writes == []


def test_a_verify_of_a_torn_plan_records_the_failed_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness(tmp_path, monkeypatch)
    _tear_operations(harness)

    with pytest.raises(RuntimeError):
        _verify(harness)

    assert _stored(harness).results["verify_failure"]["failed_checks"] == ["torn_operations"]


@pytest.mark.parametrize("case", ["torn_operations", "destination_binding", "lineage"])
def test_a_refused_apply_records_the_failed_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    harness = _harness(tmp_path, monkeypatch, purpose="apply")
    approved = harness.recorded_checksum
    check, breaks = _BREAKS[case]
    breaks(harness)
    publish_plan_directory(harness.projection, RUN_ID, harness.run_dir)

    # A refusal with a recorded next action ends as a Failed state; any other one raises.
    with suppress(RuntimeError):
        service_sync_run.fn(RUN_ID, "apply", *harness.binding, expected_checksum=approved, confirm_writes=True)

    stored = _stored(harness)
    assert stored.phase == "apply-failed"
    assert stored.results["apply_failure"]["failed_checks"] == [check]
    assert harness.writes == []


def test_an_unreachable_destination_fails_and_reports_no_destination_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = _harness(tmp_path, monkeypatch)
    harness.schema_unavailable = True

    # A recognized failure ends the flow run Failed with its cause, rather than raising (spec 017).
    state = _verify(harness)

    assert isinstance(state, State)
    assert state.is_failed()
    stored = _stored(harness)
    recorded = stored.results["verify_failure"]
    assert recorded["error_type"] == "DestinationSchemaUnavailableError"
    assert "verification" not in stored.results, "no check may be reported as passed"
    assert stored.phase == "planned"
    for canary in (NETBOX_CANARY, INFRAHUB_CANARY):
        assert canary not in json.dumps(stored.results)
        assert canary not in str(state.message)


def test_verify_takes_no_configuration_write_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness(tmp_path, monkeypatch)

    @contextmanager
    def guard_forbidden(_configuration_id: str) -> Iterator[None]:
        pytest.fail("verify took the configuration write guard")
        yield

    monkeypatch.setattr(service_flow, "_configuration_write_guard", guard_forbidden)

    _verify(harness)

    assert _stored(harness).results["verification"]["destination_checked"] is True


def test_verify_resolves_no_source_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The host that verifies holds the destination credential and no source credential at all."""
    harness = _harness(tmp_path, monkeypatch)
    monkeypatch.delenv("INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN")
    requested: list[bool] = []
    real = service_flow.resolve_runtime_instance

    def resolve(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        requested.append(kwargs["resolve_source_credentials"])
        return real(*args, **kwargs)

    monkeypatch.setattr(service_flow, "resolve_runtime_instance", resolve)

    _verify(harness)

    assert requested
    assert not any(requested)
    assert _stored(harness).results["verification"]["destination_checked"] is True


def test_a_legacy_run_reports_that_the_destination_was_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "legacy-verify"
    projection = local_product_projection(tmp_path / "product")
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="verify",
            configuration_reference="legacy-config-version",
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="planned",
            summary={"sync_name": "legacy-inventory"},
        )
    )
    append_execution(
        projection,
        run_id,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose="verify", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )
    saved = SavedPlan(
        manifest=PlanManifest(
            format_version=2,
            run_id=run_id,
            created_at="2026-08-10T12:00:00+00:00",
            config_version="legacy-config-version",
            source_snapshot=[],
            operations_count=0,
            delete_operations_computed=True,
            plan_checksum="a" * 64,
        ),
        operations=[],
        checksum_ok=True,
        verification_notes=[],
    )
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(
        service_flow,
        "resolve_sync_instance",
        lambda _name, *, directory: SimpleNamespace(name="legacy-inventory", directory=directory),
    )
    monkeypatch.setattr(service_flow, "resolve_config_version", lambda _instance: "legacy-config-version")
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(service_flow, "rehydrate_plan_checkpoint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service_flow, "execute_run", lambda *_args, **_kwargs: saved)
    monkeypatch.setattr(
        service_flow, "PlanApplier", SimpleNamespace(open_existing=lambda *_a, **_k: pytest.fail("legacy checked"))
    )

    service_sync_run.fn(run_id, "verify")

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.results["verification"]["destination_checked"] is False
    assert "destination_schema" not in stored.results["verification"]["checks"]
