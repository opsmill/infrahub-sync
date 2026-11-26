"""The Prefect scheduler creates the flow runs of a deployment schedule (spec 021).

A Sync schedule is a Prefect deployment schedule, and every scheduled run starts from a flow run
that Prefect's scheduler service inserts. With SQLAlchemy 2.1 and Prefect 3.8.6 the insert raises
`InvalidRequestError: Can't evaluate bulk DML statement`, so no schedule ever starts a run; the
smoke test of the development stack found it. This test calls the same server function that the
scheduler service calls, against the Prefect test server.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("prefect")

from prefect import get_client
from prefect.client.schemas.actions import DeploymentScheduleCreate
from prefect.client.schemas.schedules import CronSchedule
from prefect.server import models
from prefect.server.database import provide_database_interface
from prefect.testing.utilities import prefect_test_harness


async def _scheduled_flow_run_count() -> int:
    async with get_client() as client:
        flow_id = await client.create_flow_from_name("scheduler-check")
        deployment_id = await client.create_deployment(
            flow_id=flow_id,
            name="scheduler-check",
            schedules=[DeploymentScheduleCreate(schedule=CronSchedule(cron="* * * * *", timezone="UTC"))],
        )
    db = provide_database_interface()
    async with db.session_context(begin_transaction=True) as session:
        created = await models.deployments.schedule_runs(
            session,
            deployment_id=deployment_id,
            end_time=datetime.now(timezone.utc) + timedelta(minutes=5),
            min_runs=3,
            max_runs=3,
        )
    return len(created)


def test_the_scheduler_inserts_the_flow_runs_of_a_cron_schedule() -> None:
    with prefect_test_harness():
        assert asyncio.run(_scheduled_flow_run_count()) == 3
