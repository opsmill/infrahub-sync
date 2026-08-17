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

if TYPE_CHECKING:
    from invoke.tasks import Task


def test_preview_routes_prefect_ui_to_the_published_host_port() -> None:
    compose = (DEV_DIR / "docker-compose.preview.yml").read_text(encoding="utf-8")

    assert 'PREFECT_SERVER_UI_API_URL: "http://localhost:${PREVIEW_PREFECT_PORT:-4210}/api"' in compose


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
