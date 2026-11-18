"""A schedule with branch review on the development stack (spec 021, quickstart.md section 4).

The Prefect schedule of the Sync schedule starts the runs; the test waits for them. The cron
expression is one start a minute, so the test takes a few minutes. Infrahub is read back through
the Infrahub SDK: the branch of each run, its proposed change, and the close of the earlier one.

The source is the smoke branch and the destination is `main`, which declares no branch, as branch
review requires. The worker's token needs the permissions of contracts/branch-review.md.

Until branch review is offered to users (task T058), the Sync API refuses the schedule with
`422 schedule-setting-not-available`, and this test skips. The test does not cover the parts of
section 4 that need a person in Infrahub (an approval, a merge) or a stopped worker.
"""

from __future__ import annotations

import time
import uuid
from contextlib import suppress
from operator import itemgetter
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk.exceptions import Error as InfrahubSdkError

from tasks.smoke_dataset import SMOKE_BRANCH
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


def _finished_runs(client: httpx.Client, schedule_id: str) -> list[dict[str, Any]]:
    """The finished runs of the schedule, oldest first."""
    response = client.get("/runs", params={"schedule_id": schedule_id, "trigger_type": "scheduled"})
    assert response.status_code == 200, response.text
    runs = [run for run in response.json()["items"] if run["phase"] in TERMINAL_PHASES]
    return sorted(runs, key=itemgetter("scheduled_start_at"))


def _wait_for_runs(client: httpx.Client, schedule_id: str, count: int) -> list[dict[str, Any]]:
    """Poll until the schedule has ``count`` finished runs."""
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    runs: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        runs = _finished_runs(client, schedule_id)
        if len(runs) >= count:
            return runs
        time.sleep(POLL_INTERVAL_SECONDS)
    return pytest.fail(f"schedule {schedule_id} finished {len(runs)} of {count} runs in {RUN_TIMEOUT_SECONDS}s")


def _proposed_change(infrahub: Any, proposed_change_id: str) -> Any:  # noqa: ANN401 - the SDK's node.
    return infrahub.get(kind="CoreProposedChange", id=proposed_change_id, branch="main")


def _comments(infrahub: Any, proposed_change_id: str) -> list[str]:  # noqa: ANN401 - the SDK's sync client.
    """The text of each comment on a proposed change, read as `CoreChangeComment` nodes."""
    nodes = infrahub.filters(kind="CoreChangeComment", change__ids=[proposed_change_id], branch="main")
    return [node.text.value for node in nodes]


def _branch_names(infrahub: Any) -> set[str]:  # noqa: ANN401 - the SDK's sync client.
    return set(infrahub.branch.all())


def test_a_schedule_with_branch_review_opens_and_replaces_its_proposed_changes(
    smoke_env: dict[str, Any], tmp_path: Path
) -> None:
    """Section 4: a branch and a proposed change for each run; the next run replaces the earlier one."""
    seed_source_branch(smoke_env)
    infrahub = infrahub_client(smoke_env)
    with authenticated_client(smoke_env, transcript=tmp_path / "scheduled-run-http.jsonl") as client:
        registered = client.post(
            "/configs",
            headers=idempotency_headers(),
            json={"package": _review_package(smoke_env["urls"]["infrahub"]), "reason": REASON},
        )
        assert registered.status_code == 201, registered.text
        version = registered.json()["version"]
        created = client.post(
            "/schedules",
            headers=idempotency_headers(),
            json={
                "name": f"smoke-review-{uuid.uuid4().hex[:8]}",
                "config_id": version["config_id"],
                "registry_version": version["registry_version"],
                "cron": "* * * * *",
                "timezone": "UTC",
                "write_target": "branch-review",
                "reason": REASON,
            },
        )
        if created.status_code == 422 and created.json()["error"]["code"] == "schedule-setting-not-available":
            pytest.skip("branch review is not offered by this Sync API yet (task T058)")
        assert created.status_code == 201, created.text
        schedule_id = created.json()["schedule_id"]
        try:
            first, second = _wait_for_runs(client, schedule_id, 2)[:2]

            for run in (first, second):
                assert run["destination_branch"].startswith(f"sync/{created.json()['name']}-")
            first_review = client.get(f"/runs/{first['run_id']}/results").json()["results"]["branch_review"]
            second_review = client.get(f"/runs/{second['run_id']}/results").json()["results"]["branch_review"]

            if first_review["branch_state"] == "written":
                # The second run replaced the first: its proposed change is closed with a comment
                # that names the second, and its branch no longer exists.
                assert second_review["replaced"] == [first["run_id"]], second_review
                assert first["destination_branch"] not in _branch_names(infrahub)
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
            client.post(f"/schedules/{schedule_id}/delete", headers=idempotency_headers(), json={"reason": REASON})
            # The schedule leaves its open branch in Infrahub when it is deleted; the test removes it.
            for name in _branch_names(infrahub):
                if name.startswith(f"sync/{created.json()['name']}-"):
                    with suppress(InfrahubSdkError):
                        infrahub.branch.delete(name)
