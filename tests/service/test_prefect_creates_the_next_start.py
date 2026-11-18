"""Prefect creates the flow run of the next start as soon as a schedule is created or changed (spec 021, R1, R10).

Prefect's scheduler creates the flow runs of a schedule only for the start times after the
moment its loop runs, about once a minute (`prefect/server/services/scheduler.py`,
`_collect_flow_runs`, `start_time=right_now`). A schedule created 30 seconds before a start got no
flow run for that start on the development stack. A change of a schedule also deletes the future
flow runs of every schedule of the deployment (`prefect/server/api/deployments.py`,
`update_deployment_schedule`). The match step therefore asks Prefect to create the flow runs at
once, through `PrefectOrchestration.create_scheduled_runs`. This test runs that call against the
Prefect test server, with the scheduler service turned off, and checks that the flow runs are the
ones the worker admits: `auto_scheduled`, created by the schedule, with the start time as
`expected_start_time`, and one flow run for each start.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

pytest.importorskip("prefect")

from prefect import get_client
from prefect.client.schemas.filters import DeploymentFilter, DeploymentFilterId
from prefect.settings import PREFECT_SERVER_SERVICES_SCHEDULER_ENABLED, temporary_settings
from prefect.testing.utilities import prefect_test_harness

from infrahub_sync.service.orchestration import (
    SERVICE_DEFINITION,
    DeploymentSchedule,
    PrefectOrchestration,
)

if TYPE_CHECKING:
    from prefect.client.orchestration import PrefectClient
    from prefect.client.schemas.objects import FlowRun

_SLUG = "s-" + "c" * 32
_EVERY_MINUTE = DeploymentSchedule(slug=_SLUG, cron="* * * * *", timezone="UTC", active=True)


async def _flow_runs(client: PrefectClient, deployment_id: UUID) -> list[FlowRun]:
    return list(
        await client.read_flow_runs(deployment_filter=DeploymentFilter(id=DeploymentFilterId(any_=[deployment_id])))
    )


def _next_minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0) + timedelta(minutes=1)


async def _check() -> None:
    async with get_client() as client:
        flow_id = await client.create_flow_from_name(SERVICE_DEFINITION.flow_name)
        deployment_id = await client.create_deployment(flow_id=flow_id, name=SERVICE_DEFINITION.deployment_name)
        orchestration = PrefectOrchestration(client)

        before = datetime.now(timezone.utc)
        schedule_id = await orchestration.create_schedule(_EVERY_MINUTE)
        assert await _flow_runs(client, deployment_id) == []

        await orchestration.create_scheduled_runs()
        await orchestration.create_scheduled_runs()

        runs = await _flow_runs(client, deployment_id)
        starts = sorted(run.expected_start_time for run in runs)
        assert len(starts) == len(set(starts)), "one flow run for each start"
        first = min(runs, key=lambda run: run.expected_start_time)
        assert first.expected_start_time in {_next_minute(before), _next_minute(datetime.now(timezone.utc))}
        assert first.auto_scheduled is True
        assert first.created_by is not None
        assert str(first.created_by.id) == schedule_id
        assert first.parameters == {"schedule_id": _SLUG, "stage": "sync"}

        # A pause and a resume change the schedule: Prefect deletes its flow runs whose start time
        # is still to come, and the call creates them again from now.
        await orchestration.update_schedule(schedule_id, replace(_EVERY_MINUTE, active=False))
        await orchestration.update_schedule(schedule_id, _EVERY_MINUTE)
        assert await _future_flow_runs(client, deployment_id) == []
        await orchestration.create_scheduled_runs()
        assert await _future_flow_runs(client, deployment_id)


async def _future_flow_runs(client: PrefectClient, deployment_id: UUID) -> list[FlowRun]:
    """The flow runs whose start time is still to come; a minute can turn while the test runs."""
    now = datetime.now(timezone.utc)
    return [
        run
        for run in await _flow_runs(client, deployment_id)
        if run.expected_start_time is not None and run.expected_start_time > now
    ]


def test_the_flow_run_of_the_next_start_exists_as_soon_as_the_schedule_is_created() -> None:
    with temporary_settings({PREFECT_SERVER_SERVICES_SCHEDULER_ENABLED: False}), prefect_test_harness():
        asyncio.run(_check())
