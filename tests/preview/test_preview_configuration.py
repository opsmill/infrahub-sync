"""Regression checks for the disposable preview environment."""

from contextlib import nullcontext
from typing import TYPE_CHECKING, cast

import infrahub_sdk
import pytest
from infrahub_sdk.exceptions import ServerNotReachableError
from invoke import Context

from tasks import preview
from tasks.preview import (
    COMPOSE_FILES,
    DEV_DIR,
    ENV_FILE,
    EXPECT_MAIN_EMPTY_ENV,
    REPO_ROOT,
    SMOKE_BRANCH,
    ensure_smoke_branch,
)

PREVIEW_MINIO_SECRET = "preview-minio-secret"  # noqa: S105 - disposable local contract canary.

if TYPE_CHECKING:
    from invoke.tasks import Task


def test_preview_routes_prefect_ui_to_the_published_host_port() -> None:
    compose = (DEV_DIR / "docker-compose.preview.yml").read_text(encoding="utf-8")

    assert 'PREFECT_SERVER_UI_API_URL: "http://localhost:${PREVIEW_PREFECT_PORT:-4210}/api"' in compose


def test_preview_declares_the_managed_postgresql_and_minio_storage_shape() -> None:
    """Preview supplies storage and liveness settings to both managed processes."""
    compose = (DEV_DIR / "docker-compose.preview.yml").read_text(encoding="utf-8")
    environment = preview._runtime_env(
        {
            "INFRAHUB_INITIAL_ADMIN_TOKEN": "local-token",
            "PREVIEW_BEARER_TOKENS": "{}",
            "PREVIEW_INFRAHUB_PORT": "8080",
            "PREVIEW_PREFECT_PORT": "4210",
            "PREVIEW_SYNC_API_PORT": "8090",
            "PREVIEW_STORAGE_POSTGRES_PORT": "5439",
            "PREVIEW_MINIO_PORT": "9010",
            "PREVIEW_MINIO_ACCESS_KEY": "preview-minio-access",
            "PREVIEW_MINIO_SECRET_KEY": PREVIEW_MINIO_SECRET,
            "PREVIEW_S3_BUCKET": "infrahub-sync-preview",
            "PREVIEW_WORK_POOL": "preview-pool",
            "PREVIEW_RUN_ADMISSION_TTL_SECONDS": "600",
            "PREVIEW_PREFECT_WORKER_QUERY_SECONDS": "15",
        }
    )

    assert "sync-postgres:" in compose
    assert "sync-minio:" in compose
    assert "sync-minio-bootstrap:" in compose
    assert "mc mb --ignore-existing" in compose
    assert "${PREVIEW_MINIO_ACCESS_KEY" in compose
    assert "${PREVIEW_MINIO_SECRET_KEY" in compose
    assert environment["INFRAHUB_SYNC_DATABASE_URL"] == "postgresql://postgres:postgres@127.0.0.1:5439/infrahub_sync"
    assert environment["INFRAHUB_SYNC_S3_BUCKET"] == "infrahub-sync-preview"
    assert environment["INFRAHUB_SYNC_S3_ENDPOINT_URL"] == "http://127.0.0.1:9010"
    assert environment["AWS_ACCESS_KEY_ID"] == "preview-minio-access"
    assert environment["AWS_SECRET_ACCESS_KEY"] == PREVIEW_MINIO_SECRET
    assert "INFRAHUB_SYNC_CACHE_DIR" in environment
    assert "INFRAHUB_SYNC_MANAGED_CACHE_LOCATION" not in environment
    assert environment["INFRAHUB_SYNC_MANAGED_WORK_POOL"] == "preview-pool"
    assert environment["INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS"] == "600"
    assert environment["PREFECT_WORKER_QUERY_SECONDS"] == "15"


def test_preview_minio_healthcheck_is_self_contained_before_bootstrap() -> None:
    """MinIO becomes healthy without relying on the alias created by its bootstrap."""
    compose = (DEV_DIR / "docker-compose.preview.yml").read_text(encoding="utf-8")
    minio_start = compose.index("  sync-minio:\n")
    bootstrap_start = compose.index("  sync-minio-bootstrap:\n")
    bootstrap_end = compose.index("  infrahub-server:\n")
    minio_service = compose[minio_start:bootstrap_start]
    bootstrap_service = compose[bootstrap_start:bootstrap_end]

    assert 'test: ["CMD", "curl", "--fail", "http://localhost:9000/minio/health/live"]' in minio_service
    assert "mc ready" not in minio_service
    assert "depends_on:\n      sync-minio:\n        condition: service_healthy" in bootstrap_service


def test_preview_smoke_reads_the_worker_published_plan_artifact_through_the_api() -> None:
    """The static smoke contract proves Preview reads a worker-published artifact via the API."""
    smoke = (REPO_ROOT / "tests" / "preview" / "test_managed_api.py").read_text(encoding="utf-8")
    planned = smoke.index('planned = _wait_for_phase(client, run_id, "planned")')
    artifact = smoke.index('plan_view = client.get(f"/runs/{run_id}/plan")')
    assert planned < artifact
    assert "assert plan_view.status_code == 200" in smoke
    assert 'assert plan_payload["checksum_ok"] is True' in smoke


def test_compose_receives_merged_local_preview_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str], bool]] = []
    context = Context()
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda command, *, env, pty: calls.append((command, env, pty)))

    values = {
        "COMPOSE_PROJECT_NAME": "preview-test",
        "PREVIEW_PREFECT_PORT": "4321",
    }
    preview._compose(context, "config", values)

    assert calls == [
        (
            "docker compose --project-name preview-test "
            f"--env-file {ENV_FILE} " + " ".join(f"-f {path}" for path in COMPOSE_FILES) + " config",
            values,
            False,
        )
    ]


def test_smoke_branch_creation_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[tuple[str, bool]] = []

    class BranchManager:
        def __init__(self) -> None:
            self.branches: dict[str, object] = {}

        def all(self) -> dict[str, object]:
            return self.branches

        def create(self, branch_name: str, *, sync_with_git: bool) -> None:
            created.append((branch_name, sync_with_git))
            self.branches[branch_name] = object()

    branch_manager = BranchManager()

    class Client:
        branch = branch_manager

    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", lambda **_kwargs: Client())
    environment = {"INFRAHUB_ADDRESS": "http://127.0.0.1:8080", "INFRAHUB_API_TOKEN": "local-token"}

    ensure_smoke_branch(environment)
    ensure_smoke_branch(environment)

    assert created == [(SMOKE_BRANCH, False)]


def test_standalone_smoke_ensures_its_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    context = Context()
    values = {"COMPOSE_PROJECT_NAME": "preview-test"}
    environment = {
        "INFRAHUB_ADDRESS": "http://localhost:8080",
        "INFRAHUB_API_TOKEN": "local-token",
        EXPECT_MAIN_EMPTY_ENV: "1",
    }

    monkeypatch.setattr(preview, "load_preview_env", lambda: values)
    monkeypatch.setattr(preview, "_runtime_env", lambda _values: environment)
    monkeypatch.setattr(preview, "ensure_smoke_branch", lambda env: events.append(("branch", env)))
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(
        context,
        "run",
        lambda command, *, env: events.append(("run", command, env)),
    )

    cast("Task", preview.smoke).body(context)

    assert EXPECT_MAIN_EMPTY_ENV not in environment
    assert events == [
        ("branch", environment),
        ("run", "uv run pytest -m preview tests/preview -q", environment),
    ]


def test_startup_smoke_requests_the_pristine_main_check(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    context = Context()
    values = {"COMPOSE_PROJECT_NAME": "preview-test"}
    environment = {"INFRAHUB_ADDRESS": "http://localhost:8080", "INFRAHUB_API_TOKEN": "local-token"}

    monkeypatch.setattr(preview, "load_preview_env", lambda: values)
    monkeypatch.setattr(preview, "_runtime_env", lambda _values: environment)
    monkeypatch.setattr(preview, "ensure_smoke_branch", lambda _env: None)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(
        context,
        "run",
        lambda command, *, env: events.append((command, env.copy())),
    )

    preview._run_smoke(context, expect_main_empty=True)

    assert events == [
        (
            "uv run pytest -m preview tests/preview -q",
            {**environment, EXPECT_MAIN_EMPTY_ENV: "1"},
        )
    ]


def test_actual_smoke_path_receives_the_preview_aws_credential_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reusable smoke command receives the same MinIO credentials as API, worker, and CLI."""
    events: list[tuple[str, dict[str, str]]] = []
    context = Context()
    values = {
        "COMPOSE_PROJECT_NAME": "preview-test",
        "INFRAHUB_INITIAL_ADMIN_TOKEN": "local-token",
        "PREVIEW_BEARER_TOKENS": "{}",
        "PREVIEW_INFRAHUB_PORT": "8080",
        "PREVIEW_PREFECT_PORT": "4210",
        "PREVIEW_SYNC_API_PORT": "8090",
        "PREVIEW_STORAGE_POSTGRES_PORT": "5439",
        "PREVIEW_MINIO_PORT": "9010",
        "PREVIEW_MINIO_ACCESS_KEY": "preview-minio-access",
        "PREVIEW_MINIO_SECRET_KEY": PREVIEW_MINIO_SECRET,
        "PREVIEW_S3_BUCKET": "infrahub-sync-preview",
        "PREVIEW_WORK_POOL": "preview-pool",
        "PREVIEW_RUN_ADMISSION_TTL_SECONDS": "600",
        "PREVIEW_PREFECT_WORKER_QUERY_SECONDS": "15",
    }

    monkeypatch.setattr(preview, "load_preview_env", lambda: values)
    monkeypatch.setattr(preview, "ensure_smoke_branch", lambda _env: None)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda command, *, env: events.append((command, env.copy())))

    preview._run_smoke(context, expect_main_empty=False)

    assert len(events) == 1
    command, smoke_environment = events[0]
    assert command == "uv run pytest -m preview tests/preview -q"
    assert smoke_environment["AWS_ACCESS_KEY_ID"] == "preview-minio-access"
    assert smoke_environment["AWS_SECRET_ACCESS_KEY"] == PREVIEW_MINIO_SECRET


def test_standalone_smoke_leaves_an_unreachable_environment_to_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    context = Context()
    values = {"COMPOSE_PROJECT_NAME": "preview-test"}
    environment = {"INFRAHUB_ADDRESS": "http://localhost:8080", "INFRAHUB_API_TOKEN": "local-token"}

    monkeypatch.setattr(preview, "load_preview_env", lambda: values)
    monkeypatch.setattr(preview, "_runtime_env", lambda _values: environment)
    monkeypatch.setattr(
        preview,
        "ensure_smoke_branch",
        lambda _env: (_ for _ in ()).throw(ServerNotReachableError(environment["INFRAHUB_ADDRESS"])),
    )
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(
        context,
        "run",
        lambda command, *, env: events.append(("run", command, env)),
    )

    cast("Task", preview.smoke).body(context)

    assert events == [("run", "uv run pytest -m preview tests/preview -q", environment)]


def test_netbox_tutorial_uses_the_preview_1_tag_for_code_and_configuration() -> None:
    tutorial = (REPO_ROOT / "docs/docs/tutorials/netbox-demo-to-infrahub.mdx").read_text(encoding="utf-8")

    assert "infrahub-sync.git@v3-preview.1" in tutorial
    assert "infrahub-sync/refs/tags/v3-preview.1/examples/netbox_to_infrahub/config.yml" in tutorial
