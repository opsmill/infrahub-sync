"""The worker runs only a run bound to a registered configuration.

A stored run that names no registered configuration gets one fixed refusal, whichever
stage is asked for and whatever binding the Prefect parameters carry. Parameters that
carry part of a binding, or a binding other than the stored run's, are refused too.
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import service_sync_run
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection

FLOW_RUN_ID = "ed4778cb-f2cf-4b1f-a87b-68be37659e93"
WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
RUN_ID = "worker-run-binding"
BINDING = ("config-001", 1, "a" * 64)
NO_BINDING_REFUSAL = (
    "the Sync service worker runs only a run with a complete registered configuration binding, and this run has none"
)

Carrier = tuple[str | None, int | None, str | None]


def _stored_run(cache: Path, binding: tuple[str, int, str] | None) -> ProductProjection:
    """Store one run with a submitted execution, bound to `binding` or to no configuration."""
    projection = local_product_projection(cache)
    config_id, registry_version, package_checksum = binding or (None, None, None)
    projection.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="plan",
            configuration_reference="stored-config-version",
            config_id=config_id,
            registry_version=registry_version,
            package_checksum=package_checksum,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    append_execution(
        projection,
        RUN_ID,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose="plan", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )
    return projection


@pytest.fixture
def constructed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Claim as a known worker, and record every runtime or write guard the stage constructs."""
    built: list[str] = []
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-service"), False))
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", lambda *_args, **_kwargs: built.append("runtime"))
    monkeypatch.setattr(service_flow, "service_guard_session", lambda: built.append("guard"))
    monkeypatch.setattr(service_flow, "read_plan_artifact_bytes", lambda _path: built.append("artifact"))
    return built


@pytest.mark.parametrize("stage", ["plan", "verify", "apply", "sync", "resume"])
@pytest.mark.parametrize("carrier", [(None, None, None), BINDING], ids=["no-parameters", "bound-parameters"])
def test_a_stored_run_without_a_configuration_gets_one_fixed_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    constructed: list[str],
    stage: Literal["plan", "verify", "apply", "sync", "resume"],
    carrier: Carrier,
) -> None:
    """Every stage refuses with the same text, before a runtime, an artifact or a guard exists."""
    projection = _stored_run(tmp_path / "product", None)
    monkeypatch.setattr(service_flow, "_runtime", lambda: projection)

    with pytest.raises(RuntimeError) as refusal:
        service_sync_run.fn(RUN_ID, stage, *carrier, expected_checksum="a" * 64, confirm_writes=True)

    assert str(refusal.value) == f"ValueError: {NO_BINDING_REFUSAL}"
    assert constructed == []


@pytest.mark.parametrize(
    ("carrier", "error"),
    [
        pytest.param((None, None, None), NO_BINDING_REFUSAL, id="absent"),
        pytest.param(
            ("config-001", None, None),
            "service worker configuration binding parameters must all be present",
            id="partial",
        ),
        pytest.param(
            ("config-002", 1, "a" * 64), "service run binding does not match worker parameters", id="other-binding"
        ),
    ],
)
def test_parameters_that_do_not_carry_the_stored_binding_refuse_before_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, constructed: list[str], carrier: Carrier, error: str
) -> None:
    """A registered run refuses parameters with no binding, part of one, or another run's."""
    projection = _stored_run(tmp_path / "product", BINDING)
    monkeypatch.setattr(service_flow, "_runtime", lambda: projection)

    with pytest.raises(RuntimeError, match=error):
        service_sync_run.fn(RUN_ID, "plan", *carrier)

    assert constructed == []


def test_the_flow_keeps_its_binding_parameters_in_order_with_no_default_binding() -> None:
    """The deployment's parameter schema is unchanged; the worker refuses a missing binding."""
    parameters = inspect.signature(service_sync_run.fn).parameters
    assert tuple(parameters)[:5] == ("run_id", "stage", "config_id", "registry_version", "package_checksum")
    assert [parameters[name].default for name in ("config_id", "registry_version", "package_checksum")] == [
        None,
        None,
        None,
    ]
