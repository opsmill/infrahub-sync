"""Behavioral contract tests for the version 1 local Python API."""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest
from filelock import Timeout
from pydantic import ValidationError

import infrahub_sync.api.v1 as api
import infrahub_sync.api.v1._operations as operations  # noqa: PLC2701 - tests exercise internal composition seams
from infrahub_sync import SyncInstance
from infrahub_sync.cache.paths import run_dir
from infrahub_sync.cache.sidecars import RunFile
from infrahub_sync.execution import RunResult as CoreRunResult
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.errors import OperationApplyFailedError, PlanVerificationError
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.plan.review import SavedPlan, read_saved_plan
from infrahub_sync.potenda import Potenda
from infrahub_sync.utils import PlanApplier
from tests.plan.artifact_fixtures import operation_record, tamper_with_operations, tamperable_operation, write_artifact

if TYPE_CHECKING:
    from infrahub_sync.plan.models import PlannedOperation
    from infrahub_sync.plan.reader import RawPlanArtifact

SYNC_NAME = "api-example"
RUN_ID = "20260808T1200-a1b2c3d4"
TOKEN = "PUBLIC-API-SECRET-CANARY-12345"  # noqa: S105 - a redaction canary
SECOND_TOKEN = "SECOND-PUBLIC-API-SECRET-67890"  # noqa: S105 - a redaction canary

EXPECTED_ALL = [
    "ActionCounts",
    "ApplyRequest",
    "ArtifactReference",
    "LifecycleEvent",
    "PlanRequest",
    "RunError",
    "RunExecutionError",
    "RunResult",
    "RunValidationError",
    "SyncRequest",
    "VerifyRequest",
    "apply",
    "plan",
    "sync",
    "verify",
]


def _config_text(*, token: str = TOKEN) -> str:
    return f"""
name: {SYNC_NAME}
source:
  name: mockdb
  settings:
    url: http://localhost:9999
    token: {token}
destination:
  name: infrahub
  settings:
    url: http://localhost:8000
schema_mapping:
  - name: BuiltinTag
    mapping: tag
    identifiers: [name]
    fields:
      - name: name
        mapping: name
"""


@pytest.fixture
def config_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "configs" / SYNC_NAME
    directory.mkdir(parents=True)
    (directory / "config.yml").write_text(_config_text(), encoding="utf-8")
    return directory.parent


@pytest.fixture
def instance(config_directory: Path) -> SyncInstance:
    return operations.resolve_sync_instance(SYNC_NAME, directory=str(config_directory))


@pytest.fixture
def cache_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "cache"
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(directory))
    return directory


def _saved_run(instance: SyncInstance) -> tuple[Path, str]:
    directory = run_dir(instance.name, RUN_ID)
    manifest = write_artifact(
        directory,
        [operation_record()],
        run_id=RUN_ID,
        config_version=resolve_config_version(instance),
    )
    return directory, cast("str", manifest["plan_checksum"])


def test_public_surface_is_explicit_and_versioned() -> None:
    assert sorted(api.__all__) == sorted(EXPECTED_ALL)
    assert api.PlanRequest(sync_name=SYNC_NAME, config_directory="configs").model_dump() == {
        "sync_name": SYNC_NAME,
        "config_directory": "configs",
        "branch": None,
    }


def test_requests_are_strict_and_immutable() -> None:
    request = api.VerifyRequest(sync_name=SYNC_NAME, config_directory="configs", run_id=RUN_ID)

    with pytest.raises(ValidationError):
        api.VerifyRequest.model_validate(
            {"sync_name": SYNC_NAME, "config_directory": "configs", "run_id": RUN_ID, "branch": "main"}
        )
    with pytest.raises(ValidationError):
        request.run_id = "different"


def test_result_and_lifecycle_readers_preserve_future_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", TOKEN)
    result = api.RunResult.model_validate(
        {
            "run_id": RUN_ID,
            "operation": "verify",
            "phase": "future-phase",
            "outcome": "future-outcome",
            "counts": {},
            "domain_summary": {"BuiltinTag": 0},
            "artifacts": [{"kind": "future-artifact", "path": f"/var/data/{TOKEN}/plan"}],
            "future_field": "preserved",
        }
    )
    event = api.LifecycleEvent.model_validate(
        {
            "run_id": RUN_ID,
            "operation": "verify",
            "stage": "future-stage",
            "outcome": "future-outcome",
            "future_field": "preserved",
        }
    )

    assert (result.phase, result.outcome) == ("future-phase", "future-outcome")
    assert result.model_dump()["future_field"] == "preserved"
    assert TOKEN not in result.model_dump_json()
    assert result.model_dump()["artifacts"][0]["path"] == "/var/data/***/plan"
    assert (event.stage, event.outcome, event.model_extra) == (
        "future-stage",
        "future-outcome",
        {"future_field": "preserved"},
    )


def test_result_redacts_direct_fields_mapping_keys_extras_and_late_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_TOKEN", TOKEN)
    result = api.RunResult.model_validate(
        {
            "run_id": f"run-{TOKEN}",
            "operation": "verify",
            "phase": "future-phase",
            "outcome": "future-outcome",
            "counts": {},
            "domain_summary": {f"kind-{TOKEN}": 1},
            "artifacts": [{"kind": "plan", "path": f"/var/{TOKEN}/plan"}],
            "future_field": {f"key-{TOKEN}": [f"value-{TOKEN}"]},
        }
    )

    assert result.run_id == "run-***"
    assert result.domain_summary == {"kind-***": 1}
    assert result.artifacts[0].path == "/var/***/plan"
    assert result.model_extra == {"future_field": {"key-***": ["value-***"]}}
    assert TOKEN not in str(result.__dict__)

    late_secret = "LATE-PUBLIC-API-SECRET-67890"  # noqa: S105 - a late redaction canary
    result.domain_summary[late_secret] = 2
    assert result.model_extra is not None
    result.model_extra["late"] = {late_secret: [late_secret]}
    monkeypatch.setenv("LATE_API_TOKEN", late_secret)

    assert late_secret not in str(result.model_dump())
    assert late_secret not in result.model_dump_json()
    assert TOKEN not in str(result.model_dump())
    assert TOKEN not in result.model_dump_json()


def test_result_redacts_sets_and_preserves_colliding_mapping_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", TOKEN)
    monkeypatch.setenv("SECOND_API_TOKEN", SECOND_TOKEN)
    result = api.RunResult.model_validate(
        {
            "run_id": RUN_ID,
            "operation": "verify",
            "phase": "completed",
            "outcome": "verified",
            "counts": {},
            "domain_summary": {},
            "artifacts": [],
            "future_set": {TOKEN, SECOND_TOKEN},
            "future_frozenset": frozenset({TOKEN, SECOND_TOKEN}),
            "future_mapping": {TOKEN: "first", SECOND_TOKEN: "second"},
        }
    )

    dumped = result.model_dump()
    assert TOKEN not in str(dumped)
    assert SECOND_TOKEN not in str(dumped)
    assert dumped["future_set"] == {"***"}
    assert dumped["future_frozenset"] == frozenset({"***"})
    assert len(dumped["future_mapping"]) == 2
    assert set(dumped["future_mapping"].values()) == {"first", "second"}


def test_boundary_secret_merging_replaces_longest_values_first(monkeypatch: pytest.MonkeyPatch) -> None:
    short_secret = "OVERLAP-SECRET"  # noqa: S105 - a redaction canary
    long_secret = f"{short_secret}-LONGER"
    monkeypatch.setenv("LATE_API_TOKEN", long_secret)

    error = api.RunExecutionError(
        f"credential={long_secret}",
        operation="sync",
        stage="apply",
        run_id=RUN_ID,
        secrets=(short_secret,),
    )

    assert str(error) == "credential=***"


def test_boundary_secrets_are_removed_from_returned_result_attributes(
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
) -> None:
    directory, _checksum = _saved_run(instance)
    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=instance)
    saved.manifest.run_id = f"run-{TOKEN}"
    saved._operations[0].kind = f"kind-{TOKEN}"

    result = operations._result(
        saved=saved,
        operation="plan",
        outcome="planned",
        run_directory=directory / TOKEN,
        secrets=(TOKEN,),
    )

    assert result.run_id == "run-***"
    assert result.domain_summary == {"kind-***": 1}
    assert all(TOKEN not in artifact.path for artifact in result.artifacts)
    assert TOKEN not in str(result.__dict__)
    assert TOKEN not in result.model_dump_json()


def test_plan_uses_shared_core_and_returns_saved_plan_summary(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_execute(sync_instance: SyncInstance, **kwargs: Any) -> CoreRunResult:  # noqa: ANN401
        calls.append(kwargs)
        requested_run_id = cast("str", kwargs["run_id"])
        directory = run_dir(sync_instance.name, requested_run_id)
        write_artifact(
            directory,
            [
                operation_record(kind="BuiltinTag"),
                operation_record(action="delete", kind="BuiltinLocation", identity={"name": "old"}),
            ],
            run_id=requested_run_id,
            config_version=resolve_config_version(sync_instance),
        )
        return CoreRunResult(
            sync_name=sync_instance.name,
            operation="plan",
            run_id=requested_run_id,
            status="planned",
            changed=True,
            summary={"create": 1, "update": 0, "delete": 1},
            artifact_path=str(directory),
        )

    monkeypatch.setattr(operations, "execute_run", fake_execute)
    monkeypatch.setattr(operations, "generate_run_id", lambda: RUN_ID)
    caplog.set_level(logging.INFO, logger=operations.__name__)

    result = api.plan(api.PlanRequest(sync_name=SYNC_NAME, config_directory=str(config_directory)))

    assert calls == [
        {
            "operation": "plan",
            "branch": None,
            "run_id": RUN_ID,
            "show_progress": False,
            "print_diff": False,
            "_lock_already_held": False,
            "_run_file_mode": None,
        }
    ]
    assert result.operation == "plan"
    assert result.outcome == "planned"
    assert result.counts == api.ActionCounts(create=1, delete=1)
    assert result.domain_summary == {"BuiltinLocation": 1, "BuiltinTag": 1}
    assert all(Path(reference.path).is_absolute() for reference in result.artifacts)
    assert read_saved_plan(sync_name=instance.name, run_id=RUN_ID).manifest.run_id == RUN_ID
    events = [
        api.LifecycleEvent.model_validate(record.__dict__) for record in caplog.records if hasattr(record, "run_id")
    ]
    assert [(event.operation, event.stage, event.outcome) for event in events] == [
        ("plan", "plan", "running"),
        ("plan", "plan", "planned"),
    ]


def test_verify_is_independent_read_only_and_emits_structured_fields(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _checksum = _saved_run(instance)

    def adapters_are_forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "independent verification constructed an adapter"
        raise AssertionError(msg)

    monkeypatch.setattr(operations.PlanApplier, "open_existing", adapters_are_forbidden)
    caplog.set_level(logging.INFO, logger=operations.__name__)

    result = api.verify(api.VerifyRequest(sync_name=SYNC_NAME, config_directory=str(config_directory), run_id=RUN_ID))

    assert result.outcome == "verified"
    assert result.artifacts[0].path == str(directory)
    events = [
        api.LifecycleEvent.model_validate(record.__dict__) for record in caplog.records if hasattr(record, "run_id")
    ]
    assert [(event.operation, event.stage, event.outcome) for event in events] == [
        ("verify", "verify", "running"),
        ("verify", "verify", "verified"),
    ]


def test_stale_plan_verification_raises_a_typed_product_refusal(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
) -> None:
    directory = run_dir(instance.name, RUN_ID)
    write_artifact(
        directory,
        [tamperable_operation()],
        run_id=RUN_ID,
        config_version=resolve_config_version(instance),
    )
    tamper_with_operations(directory)

    with pytest.raises(api.RunValidationError) as caught:
        api.verify(api.VerifyRequest(sync_name=SYNC_NAME, config_directory=str(config_directory), run_id=RUN_ID))

    assert caught.value.operation == "verify"
    assert caught.value.stage == "verify"
    assert "plan_checksum" in str(caught.value)


class _RecordingDestination:
    """Minimal planned-write destination that records verification/write order."""

    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    def new_peer_resolver(self) -> object:  # noqa: PLR6301 - protocol method
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: object) -> str:  # noqa: ARG002
        self.timeline.append(f"write:{operation.operation_id}")
        return "destination-node"


class _ApplyOnlySource:
    """Mutable placeholder for the source that reviewed-plan apply never reads."""


def test_apply_reverifies_immediately_before_the_first_destination_write(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, checksum = _saved_run(instance)
    timeline: list[str] = []
    destination = _RecordingDestination(timeline)
    engine = Potenda(
        source=cast("Any", _ApplyOnlySource()),
        destination=cast("Any", destination),
        config=instance,
        top_level=[],
        run_dir=directory,
        run_id=RUN_ID,
        cache_root=directory.parent,
    )
    applier = PlanApplier(engine, run_dir=directory, run_id=RUN_ID)
    real_verify = operations.verify_plan

    def recording_verify(**kwargs: Any) -> Any:  # noqa: ANN401
        timeline.append("verify")
        return real_verify(**kwargs)

    monkeypatch.setattr("infrahub_sync.plan.verify.verify_plan", recording_verify)
    monkeypatch.setattr(operations.PlanApplier, "open_existing", lambda *_args, **_kwargs: applier)
    caplog.set_level(logging.INFO, logger=operations.__name__)

    result = api.apply(
        api.ApplyRequest(
            sync_name=SYNC_NAME,
            config_directory=str(config_directory),
            run_id=RUN_ID,
            expected_checksum=checksum,
        )
    )

    assert timeline[0] == "verify"
    assert timeline[1].startswith("write:")
    assert result.operation == "apply"
    assert result.outcome == "applied"
    events = [
        api.LifecycleEvent.model_validate(record.__dict__) for record in caplog.records if hasattr(record, "run_id")
    ]
    assert [(event.operation, event.stage, event.outcome) for event in events] == [
        ("apply", "apply", "running"),
        ("apply", "apply", "applied"),
    ]


def test_apply_refuses_an_unreviewed_checksum_before_destination_construction(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _checksum = _saved_run(instance)
    RunFile(
        path=directory / "run.json",
        status="dry-run",
        mode="diff",
        summary={"resources": 1},
        finished_at="2026-08-08T12:00:00+00:00",
    ).save()
    before = _read_run_sidecar(directory)

    def adapters_are_forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "checksum mismatch constructed the destination"
        raise AssertionError(msg)

    monkeypatch.setattr(operations.PlanApplier, "open_existing", adapters_are_forbidden)

    with pytest.raises(api.RunValidationError) as caught:
        api.apply(
            api.ApplyRequest(
                sync_name=SYNC_NAME,
                config_directory=str(config_directory),
                run_id=RUN_ID,
                expected_checksum="0" * 64,
            )
        )

    assert caught.value.stage == "apply"
    assert "is not the plan this apply approved" in str(caught.value)
    assert _read_run_sidecar(directory) == before


def test_apply_destination_construction_refusal_preserves_a_healthy_sidecar(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, checksum = _saved_run(instance)
    RunFile(
        path=directory / "run.json",
        status="dry-run",
        mode="diff",
        summary={"resources": 1},
        finished_at="2026-08-08T12:00:00+00:00",
    ).save()
    before = _read_run_sidecar(directory)

    def refuse_destination(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "destination credentials are missing"
        raise ValueError(msg)

    monkeypatch.setattr(operations.PlanApplier, "open_existing", refuse_destination)

    with pytest.raises(api.RunExecutionError):
        api.apply(
            api.ApplyRequest(
                sync_name=SYNC_NAME,
                config_directory=str(config_directory),
                run_id=RUN_ID,
                expected_checksum=checksum,
            )
        )

    assert _read_run_sidecar(directory) == before


def test_apply_result_counts_come_from_the_exact_artifact_passed_to_apply(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, checksum = _saved_run(instance)

    class ReplacingApplier:
        @staticmethod
        def apply_plan(**kwargs: object) -> ApplyRecord:
            approved_artifact = cast("RawPlanArtifact", kwargs["artifact"])
            assert approved_artifact.operations_bytes is not None
            write_artifact(
                directory,
                [
                    operation_record(kind="BuiltinLocation", identity={"name": "one"}),
                    operation_record(kind="BuiltinLocation", identity={"name": "two"}),
                ],
                run_id=RUN_ID,
                config_version=resolve_config_version(instance),
            )
            return ApplyRecord(applied_operations=("approved-operation",))

    monkeypatch.setattr(operations.PlanApplier, "open_existing", lambda *_args, **_kwargs: ReplacingApplier())

    result = api.apply(
        api.ApplyRequest(
            sync_name=SYNC_NAME,
            config_directory=str(config_directory),
            run_id=RUN_ID,
            expected_checksum=checksum,
        )
    )

    assert result.counts == api.ActionCounts(create=1)
    assert result.domain_summary == {"BuiltinTag": 1}


def test_sync_requires_confirmation_before_loading_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        operations,
        "_load_instance",
        lambda *_args: pytest.fail("configuration was loaded before confirmation"),
    )

    with pytest.raises(api.RunValidationError) as caught:
        api.sync(api.SyncRequest(sync_name=SYNC_NAME, config_directory="missing"))

    assert caught.value.model_dump() == {
        "api_version": "1",
        "run_id": None,
        "operation": "sync",
        "stage": "confirmation",
        "outcome": "failed",
        "message": "confirm_writes=true is required to run operation=sync",
    }


def test_confirmed_sync_composes_plan_verify_apply_in_order(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _checksum = _saved_run(instance)
    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=instance)
    timeline: list[str] = []
    lock_entries = 0
    lock_held = False

    @contextmanager
    def recording_lock(_sync_name: str) -> Iterator[None]:
        nonlocal lock_entries, lock_held
        lock_entries += 1
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    def fake_plan(*_args: object, **_kwargs: object) -> tuple[Any, Path]:
        assert lock_held is True
        assert _kwargs["lock_already_held"] is True
        timeline.append("plan")
        operations._log_lifecycle(run_id=RUN_ID, operation="sync", stage="plan", outcome="planned")
        return saved, directory

    def fake_verify(*_args: object, **_kwargs: object) -> tuple[Any, Path]:
        assert lock_held is True
        timeline.append("verify")
        operations._log_lifecycle(run_id=RUN_ID, operation="sync", stage="verify", outcome="verified")
        return saved, directory

    def fake_apply(*_args: object, **_kwargs: object) -> tuple[ApplyRecord, SavedPlan]:
        assert lock_held is True
        assert _kwargs["lock_already_held"] is True
        timeline.append("apply")
        operations._log_lifecycle(run_id=RUN_ID, operation="sync", stage="apply", outcome="applied")
        return ApplyRecord(applied_operations=(saved.operations()[0].operation_id,)), saved

    monkeypatch.setattr(operations, "generate_run_id", lambda: RUN_ID)
    monkeypatch.setattr(operations, "_plan_instance", fake_plan)
    monkeypatch.setattr(operations, "_verify_instance", fake_verify)
    monkeypatch.setattr(operations, "_apply_instance", fake_apply)
    monkeypatch.setattr(operations, "pipeline_lock", recording_lock)
    caplog.set_level(logging.INFO, logger=operations.__name__)

    result = api.sync(
        api.SyncRequest(
            sync_name=SYNC_NAME,
            config_directory=str(config_directory),
            confirm_writes=True,
        )
    )

    assert timeline == ["plan", "verify", "apply"]
    assert lock_entries == 1
    assert lock_held is False
    assert result.operation == "sync"
    assert result.outcome == "applied"
    events = [
        api.LifecycleEvent.model_validate(record.__dict__) for record in caplog.records if hasattr(record, "run_id")
    ]
    assert [(event.operation, event.stage, event.outcome) for event in events] == [
        ("sync", "plan", "planned"),
        ("sync", "verify", "verified"),
        ("sync", "apply", "applied"),
        ("sync", "completed", "applied"),
    ]


def test_confirmed_sync_lock_timeout_creates_no_running_sidecar(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operations, "generate_run_id", lambda: RUN_ID)

    def locked_out(_sync_name: str) -> NoReturn:
        lock_path = "pipeline.lock"
        raise Timeout(lock_path)

    monkeypatch.setattr(operations, "pipeline_lock", locked_out)

    with pytest.raises(api.RunExecutionError) as caught:
        api.sync(
            api.SyncRequest(
                sync_name=SYNC_NAME,
                config_directory=str(config_directory),
                confirm_writes=True,
            )
        )

    assert caught.value.stage == "plan"
    assert not (run_dir(instance.name, RUN_ID) / "run.json").exists()


def _replace_sync_plan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    saved: SavedPlan,
    directory: Path,
) -> None:
    """Make a composed sync begin with one completed plan sidecar."""
    monkeypatch.setattr(operations, "generate_run_id", lambda: RUN_ID)

    def fake_plan(*_args: object, **_kwargs: object) -> tuple[Any, Path]:
        RunFile(
            path=directory / "run.json",
            status="dry-run",
            mode="diff",
            summary={"resources": 1},
            finished_at="2026-08-08T12:00:00+00:00",
        ).save()
        return saved, directory

    monkeypatch.setattr(operations, "_plan_instance", fake_plan)


def _read_run_sidecar(directory: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((directory / "run.json").read_text(encoding="utf-8")))


def test_sync_verification_refusal_persists_one_failed_sync_sidecar(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _checksum = _saved_run(instance)
    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=instance)
    _replace_sync_plan(monkeypatch, saved=saved, directory=directory)

    def refuse_verify(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "reviewed plan is stale"
        raise PlanVerificationError(msg)

    monkeypatch.setattr(operations, "_verify_instance", refuse_verify)

    with pytest.raises(api.RunValidationError) as caught:
        api.sync(
            api.SyncRequest(
                sync_name=SYNC_NAME,
                config_directory=str(config_directory),
                confirm_writes=True,
            )
        )

    recorded = _read_run_sidecar(directory)
    assert (caught.value.operation, caught.value.stage) == ("sync", "verify")
    assert recorded["mode"] == "sync"
    assert recorded["status"] == "failed"
    assert recorded["summary"] == {"resources": 1}
    assert recorded["finished_at"] != "2026-08-08T12:00:00+00:00"


def test_sync_partial_apply_failure_persists_record_and_failed_state(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _checksum = _saved_run(instance)
    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=instance)
    operation_id = saved.operations()[0].operation_id
    _replace_sync_plan(monkeypatch, saved=saved, directory=directory)
    monkeypatch.setattr(operations, "_verify_instance", lambda *_args, **_kwargs: (saved, directory))

    class RejectingApplier:
        @staticmethod
        def apply_plan(**_kwargs: object) -> NoReturn:
            partial = ApplyRecord(applied_operations=("already-applied",), failed_operation=operation_id)
            msg = "destination refused the operation"
            raise OperationApplyFailedError(msg, apply_record=partial)

    monkeypatch.setattr(operations.PlanApplier, "open_existing", lambda *_args, **_kwargs: RejectingApplier())

    with pytest.raises(api.RunExecutionError):
        api.sync(
            api.SyncRequest(
                sync_name=SYNC_NAME,
                config_directory=str(config_directory),
                confirm_writes=True,
            )
        )

    recorded = _read_run_sidecar(directory)
    assert (recorded["mode"], recorded["status"]) == ("sync", "failed")
    assert recorded["summary"]["resources"] == 1
    assert recorded["summary"]["applied_operations"] == ["already-applied"]
    assert recorded["summary"]["failed_operation"] == operation_id
    assert recorded["summary"]["may_have_partially_written"] is True
    assert recorded["finished_at"] is not None


def test_sync_apply_interrupt_persists_carried_partial_record(
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, checksum = _saved_run(instance)
    RunFile(path=directory / "run.json", status="running", mode="sync").save()
    interruption = KeyboardInterrupt("operator interrupted apply")
    interruption.apply_record = ApplyRecord(  # ty: ignore[unresolved-attribute]
        applied_operations=("already-applied",),
        failed_operation="interrupted-operation",
    )

    class InterruptingApplier:
        @staticmethod
        def apply_plan(**_kwargs: object) -> NoReturn:
            raise interruption

    monkeypatch.setattr(operations.PlanApplier, "open_existing", lambda *_args, **_kwargs: InterruptingApplier())

    with pytest.raises(KeyboardInterrupt):
        operations._apply_instance(
            instance,
            run_directory=directory,
            run_id=RUN_ID,
            branch=None,
            expected_checksum=checksum,
            operation="sync",
            secrets=(),
        )

    recorded = _read_run_sidecar(directory)
    assert (recorded["mode"], recorded["status"]) == ("sync", "failed")
    assert recorded["summary"]["applied_operations"] == ["already-applied"]
    assert recorded["summary"]["failed_operation"] == "interrupted-operation"
    assert recorded["finished_at"] is not None


def test_sync_success_persists_applied_state_and_completion_time(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _checksum = _saved_run(instance)
    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=instance)
    operation_id = saved.operations()[0].operation_id
    _replace_sync_plan(monkeypatch, saved=saved, directory=directory)
    monkeypatch.setattr(operations, "_verify_instance", lambda *_args, **_kwargs: (saved, directory))

    class SuccessfulApplier:
        @staticmethod
        def apply_plan(**_kwargs: object) -> ApplyRecord:
            return ApplyRecord(applied_operations=(operation_id,))

    monkeypatch.setattr(operations.PlanApplier, "open_existing", lambda *_args, **_kwargs: SuccessfulApplier())

    result = api.sync(
        api.SyncRequest(
            sync_name=SYNC_NAME,
            config_directory=str(config_directory),
            confirm_writes=True,
        )
    )

    recorded = _read_run_sidecar(directory)
    assert result.outcome == "applied"
    assert (recorded["mode"], recorded["status"]) == ("sync", "applied")
    assert recorded["summary"]["resources"] == 1
    assert recorded["summary"]["applied_operations"] == [operation_id]
    assert recorded["finished_at"] is not None


def test_sync_terminal_sidecar_failure_is_typed_and_recovers_failed_state(
    config_directory: Path,
    instance: SyncInstance,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, _checksum = _saved_run(instance)
    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, config=instance)
    _replace_sync_plan(monkeypatch, saved=saved, directory=directory)
    monkeypatch.setattr(operations, "_verify_instance", lambda *_args, **_kwargs: (saved, directory))

    class SuccessfulApplier:
        @staticmethod
        def apply_plan(**_kwargs: object) -> ApplyRecord:
            return ApplyRecord(applied_operations=(saved.operations()[0].operation_id,))

    monkeypatch.setattr(operations.PlanApplier, "open_existing", lambda *_args, **_kwargs: SuccessfulApplier())
    real_transition = operations._transition_run_sidecar
    failed_once = False

    def flaky_transition(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        nonlocal failed_once
        if kwargs["status"] == "applied" and not failed_once:
            failed_once = True
            msg = f"could not save terminal state with {TOKEN}"
            raise OSError(msg)
        real_transition(*args, **kwargs)

    monkeypatch.setattr(operations, "_transition_run_sidecar", flaky_transition)

    with pytest.raises(api.RunExecutionError) as caught:
        api.sync(
            api.SyncRequest(
                sync_name=SYNC_NAME,
                config_directory=str(config_directory),
                confirm_writes=True,
            )
        )

    assert failed_once is True
    assert TOKEN not in str(caught.value)
    assert _read_run_sidecar(directory)["status"] == "failed"


def test_public_error_redacts_message_serialization_and_full_traceback(
    config_directory: Path,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_with_secret(*_args: object, **_kwargs: object) -> NoReturn:
        inner_message = f"inner adapter rejected credential {TOKEN}"
        outer_message = f"outer adapter rejected credential {TOKEN}"
        inner = ValueError(inner_message)
        outer = RuntimeError(outer_message)
        outer.__cause__ = inner
        outer.__context__ = inner
        raise outer

    monkeypatch.setattr(operations, "execute_run", fail_with_secret)
    monkeypatch.setattr(operations, "generate_run_id", lambda: RUN_ID)

    with pytest.raises(api.RunExecutionError) as caught:
        api.plan(api.PlanRequest(sync_name=SYNC_NAME, config_directory=str(config_directory)))

    rendered = "".join(traceback.format_exception(caught.value))
    assert TOKEN not in str(caught.value)
    assert TOKEN not in str(caught.value.__dict__)
    assert TOKEN not in str(caught.value.model_dump())
    assert TOKEN not in rendered
    assert "***" in rendered

    seen: set[int] = set()
    pending: list[BaseException] = [caught.value]
    while pending:
        error = pending.pop()
        if id(error) in seen:
            continue
        seen.add(id(error))
        assert TOKEN not in repr(error)
        pending.extend(link for link in (error.__cause__, error.__context__) if link is not None)
    assert caught.value.__context__ is None


def test_public_error_and_failure_log_redact_a_credential_bearing_run_id(
    config_directory: Path,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    caplog: pytest.LogCaptureFixture,
) -> None:
    unsafe_run_id = f"../{TOKEN}"
    caplog.set_level(logging.INFO, logger=operations.__name__)

    with pytest.raises(api.RunValidationError) as caught:
        api.verify(
            api.VerifyRequest(
                sync_name=SYNC_NAME,
                config_directory=str(config_directory),
                run_id=unsafe_run_id,
            )
        )

    rendered_log = "\n".join(record.getMessage() for record in caplog.records)
    assert caught.value.run_id == "../***"
    assert TOKEN not in str(caught.value.model_dump())
    assert TOKEN not in rendered_log
    assert all(TOKEN not in str(record.__dict__) for record in caplog.records)
