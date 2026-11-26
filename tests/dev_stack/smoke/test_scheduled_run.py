"""Scheduled runs on the development stack (spec 021, quickstart.md sections 3 and 4).

Section 4, a schedule with branch review. The Prefect schedule of the Sync schedule starts the runs; the test waits for them. The cron
expression is one start a minute, so the test takes a few minutes. Infrahub is read back through
the Infrahub SDK: the branch of each run, its proposed change, and the close of the earlier one.

The source is the smoke branch and the destination is `main`, which declares no branch, as branch
review requires. The worker's token needs the permissions of contracts/branch-review.md.

Before it registers anything, the test asks the Sync API whether the write target is delivered,
with a create request that names a configuration that does not exist: `422
schedule-setting-not-available` skips the test, and `404` lets it go on. After two runs it pauses
the schedule, so a third start cannot change what it checks, and in `finally` it deletes the
schedule and the branches of its runs; deleting a branch sets its proposed change to `canceled`.
The configuration stays registered, because the Sync API has no request that removes one.

The test does not cover the parts of section 4 that need a person in Infrahub (an approval, a
merge) or a stopped worker.

Section 3, a schedule that writes to the default branch, on a Sync API that does not require an
approved plan. The test checks that a scheduled run starts within one minute of its start time
(SC-007), from the run's `scheduled_start_at` and `started_at`. It then recreates the worker
alone with `REQUIRE_APPROVAL=true` and checks that the worker stops the schedule at its next start
(the `schedule.stopped` event has the operation `schedule-start`), and then recreates the Sync API
with the same setting and checks that a create is refused with `approval-required-for-schedule`.
In `finally` it deletes the schedule and recreates both services with `REQUIRE_APPROVAL=false`.
The Compose commands name the stack's project, which `tasks.dev` reads from the development file
and `COMPOSE_PROJECT_NAME`.
"""

from __future__ import annotations

import os
import shlex
import subprocess  # noqa: S404 -- fixed argv: Docker Compose, to recreate the Sync API and the worker
import time
import uuid
from contextlib import suppress
from datetime import datetime, timedelta
from operator import itemgetter
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk.exceptions import Error as InfrahubSdkError

from tasks import dev
from tasks.smoke_dataset import SMOKE_BRANCH
from tasks.utils import REPO_BASE as REPO_ROOT
from tests.dev_stack.smoke.test_service_api import (
    authenticated_client,
    idempotency_headers,
    infrahub_client,
    seed_source_branch,
    smoke_package,
)

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

pytestmark = pytest.mark.smoke

# Two starts a minute apart, plus the time a run takes on the development stack.
RUN_TIMEOUT_SECONDS = 360.0
# The proposed change is opened after the run's phase is `applied`.
REVIEW_TIMEOUT_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 5.0
REASON = "smoke: a schedule with branch review"
TERMINAL_PHASES = frozenset({"applied", "planned", "sync-failed", "failed", "cancelled"})


def _review_package(infrahub_url: str) -> dict[str, Any]:
    """The smoke package, reading the smoke branch and writing to `main` through branch review."""
    package = smoke_package(infrahub_url)
    configuration = package["configuration"]
    configuration["name"] = f"smoke-review-{uuid.uuid4().hex[:8]}"
    configuration["source"]["settings"]["branch"] = SMOKE_BRANCH
    del configuration["destination"]["settings"]["branch"]
    return package


def write_target_delivered(client: httpx.Client, write_target: str) -> bool:
    """Ask the Sync API whether it runs schedules with ``write_target``, and create nothing.

    The Sync API checks the write target before it reads the configuration, so a create request
    that names a configuration that does not exist answers `422 schedule-setting-not-available`
    when the write target is not delivered, and `404` when it is.
    """
    probe = client.post(
        "/schedules",
        headers=idempotency_headers(),
        json={
            "name": f"smoke-probe-{uuid.uuid4().hex[:8]}",
            "config_id": f"smoke-not-registered-{uuid.uuid4().hex[:8]}",
            "registry_version": 1,
            "cron": "* * * * *",
            "timezone": "UTC",
            "write_target": write_target,
            "reason": REASON,
        },
    )
    if probe.status_code == 422 and probe.json()["error"]["code"] == "schedule-setting-not-available":
        return False
    assert probe.status_code == 404, probe.text
    return True


def _scheduled_runs(client: httpx.Client, schedule_id: str) -> list[dict[str, Any]]:
    """Every run of the schedule, oldest start first."""
    response = client.get("/runs", params={"schedule_id": schedule_id, "trigger_type": "scheduled"})
    assert response.status_code == 200, response.text
    return sorted(response.json()["items"], key=itemgetter("scheduled_start_at"))


def _finished(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in runs if run["phase"] in TERMINAL_PHASES]


def _wait_for_runs(client: httpx.Client, schedule_id: str, count: int) -> None:
    """Poll until the schedule has ``count`` finished runs."""
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    finished: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        finished = _finished(_scheduled_runs(client, schedule_id))
        if len(finished) >= count:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"schedule {schedule_id} finished {len(finished)} of {count} runs in {RUN_TIMEOUT_SECONDS}s")


def _pause(client: httpx.Client, schedule_id: str) -> None:
    response = client.post(f"/schedules/{schedule_id}/pause", headers=idempotency_headers(), json={"reason": REASON})
    assert response.status_code == 200, response.text


def _settled_runs(client: httpx.Client, schedule_id: str) -> list[dict[str, Any]]:
    """Wait until no run of the paused schedule is in progress, and return every run."""
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    runs: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        runs = _scheduled_runs(client, schedule_id)
        if runs and len(_finished(runs)) == len(runs):
            return runs
        time.sleep(POLL_INTERVAL_SECONDS)
    return pytest.fail(f"a run of the paused schedule {schedule_id} is still in progress: {runs}")


def _branch_review(client: httpx.Client, run: dict[str, Any]) -> dict[str, Any]:
    """The run's `branch_review`, once its second part is saved when the run wrote to its branch.

    The first part (`branch`, `branch_state`) is saved with the stage result; the proposed change
    is opened after that, so the test polls until `proposed_change_id` or `failure` is present.
    """
    deadline = time.monotonic() + REVIEW_TIMEOUT_SECONDS
    review: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run['run_id']}/results")
        assert response.status_code == 200, response.text
        review = response.json()["results"]["branch_review"]
        if review["branch_state"] != "written" or review.get("proposed_change_id") or review.get("failure"):
            return review
        time.sleep(POLL_INTERVAL_SECONDS)
    return pytest.fail(f"run {run['run_id']} opened no proposed change in {REVIEW_TIMEOUT_SECONDS}s: {review}")


def _proposed_change(infrahub: Any, proposed_change_id: str) -> Any:  # noqa: ANN401 - the SDK's node.
    return infrahub.get(kind="CoreProposedChange", id=proposed_change_id, branch="main")


def _comments(infrahub: Any, proposed_change_id: str) -> list[str]:  # noqa: ANN401 - the SDK's sync client.
    """The text of each comment on a proposed change, read as `CoreChangeComment` nodes."""
    nodes = infrahub.filters(kind="CoreChangeComment", change__ids=[proposed_change_id], branch="main")
    return [node.text.value for node in nodes]


def _branch_names(infrahub: Any) -> set[str]:  # noqa: ANN401 - the SDK's sync client.
    return set(infrahub.branch.all())


def _delete_schedule_and_branches(client: httpx.Client, infrahub: Any, schedule_id: str, name: str) -> None:  # noqa: ANN401
    """Delete the schedule and every branch of its runs; deleting a branch cancels its proposed change."""
    with suppress(AssertionError):
        _pause(client, schedule_id)
    client.post(f"/schedules/{schedule_id}/delete", headers=idempotency_headers(), json={"reason": REASON})
    for branch in _branch_names(infrahub):
        if branch.startswith(f"sync/{name}-"):
            with suppress(InfrahubSdkError):
                infrahub.branch.delete(branch)


def test_a_schedule_with_branch_review_opens_and_replaces_its_proposed_changes(
    smoke_env: dict[str, Any], tmp_path: Path
) -> None:
    """Section 4: a branch and a proposed change for each run; the next run replaces the earlier one."""
    with authenticated_client(smoke_env, transcript=tmp_path / "scheduled-run-http.jsonl") as client:
        if not write_target_delivered(client, "branch-review"):
            pytest.skip("branch review is not offered by this Sync API yet (task T058)")
        seed_source_branch(smoke_env)
        infrahub = infrahub_client(smoke_env)
        registered = client.post(
            "/configs",
            headers=idempotency_headers(),
            json={"package": _review_package(smoke_env["urls"]["infrahub"]), "reason": REASON},
        )
        assert registered.status_code == 201, registered.text
        version = registered.json()["version"]
        name = f"smoke-review-{uuid.uuid4().hex[:8]}"
        created = client.post(
            "/schedules",
            headers=idempotency_headers(),
            json={
                "name": name,
                "config_id": version["config_id"],
                "registry_version": version["registry_version"],
                "cron": "* * * * *",
                "timezone": "UTC",
                "write_target": "branch-review",
                "reason": REASON,
            },
        )
        assert created.status_code == 201, created.text
        schedule_id = created.json()["schedule_id"]
        try:
            _wait_for_runs(client, schedule_id, 2)
            # No start after this one: a third run would replace the second one's proposed change.
            _pause(client, schedule_id)
            first, second = _settled_runs(client, schedule_id)[-2:]

            for run in (first, second):
                assert run["destination_branch"].startswith(f"sync/{name}-")
            first_review = _branch_review(client, first)
            second_review = _branch_review(client, second)
            assert first_review.get("failure") is None, first_review
            assert second_review.get("failure") is None, second_review

            if first_review["branch_state"] == "written":
                # The second run replaced the first: its proposed change is closed with a comment
                # that names the second, and its branch no longer exists.
                assert second_review["replaced"] == [first["run_id"]], second_review
                assert first["destination_branch"] not in _branch_names(infrahub)
                assert _proposed_change(infrahub, first_review["proposed_change_id"]).state.value == "closed"
                comments = _comments(infrahub, first_review["proposed_change_id"])
                assert any(second_review["proposed_change_id"] in comment for comment in comments), comments
            else:
                # The source did not change: the run left no branch and no proposed change.
                assert first["outcome"] == "no-change"
                assert first["destination_branch"] not in _branch_names(infrahub)

            if second_review["branch_state"] == "written":
                assert second_review["proposed_change_id"] is not None, second_review
                assert second["destination_branch"] in _branch_names(infrahub)
                assert _proposed_change(infrahub, second_review["proposed_change_id"]).state.value == "open"
                shown = client.get(f"/schedules/{schedule_id}").json()
                assert shown["open_branch"]["branch"] == second["destination_branch"], shown
                assert shown["open_branch"]["proposed_change_id"] == second_review["proposed_change_id"]
            else:
                assert second["outcome"] == "no-change"
                assert second["destination_branch"] not in _branch_names(infrahub)
        finally:
            _delete_schedule_and_branches(client, infrahub, schedule_id, name)


# ---------------------------------------------------------------------------
# Section 3: a schedule that writes to the default branch
# ---------------------------------------------------------------------------

DEFAULT_BRANCH_REASON = "smoke: a schedule that writes to the default branch"
# SC-007: a scheduled run starts within one minute of its start time.
START_DELAY_LIMIT = timedelta(minutes=1)
# A start a minute, plus the time the worker takes to start again after it is recreated.
STOP_TIMEOUT_SECONDS = 240.0
RECREATE_TIMEOUT_SECONDS = 600
# The seconds of a minute in which the worker is recreated: after the start of the minute's run,
# and early enough that the worker runs again before the next start.
QUIET_SECONDS = (15, 30)


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _recreate(services: tuple[str, ...], *, require_approval: bool) -> None:
    """Recreate Sync services of the stack's own project with `REQUIRE_APPROVAL` set.

    The worker is given no credential: it reads `infrahub-token` from the Sync API, which keeps it.
    """
    settings = dev.dev_settings()
    command = [
        *shlex.split(dev._compose_command(settings.project, settings.profiles)),
        "up",
        "--detach",
        "--wait",
        "--wait-timeout",
        str(dev.WAIT_TIMEOUT_SECONDS),
        "--force-recreate",
        "--no-deps",
        *services,
    ]
    environment = {
        **os.environ,
        "REQUIRE_APPROVAL": "true" if require_approval else "false",
    }
    subprocess.run(  # noqa: S603 -- fixed argv against the development stack
        command, cwd=REPO_ROOT, env=environment, check=True, timeout=RECREATE_TIMEOUT_SECONDS
    )


def _approval_required(client: httpx.Client) -> bool:
    response = client.get("/me")
    assert response.status_code == 200, response.text
    return bool(response.json()["approval_required"])


def _wait_for_first_run(client: httpx.Client, schedule_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        finished = _finished(_scheduled_runs(client, schedule_id))
        if finished:
            return finished[0]
        time.sleep(POLL_INTERVAL_SECONDS)
    return pytest.fail(f"schedule {schedule_id} finished no run in {RUN_TIMEOUT_SECONDS}s")


def _wait_for_a_quiet_moment(client: httpx.Client, schedule_id: str) -> None:
    """Wait until no run of the schedule is in progress, early in a minute, far from the next start."""
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        runs = _scheduled_runs(client, schedule_id)
        if len(_finished(runs)) == len(runs) and QUIET_SECONDS[0] <= datetime.now().second <= QUIET_SECONDS[1]:  # noqa: DTZ005 -- only the second of the minute is read
            return
        time.sleep(1)
    pytest.fail(f"a run of schedule {schedule_id} stayed in progress for {RUN_TIMEOUT_SECONDS}s")


def _wait_for_stop(client: httpx.Client, schedule_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    shown: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/schedules/{schedule_id}")
        assert response.status_code == 200, response.text
        shown = response.json()
        if shown["state"] == "stopped":
            return shown
        time.sleep(POLL_INTERVAL_SECONDS)
    return pytest.fail(f"schedule {schedule_id} is not stopped after {STOP_TIMEOUT_SECONDS}s: {shown}")


def _schedule_events(client: httpx.Client, schedule_id: str, event_type: str) -> list[dict[str, Any]]:
    response = client.get(
        "/events", params={"related": f"schedule:{schedule_id}", "event_type": event_type, "limit": 100}
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_a_schedule_on_the_default_branch_starts_on_time_and_stops_when_approval_is_required(
    smoke_env: dict[str, Any], tmp_path: Path
) -> None:
    """Section 3: a run starts within one minute; the worker and the Sync API refuse it under approval."""
    if os.environ.get(dev.SMOKE_STACK_VARIABLE) == dev.DEPS_SMOKE_STACK:
        pytest.skip("the worker runs on the host in dependencies mode; run this test with `invoke dev.start`")
    with authenticated_client(smoke_env, transcript=tmp_path / "default-branch-http.jsonl") as client:
        if _approval_required(client):
            pytest.skip("the Sync API requires an approved plan; section 3 starts on one that does not")
        if not write_target_delivered(client, "default-branch"):
            pytest.skip("the default branch is not offered by this Sync API yet (task T041)")
        seed_source_branch(smoke_env)
        package = smoke_package(smoke_env["urls"]["infrahub"])
        package["configuration"]["name"] = f"smoke-default-{uuid.uuid4().hex[:8]}"
        registered = client.post(
            "/configs", headers=idempotency_headers(), json={"package": package, "reason": DEFAULT_BRANCH_REASON}
        )
        assert registered.status_code == 201, registered.text
        version = registered.json()["version"]
        created = client.post(
            "/schedules",
            headers=idempotency_headers(),
            json={
                "name": f"smoke-default-{uuid.uuid4().hex[:8]}",
                "config_id": version["config_id"],
                "registry_version": version["registry_version"],
                "cron": "* * * * *",
                "timezone": "UTC",
                "write_target": "default-branch",
                "reason": DEFAULT_BRANCH_REASON,
            },
        )
        assert created.status_code == 201, created.text
        schedule = created.json()
        schedule_id = schedule["schedule_id"]
        recreated = False
        try:
            assert _time(schedule["next_start_at"]) - _time(schedule["created_at"]) <= START_DELAY_LIMIT, schedule

            run = _wait_for_first_run(client, schedule_id)
            assert run["trigger_type"] == "scheduled", run
            delay = _time(run["started_at"]) - _time(run["scheduled_start_at"])
            assert delay <= START_DELAY_LIMIT, f"run {run['run_id']} started {delay} after its start time"
            assert run["destination_branch"] in {None, SMOKE_BRANCH}, run

            # The worker alone requires approval: its next start stops the schedule and creates no run.
            # It is recreated while no run is in progress, so no run is interrupted.
            _wait_for_a_quiet_moment(client, schedule_id)
            recreated = True
            _recreate(("sync-worker",), require_approval=True)
            runs_before = {item["run_id"] for item in _scheduled_runs(client, schedule_id)}
            stopped = _wait_for_stop(client, schedule_id)
            assert stopped["state_reason"] == "approval-required", stopped
            events = _schedule_events(client, schedule_id, "infrahub_sync.schedule.stopped")
            assert [event["operation"] for event in events] == ["schedule-start"], events
            assert {item["run_id"] for item in _scheduled_runs(client, schedule_id)} == runs_before

            # The Sync API requires approval too: it refuses a schedule that writes to the default branch.
            _recreate(("sync-api",), require_approval=True)
            assert _approval_required(client)
            refused = client.post(
                "/schedules",
                headers=idempotency_headers(),
                json={
                    "name": f"smoke-default-{uuid.uuid4().hex[:8]}",
                    "config_id": version["config_id"],
                    "registry_version": version["registry_version"],
                    "cron": "* * * * *",
                    "timezone": "UTC",
                    "write_target": "default-branch",
                    "reason": DEFAULT_BRANCH_REASON,
                },
            )
            assert refused.status_code == 409, refused.text
            assert refused.json()["error"]["code"] == "approval-required-for-schedule"
        finally:
            with suppress(AssertionError):
                _pause(client, schedule_id)
            client.post(
                f"/schedules/{schedule_id}/delete",
                headers=idempotency_headers(),
                json={"reason": DEFAULT_BRANCH_REASON},
            )
            if recreated:
                _recreate(("sync-api", "sync-worker"), require_approval=False)
