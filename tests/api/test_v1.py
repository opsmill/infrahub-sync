"""Behavioral contract tests for the version 1 local Python API."""

from __future__ import annotations

import logging
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

import pytest
from pydantic import ValidationError

import infrahub_sync.api.v1 as api
import infrahub_sync.api.v1._operations as operations  # noqa: PLC2701 - tests exercise internal composition seams
from infrahub_sync import SyncInstance
from infrahub_sync.cache.paths import run_dir
from infrahub_sync.execution import RunResult as CoreRunResult
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.potenda import Potenda
from infrahub_sync.utils import PlanApplier
from tests.plan.artifact_fixtures import operation_record, tamper_with_operations, tamperable_operation, write_artifact

if TYPE_CHECKING:
    from infrahub_sync.plan.models import PlannedOperation

SYNC_NAME = "api-example"
RUN_ID = "20260808T1200-a1b2c3d4"
TOKEN = "PUBLIC-API-SECRET-CANARY-12345"  # noqa: S105 - a redaction canary

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
    _directory, _checksum = _saved_run(instance)

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

    def fake_plan(*_args: object, **_kwargs: object) -> tuple[Any, Path]:
        timeline.append("plan")
        operations._log_lifecycle(run_id=RUN_ID, operation="sync", stage="plan", outcome="planned")
        return saved, directory

    def fake_verify(*_args: object, **_kwargs: object) -> tuple[Any, Path]:
        timeline.append("verify")
        operations._log_lifecycle(run_id=RUN_ID, operation="sync", stage="verify", outcome="verified")
        return saved, directory

    def fake_apply(*_args: object, **_kwargs: object) -> ApplyRecord:
        timeline.append("apply")
        operations._log_lifecycle(run_id=RUN_ID, operation="sync", stage="apply", outcome="applied")
        return ApplyRecord(applied_operations=(saved.operations()[0].operation_id,))

    monkeypatch.setattr(operations, "generate_run_id", lambda: RUN_ID)
    monkeypatch.setattr(operations, "_plan_instance", fake_plan)
    monkeypatch.setattr(operations, "_verify_instance", fake_verify)
    monkeypatch.setattr(operations, "_apply_instance", fake_apply)
    caplog.set_level(logging.INFO, logger=operations.__name__)

    result = api.sync(
        api.SyncRequest(
            sync_name=SYNC_NAME,
            config_directory=str(config_directory),
            confirm_writes=True,
        )
    )

    assert timeline == ["plan", "verify", "apply"]
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


def test_public_error_redacts_message_serialization_and_full_traceback(
    config_directory: Path,
    cache_directory: Path,  # noqa: ARG001 - activates the cache fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_with_secret(*_args: object, **_kwargs: object) -> NoReturn:
        msg = f"adapter rejected credential {TOKEN}"
        raise ValueError(msg)

    monkeypatch.setattr(operations, "execute_run", fail_with_secret)
    monkeypatch.setattr(operations, "generate_run_id", lambda: RUN_ID)

    with pytest.raises(api.RunExecutionError) as caught:
        api.plan(api.PlanRequest(sync_name=SYNC_NAME, config_directory=str(config_directory)))

    rendered = "".join(traceback.format_exception(caught.value))
    assert TOKEN not in str(caught.value)
    assert TOKEN not in str(caught.value.model_dump())
    assert TOKEN not in rendered
    assert "***" in rendered


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
