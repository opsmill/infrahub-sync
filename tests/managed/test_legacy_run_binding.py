"""Durable legacy runs keep their existing worker and plan-verification path."""

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.execution import RunResult
from infrahub_sync.managed import flow as managed_flow
from infrahub_sync.managed.flow import managed_sync_run
from infrahub_sync.plan.models import PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import ProductRun, local_product_projection


def _legacy_run(cache: Path, run_id: str, operation: Literal["plan", "verify", "apply"]) -> object:
    """Persist a pre-binding run with only its durable legacy identity fields."""
    projection = local_product_projection(cache)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation=operation,
            configuration_reference="legacy-config-version",
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "legacy-inventory"},
        )
    )
    return projection


def _legacy_saved(run_id: str) -> SavedPlan:
    """Return an unchanged all-absent legacy manifest."""
    return SavedPlan(
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


@pytest.mark.parametrize("stage", ["plan", "verify", "apply"])
def test_all_absent_legacy_run_reaches_existing_local_worker_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: Literal["plan", "verify", "apply"]
) -> None:
    """No durable legacy run is rejected merely because it predates tuple binding."""
    run_id = f"legacy-{stage}"
    projection = _legacy_run(tmp_path / "product", run_id, stage)
    saved = _legacy_saved(run_id)
    resolved: list[tuple[str, str]] = []
    verified: list[tuple[str, object]] = []
    instance = SimpleNamespace(name="legacy-inventory")
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("test-managed"), False))
    monkeypatch.setattr(
        managed_flow,
        "resolve_sync_instance",
        lambda name, *, directory: (resolved.append((name, directory)), instance)[1],
        raising=False,
    )
    monkeypatch.setattr(managed_flow, "resolve_config_version", lambda _instance: "legacy-config-version")
    monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(managed_flow, "_plan", lambda *_args, **_kwargs: saved)
    monkeypatch.setattr(managed_flow, "_publish_plan", lambda *_args, **_kwargs: None)
    if stage == "apply":
        artifact = object()
        monkeypatch.setattr(managed_flow, "resolve_run_directory", lambda *_args: tmp_path)
        monkeypatch.setattr(managed_flow, "read_plan_artifact_bytes", lambda _path: artifact)

        def verify(**kwargs: object) -> list[object]:
            verified.append((str(kwargs["run_id"]), kwargs["config_version"]))
            return []

        monkeypatch.setattr(managed_flow, "verify_plan", verify)
        monkeypatch.setattr(
            managed_flow,
            "parse_plan_artifact",
            lambda *_args, **_kwargs: pytest.fail("legacy verification must not parse a registered binding"),
        )

    def execute(*_args: object, operation: str, **_kwargs: object) -> SavedPlan | RunResult:
        if operation == "verify":
            return saved
        return RunResult(
            sync_name="legacy-inventory",
            operation="apply",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / run_id),
        )

    monkeypatch.setattr(managed_flow, "execute_run", execute)
    if stage == "apply":
        managed_sync_run.fn(run_id, stage, expected_checksum="a" * 64, confirm_writes=True)
    else:
        managed_sync_run.fn(run_id, stage)

    assert resolved == [("legacy-inventory", str(tmp_path))]
    assert verified == ([(run_id, "legacy-config-version")] if stage == "apply" else [])


def test_prefect_binding_carrier_is_optional_only_as_one_closed_group() -> None:
    """Legacy submissions omit all three carrier keys; validation remains in the worker."""
    parameters = inspect.signature(managed_sync_run.fn).parameters
    assert tuple(parameters)[:5] == ("run_id", "stage", "config_id", "registry_version", "package_checksum")
    assert tuple(parameters[name].default for name in ("config_id", "registry_version", "package_checksum")) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("durable_binding", "carrier", "error"),
    [
        pytest.param(
            None, ("config-001", 1, "a" * 64), "managed run binding does not match worker parameters", id="legacy-bound"
        ),
        pytest.param(
            ("config-001", 1, "a" * 64),
            (None, None, None),
            "managed run binding does not match worker parameters",
            id="bound-legacy",
        ),
        pytest.param(
            ("config-001", 1, "a" * 64),
            ("config-001", None, None),
            "managed worker configuration binding parameters must be all absent or all present",
            id="partial",
        ),
    ],
)
def test_cross_product_and_partial_worker_carriers_refuse_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    durable_binding: tuple[str, int, str] | None,
    carrier: tuple[str | None, int | None, str | None],
    error: str,
) -> None:
    """Optional Prefect parameters remain a closed all-absent/all-present carrier."""
    projection = local_product_projection(tmp_path / "product")
    if durable_binding is None:
        run = ProductRun(
            run_id="carrier-refusal",
            operation="plan",
            configuration_reference="legacy-config-version",
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "legacy-inventory"},
        )
    else:
        run = ProductRun(
            run_id="carrier-refusal",
            operation="plan",
            configuration_reference="legacy-config-version",
            config_id=durable_binding[0],
            registry_version=durable_binding[1],
            package_checksum=durable_binding[2],
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "legacy-inventory"},
        )
    projection.create_run(run)
    constructed: list[object] = []
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("test-managed"), False))
    monkeypatch.setattr(
        managed_flow, "resolve_sync_instance", lambda *_args, **_kwargs: constructed.append(True), raising=False
    )
    monkeypatch.setattr(managed_flow, "resolve_runtime_instance", lambda *_args, **_kwargs: constructed.append(True))

    with pytest.raises(RuntimeError, match=error):
        managed_sync_run.fn("carrier-refusal", "plan", *carrier)
    assert constructed == []
