"""Resume an apply whose worker was killed, against a running stack (spec 022 T034, SC-001, SC-005).

The operator path of `dev/specs/022-resume-interrupted-apply/quickstart.md`, end to end: apply
the local NetBox `demo` import, kill the worker once the apply has saved a progress marker,
wait for the run to end interrupted, then recover with `runs show`, `verify` and
`runs resume` only. A new plan of the same configuration version on the same branch must then
hold no create and no update.

The test kills a worker, so it is opt-in twice: it is marked `integration`, and it skips unless
`INFRAHUB_SYNC_RESUME_CHECK_CONFIG` names a registered configuration version as
`CONFIG_ID@VERSION`. Register the local NetBox `demo` package for it by following steps 1 to 4
of "The `from-netbox` example check" in `docs/docs/development/guidelines/testing-tiers.md`.
The smoke package is too small: its apply ends before a progress marker is saved.

The stack is the preview environment (`invoke preview.up`, a host worker process) or, with
`INFRAHUB_SYNC_SMOKE_STACK=dev`, the development stack (the `sync-worker` container). It skips,
never fails, when that stack is not reachable. On the preview stack the test restarts the worker
with `invoke preview.up`, which passes this process's environment to the worker, so set
`INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN` as for `preview.up`: the last plan reads NetBox.
"""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: S404 -- fixed argv: Docker Compose and the preview task, to stop and start the worker
import time
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.client import SyncClient
from tasks import dev
from tasks.preview import REPO_ROOT, STATE_DIR
from tests.preview.test_cli_client import run_cli, run_cli_command
from tests.preview.test_service_api import infrahub_client

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.client.models import RunResource

pytestmark = pytest.mark.integration

CONFIG_VARIABLE = "INFRAHUB_SYNC_RESUME_CHECK_CONFIG"
REASON = "resume check: interrupt an apply and resume it"
# The preview stack's host worker, started by `invoke preview.up` with a pid file.
PREVIEW_WORKER = "prefect-worker"
DEV_WORKER = "sync-worker"
PROGRESS_TIMEOUT_SECONDS = 600.0
# The liveness monitor ends a killed worker's execution after its stall threshold.
INTERRUPTED_TIMEOUT_SECONDS = 900.0
POLL_INTERVAL_SECONDS = 1.0
RESTART_TIMEOUT_SECONDS = 600


def _configuration() -> tuple[str, str]:
    """The registered `CONFIG_ID@VERSION` to import, or skip when none is named."""
    value = os.environ.get(CONFIG_VARIABLE, "")
    config_id, separator, version = value.partition("@")
    if not separator or not config_id or not version.isdigit():
        pytest.skip(
            f"{CONFIG_VARIABLE} is not set to CONFIG_ID@VERSION; register the local NetBox demo package "
            "(testing-tiers.md, the from-netbox example check) and name it"
        )
    return config_id, version


def _on_dev_stack() -> bool:
    return os.environ.get(dev.SMOKE_STACK_VARIABLE) == "dev"


def _kill_worker() -> None:
    """Stop the worker without letting it clean up, as a crash or an out-of-memory kill would."""
    if _on_dev_stack():
        subprocess.run(  # noqa: S603
            ["docker", "compose", "--profile", "sync", "-f", dev.DEV_COMPOSE_FILE, "kill", DEV_WORKER],  # noqa: S607
            cwd=REPO_ROOT,
            check=True,
        )
        return
    pid = int((STATE_DIR / f"{PREVIEW_WORKER}.pid").read_text(encoding="utf-8").strip())
    # `preview.up` starts the worker in its own session, so its process group holds the flow
    # run subprocess that is writing.
    os.killpg(os.getpgid(pid), signal.SIGKILL)


def _start_worker() -> None:
    """Start the worker again; `preview.up` starts only the processes that are not running."""
    command = (
        ["docker", "compose", "--profile", "sync", "-f", dev.DEV_COMPOSE_FILE, "start", DEV_WORKER]
        if _on_dev_stack()
        else ["uv", "run", "invoke", "preview.up"]
    )
    subprocess.run(command, cwd=REPO_ROOT, check=True, timeout=RESTART_TIMEOUT_SECONDS)  # noqa: S603


def _poll(read: Callable[[], RunResource], done: Callable[[RunResource], bool], timeout: float) -> RunResource:
    deadline = time.monotonic() + timeout
    resource = read()
    while not done(resource):
        if time.monotonic() > deadline:
            return pytest.fail(
                f"run {resource.run.run_id} stayed in phase {resource.run.phase} for {timeout}s "
                f"(progress {resource.apply_progress})"
            )
        time.sleep(POLL_INTERVAL_SECONDS)
        resource = read()
    return resource


def _create_branch(preview_env: dict[str, Any]) -> str:
    name = f"resume-check-{uuid.uuid4().hex[:8]}"
    infrahub_client(preview_env).branch.create(branch_name=name, sync_with_git=False, wait_until_completion=True)
    return name


def _plan(preview_env: dict[str, Any], config: tuple[str, str], branch: str, artifacts: dict[str, object]) -> str:
    planned = run_cli(
        preview_env,
        "plan",
        "--config-id",
        config[0],
        "--version",
        config[1],
        "--branch",
        branch,
        "--reason",
        REASON,
        artifacts=artifacts,
        artifact_name=f"plan {len(artifacts)}",
    )
    return planned["run_id"]


def _has_saved_progress(resource: RunResource) -> bool:
    if resource.run.phase == "applied":
        pytest.fail(
            f"the apply finished before a progress marker was saved; use a larger package than {CONFIG_VARIABLE} names"
        )
    progress = resource.apply_progress
    return progress is not None and progress.position is not None and progress.position > 0


def test_a_killed_apply_resumes_to_the_state_of_an_uninterrupted_apply(preview_env: dict[str, Any]) -> None:
    """Interrupt, `runs show`, `verify`, `runs resume`; a new plan then has no create and no update."""
    config = _configuration()
    branch = _create_branch(preview_env)
    artifacts: dict[str, object] = {}
    client = SyncClient(preview_env["urls"]["sync_api"], preview_env["bearer_token"])

    run_id = _plan(preview_env, config, branch, artifacts)
    checksum = client.get_plan(run_id).checksum
    run_cli(
        preview_env,
        "apply",
        run_id,
        "--expected-checksum",
        checksum,
        "--branch",
        branch,
        "--confirm-left-out",
        "--no-wait",
        "--reason",
        REASON,
        artifacts=artifacts,
        artifact_name="apply",
    )

    killed = False
    try:
        _poll(lambda: client.get_run(run_id), _has_saved_progress, PROGRESS_TIMEOUT_SECONDS)
        _kill_worker()
        killed = True
        interrupted = _poll(
            lambda: client.get_run(run_id),
            lambda resource: resource.run.phase == "apply-interrupted",
            INTERRUPTED_TIMEOUT_SECONDS,
        )
        assert interrupted.run.reconciliation_required is True
        assert interrupted.run.outcome == "ambiguous"
    finally:
        if killed:
            _start_worker()

    shown = run_cli_command(preview_env, "runs", "show", run_id, artifacts=artifacts, artifact_name="runs show")
    assert shown.returncode == 0, shown.stderr
    assert "resumable: yes (verify required)" in shown.stdout, shown.stdout
    assert "progress: " in shown.stdout, shown.stdout

    verified = run_cli_command(
        preview_env, "verify", run_id, "--reason", REASON, artifacts=artifacts, artifact_name="verify"
    )
    assert verified.returncode == 0, f"{verified.stdout}\n{verified.stderr}"
    assert "note: runs resume writes the plan's values over every operation reported as differs" in verified.stdout

    run_cli(
        preview_env,
        "runs",
        "resume",
        run_id,
        "--expected-checksum",
        checksum,
        "--confirm-left-out",
        "--reason",
        REASON,
        artifacts=artifacts,
        artifact_name="runs resume",
    )
    resumed = client.get_run(run_id)
    assert resumed.run.phase == "applied", resumed.run
    assert resumed.run.reconciliation_required is False
    assert len(resumed.resumes) == 1
    assert resumed.resumes[0].outcome == "succeeded"

    replanned = client.get_plan(_plan(preview_env, config, branch, artifacts)).summary.by_action
    assert replanned.get("create", 0) == 0, replanned
    assert replanned.get("update", 0) == 0, replanned
