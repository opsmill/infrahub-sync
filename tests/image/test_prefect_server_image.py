"""The Sync image serves the Prefect server under a read-only root filesystem (spec 021 FR-026).

The Compose files run `prefect-server` from the Sync image, read-only over the image's
scratch roots. This starts the same command in the image under test, with no database
setting, so Prefect keeps its SQLite database under `PREFECT_HOME`, which is a scratch root,
and checks that `/api/health` answers.
"""

from __future__ import annotations

import time

import pytest

from tests.image.conftest import WRITABLE_ROOTS, docker, tmpfs_arguments

pytestmark = pytest.mark.docker

PREFECT_COMMAND = (
    "uvicorn",
    "--host",
    "127.0.0.1",
    "--port",
    "4200",
    "--factory",
    "infrahub_sync.service.prefect_server:create_prefect_app",
)
HEALTH_PROBE = "import httpx; httpx.get('http://127.0.0.1:4200/api/health', timeout=5).raise_for_status(); print('ok')"
READY_SECONDS = 120


def test_the_prefect_server_answers_its_health_check_under_a_read_only_root(image_ref: str) -> None:
    started = docker(
        [
            "run",
            "--detach",
            "--read-only",
            "--network=none",
            *tmpfs_arguments(WRITABLE_ROOTS),
            "--env",
            "PREFECT_UI_ENABLED=false",
            "--env",
            "PREFECT_SERVER_ANALYTICS_ENABLED=false",
            image_ref,
            *PREFECT_COMMAND,
        ]
    )
    assert started.returncode == 0, started.stderr
    container = started.stdout.strip()
    try:
        deadline = time.monotonic() + READY_SECONDS
        answered = None
        while time.monotonic() < deadline:
            answered = docker(["exec", container, "python", "-c", HEALTH_PROBE], timeout=30)
            if answered.returncode == 0:
                break
            time.sleep(2)
        logs = docker(["logs", "--tail", "40", container])
        assert answered is not None
        assert answered.returncode == 0, f"/api/health did not answer within {READY_SECONDS}s\n{logs.stdout}{logs.stderr}"
        assert answered.stdout.strip().endswith("ok")
    finally:
        docker(["rm", "--force", container])
