"""Offline behavioural tests for deployment apply."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import (
    DeploymentCreate,
    DeploymentUpdate,
    WorkPoolCreate,
)
from prefect.client.schemas.responses import DeploymentResponse
from prefect.exceptions import ObjectAlreadyExists, ObjectNotFound
from prefect.testing.utilities import prefect_test_harness

import opsmill_prefect_extras.deployments as deployments
from opsmill_prefect_extras.deployments import (
    DefinitionPreflightError,
    DeploymentApplyConnectionError,
    DeploymentApplyReport,
    DeploymentApplyResult,
    DeploymentClient,
    apply_deployments,
)
from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition


@dataclass
class FakeDeployment:
    """A JSON-shaped Prefect deployment response for the client seam."""

    id: UUID
    payload: Mapping[str, Any]

    def model_dump(self, *, mode: str, exclude_none: bool) -> Mapping[str, Any]:
        """Return the stored response payload in Prefect's used shape."""
        assert mode == "json"
        assert exclude_none is True
        return self.payload


class FakePrefectClient:
    """Stateful stand-in for Prefect's read-before-write and upsert behavior."""

    def __init__(self, *, fail_flow_names: set[str] | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.deployment_payloads: list[dict[str, Any]] = []
        self.flow_ids: dict[str, UUID] = {}
        self.deployments: dict[str, FakeDeployment] = {}
        self.read_overrides: dict[str, DeploymentResponse] = {}
        self.fail_flow_names = fail_flow_names or set()

    async def create_flow_from_name(self, flow_name: str) -> UUID:
        self.calls.append(("create_flow_from_name", flow_name))
        if flow_name in self.fail_flow_names:
            raise RuntimeError("exception-sentinel-value")
        return self.flow_ids.setdefault(flow_name, uuid4())

    async def create_deployment(self, flow_id: UUID, **payload: Any) -> UUID:
        self.calls.append(("create_deployment", (flow_id, payload)))
        self.deployment_payloads.append(payload)
        flow_name = next(
            name for name, identifier in self.flow_ids.items() if identifier == flow_id
        )
        key = f"{flow_name}/{payload['name']}"
        normalized = DeploymentCreate(flow_id=flow_id, **payload).model_dump(
            mode="json", exclude_none=True
        )
        if key in self.deployments:
            existing = self.deployments[key]
            existing.payload = normalized
            return existing.id
        deployment_id = uuid4()
        self.deployments[key] = FakeDeployment(id=deployment_id, payload=normalized)
        return deployment_id

    async def read_deployment_by_name(
        self, name: str
    ) -> FakeDeployment | DeploymentResponse:
        self.calls.append(("read_deployment_by_name", name))
        if name in self.read_overrides:
            return self.read_overrides[name]
        try:
            return self.deployments[name]
        except KeyError:
            raise ObjectNotFound(RuntimeError("missing-deployment")) from None

    async def update_deployment(
        self, deployment_id: UUID, deployment: DeploymentUpdate
    ) -> None:
        self.calls.append(("update_deployment", deployment_id))
        for existing in self.deployments.values():
            if existing.id == deployment_id:
                existing.payload = {
                    **existing.payload,
                    **deployment.model_dump(mode="json", exclude_unset=True),
                }
                return
        raise AssertionError("unknown fake deployment")


def _definition(
    flow_name: str,
    deployment_name: str = "run",
    **kwargs: Any,
) -> WorkflowDefinition:
    """Build a definition without resolving a real flow module."""
    return WorkflowDefinition(
        flow_name=flow_name,
        deployment_name=deployment_name,
        module="tests.workflows.flows",
        function="my_sync_flow",
        **kwargs,
    )


def _apply(*args: Any, **kwargs: Any) -> DeploymentApplyReport:
    """Run the public asynchronous call without adding a pytest plugin."""
    return asyncio.run(apply_deployments(*args, **kwargs))


def test_public_surface_is_explicit() -> None:
    assert deployments.__all__ == [
        "ApplyStatus",
        "DefinitionPreflightError",
        "DeploymentApplyConnectionError",
        "DeploymentApplyReport",
        "DeploymentApplyResult",
        "DeploymentClient",
        "DeploymentClientFactory",
        "DeploymentRecord",
        "apply_deployments",
    ]


def test_deployments_import_does_not_initialize_workflow_siblings() -> None:
    script = """
import sys
import opsmill_prefect_extras.deployments

for module in (
    "opsmill_prefect_extras.workflows.catalogue",
    "opsmill_prefect_extras.workflows.validation",
):
    assert module not in sys.modules, module
"""

    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, check=False, text=True
    )

    assert completed.returncode == 0, completed.stderr


def test_workflow_public_names_are_discoverable_without_eager_imports() -> None:
    script = """
import sys
import opsmill_prefect_extras.workflows as workflows

assert set(workflows.__all__) <= set(dir(workflows))
for module in (
    "opsmill_prefect_extras.workflows.catalogue",
    "opsmill_prefect_extras.workflows.validation",
):
    assert module not in sys.modules, module
"""

    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, check=False, text=True
    )

    assert completed.returncode == 0, completed.stderr


def test_apply_uses_prefects_flow_then_deployment_calls_and_renders_inputs() -> None:
    client = FakePrefectClient()
    definitions = (
        _definition("one"),
        _definition(
            "inventory-refresh",
            "scheduled",
            cron="0 2 * * *",
            concurrency_limit=1,
        ),
        _definition("three"),
    )

    report = _apply(
        definitions,
        work_pool_name="application-pool",
        job_variables={"image": "image-sentinel-value", "replicas": 2},
        client=client,
    )

    assert [result.status for result in report.results] == ["created"] * 3
    assert [call[0] for call in client.calls] == [
        "read_deployment_by_name",
        "create_flow_from_name",
        "create_deployment",
        "read_deployment_by_name",
        "create_flow_from_name",
        "create_deployment",
        "read_deployment_by_name",
        "create_flow_from_name",
        "create_deployment",
    ]
    payload = client.deployment_payloads[1]
    assert payload["name"] == "scheduled"
    assert payload["schedules"] == [{"schedule": {"cron": "0 2 * * *"}}]
    assert payload["concurrency_limit"] == 1
    assert "concurrency_options" not in payload
    assert payload["work_pool_name"] == "application-pool"
    assert payload["job_variables"] == {
        "image": "image-sentinel-value",
        "replicas": 2,
    }
    assert "inventory-refresh/scheduled" in client.deployments


def test_second_apply_is_unchanged_and_drift_is_updated() -> None:
    client = FakePrefectClient()
    definition = _definition("inventory-refresh", "scheduled", tags=("inventory",))

    first = _apply((definition,), work_pool_name="pool", client=client)
    second = _apply((definition,), work_pool_name="pool", client=client)
    client.deployments[definition.key].payload = {
        **client.deployments[definition.key].payload,
        "tags": ["drifted"],
    }
    third = _apply((definition,), work_pool_name="pool", client=client)

    assert [result.status for result in first.results] == ["created"]
    assert [result.status for result in second.results] == ["unchanged"]
    assert [result.status for result in third.results] == ["updated"]
    assert [call[0] for call in client.calls].count("update_deployment") == 1


@pytest.mark.parametrize(
    ("definition_kwargs", "cleared_fields"),
    [
        pytest.param(
            {"cron": "0 2 * * *"},
            {"schedules": []},
            id="schedule",
        ),
        pytest.param(
            {"concurrency_limit": 2, "collision_strategy": "CANCEL_NEW"},
            {"concurrency_limit": None, "concurrency_options": None},
            id="concurrency-settings",
        ),
        pytest.param(
            {"entrypoint": "tests.workflows.flows:my_sync_flow"},
            {"entrypoint": None},
            id="entrypoint",
        ),
    ],
)
def test_removing_supported_owned_optional_settings_converges(
    definition_kwargs: Mapping[str, Any],
    cleared_fields: Mapping[str, object],
) -> None:
    client = FakePrefectClient()
    configured = _definition("inventory-refresh", "scheduled", **definition_kwargs)
    without_optional_settings = _definition("inventory-refresh", "scheduled")

    created = _apply(
        (configured,),
        work_pool_name="pool",
        client=client,
    )
    removed = _apply((without_optional_settings,), work_pool_name="pool", client=client)
    unchanged = _apply(
        (without_optional_settings,), work_pool_name="pool", client=client
    )

    assert [result.status for result in created.results] == ["created"]
    assert [result.status for result in removed.results] == ["updated"]
    assert [result.status for result in unchanged.results] == ["unchanged"]
    payload = client.deployments[configured.key].payload
    assert all(payload[field] == value for field, value in cleared_fields.items())
    assert [call[0] for call in client.calls].count("update_deployment") == 1


def test_create_race_converges_after_a_defensive_conflict() -> None:
    class RacingClient(FakePrefectClient):
        async def create_deployment(self, flow_id: UUID, **payload: Any) -> UUID:
            await super().create_deployment(flow_id, **payload)
            raise ObjectAlreadyExists(RuntimeError("concurrent-create"))

    client = RacingClient()
    definition = _definition("inventory-refresh", "scheduled")

    report = _apply((definition,), work_pool_name="pool", client=client)

    assert [result.status for result in report.results] == ["unchanged"]


def test_response_schedule_metadata_converges_without_an_update() -> None:
    client = FakePrefectClient()
    definition = _definition("inventory-refresh", "scheduled", cron="0 2 * * *")

    _apply((definition,), work_pool_name="pool", client=client)
    created = client.deployments[definition.key]
    client.read_overrides[definition.key] = DeploymentResponse.model_validate(
        {
            "id": str(created.id),
            "name": definition.deployment_name,
            "flow_id": str(client.flow_ids[definition.flow_name]),
            "schedules": [
                {
                    "id": str(uuid4()),
                    "deployment_id": str(created.id),
                    "schedule": {"cron": "0 2 * * *"},
                    "active": True,
                    "parameters": {},
                }
            ],
            "job_variables": {},
            "tags": [],
            "work_pool_name": "pool",
        }
    )

    report = _apply((definition,), work_pool_name="pool", client=client)

    assert [result.status for result in report.results] == ["unchanged"]
    assert [call[0] for call in client.calls].count("update_deployment") == 0


def test_real_prefect_server_second_scheduled_apply_is_unchanged() -> None:
    definition = _definition("integration-flow", "nightly", cron="0 2 * * *")

    async def scenario() -> tuple[DeploymentApplyReport, DeploymentApplyReport]:
        async with get_client() as client:
            await client.create_work_pool(
                WorkPoolCreate(name="integration-pool", type="process")
            )
            first = await apply_deployments(
                (definition,),
                work_pool_name="integration-pool",
                client=cast(DeploymentClient, client),
            )
            second = await apply_deployments(
                (definition,),
                work_pool_name="integration-pool",
                client=cast(DeploymentClient, client),
            )
            return first, second

    with prefect_test_harness():
        first, second = asyncio.run(scenario())

    assert [result.status for result in first.results] == ["created"]
    assert [result.status for result in second.results] == ["unchanged"]


def test_real_prefect_server_omitted_job_variables_remain_unmanaged() -> None:
    definition = _definition("integration-flow", "job-variables")
    job_variables = {
        "image": "registry.example/application:previous",
        "env": {"MODE": "managed"},
    }

    async def scenario() -> tuple[
        DeploymentApplyReport,
        DeploymentApplyReport,
        DeploymentApplyReport,
        Mapping[str, Any],
    ]:
        async with get_client() as client:
            await client.create_work_pool(
                WorkPoolCreate(name="integration-pool", type="process")
            )
            created = await apply_deployments(
                (definition,),
                work_pool_name="integration-pool",
                job_variables=job_variables,
                client=cast(DeploymentClient, client),
            )
            first_omitted = await apply_deployments(
                (definition,),
                work_pool_name="integration-pool",
                client=cast(DeploymentClient, client),
            )
            second_omitted = await apply_deployments(
                (definition,),
                work_pool_name="integration-pool",
                client=cast(DeploymentClient, client),
            )
            persisted = await client.read_deployment_by_name(definition.key)
            return created, first_omitted, second_omitted, persisted.job_variables

    with prefect_test_harness():
        created, first_omitted, second_omitted, persisted_job_variables = asyncio.run(
            scenario()
        )

    assert [result.status for result in created.results] == ["created"]
    assert [result.status for result in first_omitted.results] == ["unchanged"]
    assert [result.status for result in second_omitted.results] == ["unchanged"]
    assert persisted_job_variables == job_variables


@pytest.mark.skipif(
    "global_concurrency_limit" not in DeploymentResponse.model_fields,
    reason="this Prefect response schema predates global concurrency limits",
)
def test_response_global_concurrency_limit_converges_without_an_update() -> None:
    client = FakePrefectClient()
    definition = _definition("inventory-refresh", "scheduled", concurrency_limit=1)

    _apply((definition,), work_pool_name="pool", client=client)
    created = client.deployments[definition.key]
    client.read_overrides[definition.key] = DeploymentResponse.model_validate(
        {
            "id": str(created.id),
            "name": definition.deployment_name,
            "flow_id": str(client.flow_ids[definition.flow_name]),
            "concurrency_limit": None,
            "global_concurrency_limit": {
                "id": str(uuid4()),
                "name": "deployment-limit",
                "limit": 1,
                "active_slots": 0,
                "slot_decay_per_second": 0.0,
            },
            "schedules": [],
            "job_variables": {},
            "tags": [],
            "work_pool_name": "pool",
        }
    )

    report = _apply((definition,), work_pool_name="pool", client=client)

    assert [result.status for result in report.results] == ["unchanged"]
    assert [call[0] for call in client.calls].count("update_deployment") == 0


def test_distinct_flow_and_deployment_names_use_the_catalogue_address() -> None:
    client = FakePrefectClient()
    definition = _definition("inventory-refresh", "scheduled")

    report = _apply((definition,), work_pool_name="pool", client=client)

    assert report.created[0].key == "inventory-refresh/scheduled"
    assert "inventory-refresh/scheduled" in client.deployments


def test_a_server_failure_is_per_definition_and_later_ones_continue() -> None:
    client = FakePrefectClient(fail_flow_names={"bad"})

    report = _apply(
        (_definition("good"), _definition("bad"), _definition("later")),
        work_pool_name="pool",
        client=client,
    )

    assert [
        (result.key, result.status, result.error_type) for result in report.results
    ] == [
        ("good/run", "created", None),
        ("bad/run", "failed", "RuntimeError"),
        ("later/run", "created", None),
    ]
    assert "later/run" in client.deployments


def test_preflight_schema_and_definition_refusals_do_not_call_the_client() -> None:
    client = FakePrefectClient()
    malformed = _definition("bad", "run")
    object.__setattr__(malformed, "deployment_name", "not/a-deployment")
    bad_schema = _definition("schema", "run", cron="not a cron")

    report = _apply(
        (malformed, bad_schema, _definition("good")),
        work_pool_name="pool",
        client=client,
    )

    assert [(result.status, result.error_type) for result in report.results] == [
        ("failed", DefinitionPreflightError.__name__),
        ("failed", "ValidationError"),
        ("created", None),
    ]
    assert [call[0] for call in client.calls] == [
        "read_deployment_by_name",
        "create_flow_from_name",
        "create_deployment",
    ]


@pytest.mark.parametrize(
    ("failure_point", "expected_calls"),
    [
        pytest.param("factory", ["factory"], id="factory-call"),
        pytest.param("context-entry", ["factory", "enter"], id="context-entry"),
    ],
)
def test_client_opening_failure_is_typed_without_retaining_provider_exception(
    failure_point: str,
    expected_calls: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    canary = "connection-sentinel-value"

    class UnavailableContext:
        async def __aenter__(self) -> FakePrefectClient:
            calls.append("enter")
            raise RuntimeError(canary)

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> None:
            raise AssertionError("context entry failure must not call __aexit__")

    def unavailable() -> UnavailableContext:
        calls.append("factory")
        if failure_point == "factory":
            raise RuntimeError(canary)
        return UnavailableContext()

    with pytest.raises(DeploymentApplyConnectionError) as excinfo:
        _apply(
            (_definition("never-attempted"),),
            work_pool_name="pool",
            client_factory=unavailable,
        )

    assert excinfo.value.cause_type == "RuntimeError"
    assert canary not in f"{excinfo.value}\n{excinfo.value!r}\n{caplog.text}"
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert calls == expected_calls


def test_client_context_receives_an_iteration_failure() -> None:
    exit_types: list[type[BaseException] | None] = []

    class RecordingContext:
        async def __aenter__(self) -> FakePrefectClient:
            return FakePrefectClient()

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> bool:
            exit_types.append(exc_type)
            return False

    def definitions() -> Iterator[WorkflowDefinition]:
        yield _definition("first")
        raise LookupError("iteration-sentinel")

    with pytest.raises(LookupError, match="iteration-sentinel"):
        _apply(
            definitions(),
            work_pool_name="pool",
            client_factory=RecordingContext,
        )

    assert exit_types == [LookupError]


def test_apply_rejects_ambiguous_client_injection() -> None:
    client = FakePrefectClient()

    @asynccontextmanager
    async def client_context() -> AsyncIterator[FakePrefectClient]:
        yield client

    with pytest.raises(ValueError, match="either client or client_factory"):
        _apply(
            (_definition("never-attempted"),),
            work_pool_name="pool",
            client=client,
            client_factory=client_context,
        )


def test_reports_or_logs_do_not_disclose_job_variables_or_exception_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakePrefectClient(fail_flow_names={"bad"})
    secret_job_variables = {
        "image": "image-sentinel-value",
        "credential": "credential-sentinel-value",
    }

    report = _apply(
        (_definition("bad"),),
        work_pool_name="pool",
        job_variables=secret_job_variables,
        client=client,
    )

    rendered = f"{report!r}\n{caplog.text}"
    for secret in (*secret_job_variables.values(), "exception-sentinel-value"):
        assert secret not in rendered
    assert report.failed[0].error_type == "RuntimeError"
    assert report.failed[0].key == "bad/run"


def test_report_groups_structured_outcomes_without_storing_failure_values() -> None:
    definition = _definition("one")
    report = DeploymentApplyReport(
        results=(
            DeploymentApplyResult(definition=definition, status="created"),
            DeploymentApplyResult(
                definition=_definition("two"), status="failed", error_type="ValueError"
            ),
        )
    )

    assert [result.key for result in report.created] == ["one/run"]
    assert [result.key for result in report.failed] == ["two/run"]
    assert report.updated == ()
    assert report.unchanged == ()
    assert report.is_successful is False


def test_result_rejects_an_inconsistent_failure_shape() -> None:
    definition = _definition("one")

    with pytest.raises(ValueError, match="needs an error type"):
        DeploymentApplyResult(definition=definition, status="failed")
    with pytest.raises(ValueError, match="cannot have an error type"):
        DeploymentApplyResult(
            definition=definition, status="created", error_type="RuntimeError"
        )
