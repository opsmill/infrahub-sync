"""Regression checks for the disposable preview environment."""

import json
import subprocess  # noqa: S404 -- fixed Docker Compose argv resolves configuration without starting services
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, cast

import infrahub_sdk
import pytest
from invoke import Context, Result, UnexpectedExit

from tasks import preview
from tasks.preview import (
    COMPOSE_FILES,
    DEV_DIR,
    ENV_FILE,
    REPO_ROOT,
    SHARED_DEVICE_NAME,
    SHARED_DEVICE_SEED_TYPE,
    SMOKE_BRANCH,
    SMOKE_KIND,
    ensure_smoke_branch,
)

PREVIEW_MINIO_SECRET = "preview-minio-secret"  # noqa: S105 - disposable local contract canary.
UP_VALUES = {
    "COMPOSE_PROJECT_NAME": "preview-test",
    "PREVIEW_INFRAHUB_PORT": "8080",
    "PREVIEW_PREFECT_PORT": "4210",
    "PREVIEW_SYNC_API_PORT": "8090",
    "PREVIEW_WORK_POOL": "preview-pool",
    "PREVIEW_BEARER_TOKENS": '{"tester@local": {"token": "t", "administrator": true}}',
}
SMOKE_ENVIRONMENT = {"INFRAHUB_ADDRESS": "http://127.0.0.1:8080", "INFRAHUB_API_TOKEN": "local-token"}
# The source the CLI-cycle smoke plans against the same branch.
CLI_SMOKE_SOURCE = REPO_ROOT / "examples" / "custom_adapter" / "custom_adapter_src" / "mock_db.json"

if TYPE_CHECKING:
    from invoke.tasks import Task


@pytest.fixture(autouse=True)
def clean_image_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep preview image checks independent of the invoking shell."""
    for key in (
        "VERSION",
        "INFRAHUB_DOCKER_IMAGE",
        "INFRAHUB_DOCKER_IMAGE_DIGEST",
        "PREVIEW_PREFECT_IMAGE_TAG",
        "PREVIEW_PREFECT_IMAGE_DIGEST",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(
    ("override", "digest_name"),
    [
        ("VERSION=9.9.9", "INFRAHUB_DOCKER_IMAGE_DIGEST"),
        ("PREVIEW_PREFECT_IMAGE_TAG=3.9.0-python3.12", "PREVIEW_PREFECT_IMAGE_DIGEST"),
    ],
)
def test_preview_rejects_tag_only_image_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, override: str, digest_name: str
) -> None:
    """A changed tag must never silently keep the previous image digest."""
    local_env = tmp_path / "preview.local.env"
    local_env.write_text(f"{override}\n", encoding="utf-8")
    monkeypatch.setattr(preview, "LOCAL_ENV_FILE", local_env)

    with pytest.raises(preview.PreviewError, match=digest_name):
        preview.load_preview_env()


def test_preview_accepts_a_local_image_with_an_empty_digest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A local Infrahub build can keep the existing image-name override."""
    local_env = tmp_path / "preview.local.env"
    local_env.write_text("INFRAHUB_DOCKER_IMAGE=local-infrahub\nINFRAHUB_DOCKER_IMAGE_DIGEST=\n", encoding="utf-8")
    monkeypatch.setattr(preview, "LOCAL_ENV_FILE", local_env)

    values = preview.load_preview_env()

    assert values["INFRAHUB_DOCKER_IMAGE"] == "local-infrahub"
    assert not values["INFRAHUB_DOCKER_IMAGE_DIGEST"]


@pytest.mark.parametrize("source", ["file", "shell"])
def test_preview_image_name_override_drops_the_shipped_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str
) -> None:
    """A local image selected by either override path stays unpinned."""
    local_env = tmp_path / "preview.local.env"
    if source == "file":
        local_env.write_text("INFRAHUB_DOCKER_IMAGE=local-infrahub\n", encoding="utf-8")
    else:
        monkeypatch.setenv("INFRAHUB_DOCKER_IMAGE", "local-infrahub")
    monkeypatch.setattr(preview, "LOCAL_ENV_FILE", local_env)

    values = preview.load_preview_env()

    assert values["INFRAHUB_DOCKER_IMAGE"] == "local-infrahub"
    assert not values["INFRAHUB_DOCKER_IMAGE_DIGEST"]


def test_preview_accepts_a_registry_mirror_with_the_shipped_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicitly paired mirror may serve the same content digest."""
    local_env = tmp_path / "preview.local.env"
    monkeypatch.setattr(preview, "LOCAL_ENV_FILE", local_env)
    shipped = preview.load_preview_env()
    local_env.write_text(
        f"INFRAHUB_DOCKER_IMAGE=mirror.example/infrahub\n"
        f"INFRAHUB_DOCKER_IMAGE_DIGEST={shipped['INFRAHUB_DOCKER_IMAGE_DIGEST']}\n",
        encoding="utf-8",
    )
    values = preview.load_preview_env()

    assert values["INFRAHUB_DOCKER_IMAGE"] == "mirror.example/infrahub"
    assert values["INFRAHUB_DOCKER_IMAGE_DIGEST"] == shipped["INFRAHUB_DOCKER_IMAGE_DIGEST"]


@pytest.mark.parametrize(
    ("setting", "value", "digest_name"),
    [
        ("PREVIEW_PREFECT_IMAGE_TAG", "3.9.0-python3.12", "PREVIEW_PREFECT_IMAGE_DIGEST"),
    ],
)
def test_preview_rejects_shell_tag_only_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, setting: str, value: str, digest_name: str
) -> None:
    """A shell tag override cannot pair with a digest from preview.env."""
    monkeypatch.setattr(preview, "LOCAL_ENV_FILE", tmp_path / "preview.local.env")
    monkeypatch.setenv(setting, value)

    with pytest.raises(preview.PreviewError, match=digest_name):
        preview.load_preview_env()


def test_preview_ignores_unrelated_shell_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The shipped Infrahub version wins over a generic shell variable."""
    monkeypatch.setattr(preview, "LOCAL_ENV_FILE", tmp_path / "preview.local.env")
    monkeypatch.setenv("VERSION", "unrelated-version")

    values = preview.load_preview_env()

    assert values["VERSION"] == "1.10.6"


def test_preview_routes_prefect_ui_to_the_published_host_port() -> None:
    compose = (DEV_DIR / "docker-compose.preview.yml").read_text(encoding="utf-8")

    assert 'PREFECT_SERVER_UI_API_URL: "http://localhost:${PREVIEW_PREFECT_PORT:-4210}/api"' in compose


def test_preview_declares_the_service_postgresql_and_minio_storage_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preview supplies storage and liveness settings to both service processes.

    Every retired name is seeded first, as an operator's shell would carry it across the
    cutover, so the absences asserted below are removals rather than values that merely
    happened not to be set.
    """
    compose = (DEV_DIR / "docker-compose.preview.yml").read_text(encoding="utf-8")
    for retired in preview.RETIRED_SERVICE_SETTINGS:
        monkeypatch.setenv(retired, f"/inherited/{retired.lower()}-canary")
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
    # Seeded above and gone here: the retired names are removed, not merely unset.
    for retired in preview.RETIRED_SERVICE_SETTINGS:
        assert retired not in environment, retired
    assert environment["INFRAHUB_SYNC_SERVICE_WORK_POOL"] == "preview-pool"
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


def test_preview_prefect_waits_for_successful_minio_bootstrap() -> None:
    """The waited Prefect service exposes bootstrap failure to Compose up --wait."""
    command = ["docker", "compose", "--project-name", "preview-startup-test", "--env-file", str(ENV_FILE)]
    for compose_file in COMPOSE_FILES:
        command.extend(("-f", str(compose_file)))
    command.extend(("config", "--format", "json"))

    result = subprocess.run(command, check=True, capture_output=True, text=True)  # noqa: S603
    services = json.loads(result.stdout)["services"]

    assert services["sync-minio-bootstrap"]["depends_on"]["sync-minio"]["condition"] == "service_healthy"
    assert (
        services["sync-prefect"]["depends_on"]["sync-minio-bootstrap"]["condition"] == "service_completed_successfully"
    )


def test_preview_up_uses_a_bounded_compose_wait(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Preview startup cannot wait forever for its container dependency chain."""
    compose_calls: list[str] = []
    context = Context()

    class StopAfterComposeError(RuntimeError):
        pass

    def record_compose(_context: Context, arguments: str, _values: dict[str, str]) -> None:
        compose_calls.append(arguments)
        raise StopAfterComposeError

    monkeypatch.setattr(preview, "STATE_DIR", tmp_path / ".preview")
    monkeypatch.setattr(
        preview,
        "load_preview_env",
        lambda: {
            "COMPOSE_PROJECT_NAME": "preview-test",
            "PREVIEW_INFRAHUB_PORT": "8080",
            "PREVIEW_PREFECT_PORT": "4210",
            "PREVIEW_SYNC_API_PORT": "8090",
        },
    )
    monkeypatch.setattr(
        preview,
        "_runtime_env",
        lambda _values: {"INFRAHUB_SYNC_CACHE_DIR": str(tmp_path / "sync-cache")},
    )
    monkeypatch.setattr(preview, "_compose", record_compose)

    with pytest.raises(StopAfterComposeError):
        cast("Task", preview.up).body(context)

    assert compose_calls == ["up --detach --wait --wait-timeout 420 --quiet-pull"]


def test_bringing_the_preview_up_writes_nothing_to_infrahub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`up` starts the stack and nothing else; `seed` and `smoke` own every write.

    The daily bring-up must leave the disposable Infrahub exactly as it found it, so the
    commands `up` issues are pinned in full: no schema load, no seeded branch or device,
    and no smoke suite -- the one path that admits a Sync run.
    """
    started: list[str] = []
    commands: list[str] = []
    seeded: list[dict[str, str]] = []
    context = Context()

    monkeypatch.setattr(preview, "STATE_DIR", tmp_path / ".preview")
    monkeypatch.setattr(preview, "load_preview_env", lambda: UP_VALUES)
    monkeypatch.setattr(
        preview,
        "_runtime_env",
        lambda _values: {
            "INFRAHUB_ADDRESS": "http://localhost:8080",
            "INFRAHUB_API_TOKEN": "local-token",
            "PREFECT_API_URL": "http://localhost:4210/api",
            "INFRAHUB_SYNC_CONFIG_DIRECTORY": str(tmp_path / "examples"),
            "INFRAHUB_SYNC_CACHE_DIR": str(tmp_path / "sync-cache"),
        },
    )
    monkeypatch.setattr(preview, "_compose", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "_wait_for_http", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "assert_no_legacy_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preview, "_start_process", lambda name, _argv, _env, **_kwargs: started.append(name))
    monkeypatch.setattr(preview, "_project_interpreter", lambda _context: "/preview/venv/bin/python")
    monkeypatch.setattr(preview, "ensure_smoke_branch", seeded.append)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda command, **_kwargs: commands.append(command))

    cast("Task", preview.up).body(context)

    assert started == ["prefect-worker", "sync-api"]
    assert commands == [
        "uv run prefect work-pool create preview-pool --type process",
        "uv run python -m infrahub_sync.service.deploy",
    ]
    assert seeded == []


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


class _SmokeUpsert:
    """Stand-in for the node `create` returns; only `save` is called on it."""

    def __init__(self, events: list[tuple[object, ...]]) -> None:
        self._events = events

    def save(self, *, allow_upsert: bool) -> None:
        self._events.append(("save", allow_upsert))


class _SmokeBranchManager:
    def __init__(self, events: list[tuple[object, ...]], branches: dict[str, object]) -> None:
        self._events = events
        self.branches = dict(branches)

    def all(self) -> dict[str, object]:
        return self.branches

    def create(self, branch_name: str, *, sync_with_git: bool) -> None:
        self._events.append(("branch", branch_name, sync_with_git))
        self.branches[branch_name] = object()


class _SmokeSetupClient:
    """Record what `ensure_smoke_branch` does, in order, against a fake Infrahub.

    Order is the property under test, not merely which calls happen, so every write is
    appended to one shared list rather than to per-call counters.
    """

    def __init__(self, branches: dict[str, object] | None = None) -> None:
        self.events: list[tuple[object, ...]] = []
        self.branch = _SmokeBranchManager(self.events, branches or {})

    def create(self, *, kind: str, branch: str, data: dict[str, str]) -> _SmokeUpsert:
        self.events.append(("create", kind, branch, dict(data)))
        return _SmokeUpsert(self.events)


def test_the_shared_device_is_seeded_on_main_before_the_smoke_branch_forks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A branch inherits what `main` held when it forked, so the seed has to come first.

    Seeded afterwards the device would exist on `main` alone, and the registered smoke
    would plan a create. Seeded before, both branches carry it and that smoke has a real
    update to plan and apply.
    """
    client = _SmokeSetupClient()
    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", lambda **_kwargs: client)

    ensure_smoke_branch(SMOKE_ENVIRONMENT)

    assert client.events == [
        ("create", SMOKE_KIND, "main", {"name": SHARED_DEVICE_NAME, "type": SHARED_DEVICE_SEED_TYPE}),
        ("save", True),
        ("branch", SMOKE_BRANCH, False),
    ]


def test_smoke_branch_setup_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second bring-up neither reforks the branch nor reseeds `main`."""
    client = _SmokeSetupClient()
    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", lambda **_kwargs: client)

    ensure_smoke_branch(SMOKE_ENVIRONMENT)
    after_first_pass = list(client.events)
    ensure_smoke_branch(SMOKE_ENVIRONMENT)

    assert client.events == after_first_pass


def test_an_existing_smoke_branch_is_left_entirely_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeding after the fork cannot reach the branch, so it is not attempted at all.

    Re-forking would be worse than useless: it would discard the destination both smokes
    have been writing to.
    """
    client = _SmokeSetupClient({SMOKE_BRANCH: object()})
    monkeypatch.setattr(infrahub_sdk, "InfrahubClientSync", lambda **_kwargs: client)

    ensure_smoke_branch(SMOKE_ENVIRONMENT)

    assert not client.events, client.events


def test_the_seeded_device_is_one_the_cli_smoke_source_already_owns() -> None:
    """The seed must not leave the CLI-cycle smoke a delete it can never converge.

    That smoke plans `custom-example`'s source against this same branch and asserts the
    re-plan holds zero operations. A device on the branch its source does not own is a
    recorded, never-executed delete — permanently non-zero. Reusing a name the source
    already owns keeps the two smokes independent of each other in both orders.
    """
    devices = json.loads(CLI_SMOKE_SOURCE.read_text(encoding="utf-8"))["nodes"]["devices"]

    assert SHARED_DEVICE_NAME in {device["name"] for device in devices}


def test_the_smoke_task_seeds_before_it_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite writes, so `smoke` puts the dataset in place before running it.

    Order is the property: seeded afterwards, the suite would plan against an Infrahub
    that holds neither the schema nor the shared device it asserts on.
    """
    events: list[object] = []
    context = Context()
    values = {"COMPOSE_PROJECT_NAME": "preview-test"}
    environment = {"INFRAHUB_ADDRESS": "http://localhost:8080", "INFRAHUB_API_TOKEN": "local-token"}

    monkeypatch.setattr(preview, "load_preview_env", lambda: values)
    monkeypatch.setattr(preview, "_runtime_env", lambda _values: environment)
    monkeypatch.setattr(preview, "_infrahub_reachable", lambda _address: True)
    monkeypatch.setattr(preview, "ensure_smoke_branch", lambda env: events.append(("branch", env)))
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(
        context,
        "run",
        lambda command, *, env: events.append(("run", command, env)),
    )

    cast("Task", preview.smoke).body(context)

    assert events == [
        (
            "run",
            f"uv run infrahubctl schema load --wait {preview.SCHEMA_CONVERGE_SECONDS} {preview.SCHEMA_FILE}",
            environment,
        ),
        ("branch", environment),
        ("run", "uv run pytest -m preview tests/preview -q", environment),
    ]


def test_the_seed_task_loads_the_schema_before_it_creates_the_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ensure_smoke_branch` creates a node of the kind the schema defines, so it comes second."""
    events: list[object] = []
    context = Context()
    values = {"COMPOSE_PROJECT_NAME": "preview-test"}
    environment = {"INFRAHUB_ADDRESS": "http://localhost:8080", "INFRAHUB_API_TOKEN": "local-token"}

    monkeypatch.setattr(preview, "load_preview_env", lambda: values)
    monkeypatch.setattr(preview, "_runtime_env", lambda _values: environment)
    monkeypatch.setattr(preview, "ensure_smoke_branch", lambda env: events.append(("branch", env)))
    monkeypatch.setattr(
        context,
        "run",
        lambda command, *, env: events.append(("run", command, env)),
    )

    cast("Task", preview.seed).body(context)

    assert events == [
        (
            "run",
            f"uv run infrahubctl schema load --wait {preview.SCHEMA_CONVERGE_SECONDS} {preview.SCHEMA_FILE}",
            environment,
        ),
        ("branch", environment),
    ]


def test_the_smoke_task_does_not_run_the_suite_when_the_seed_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A seed that fails against a reachable Infrahub must stop the smoke, not be absorbed.

    A rejected schema, an edit the destination refuses, or a wrong credential override
    all fail here. Running the suite anyway would check the state the seed failed to
    replace, and that state can still satisfy every assertion.
    """
    commands: list[str] = []
    context = Context()
    values = {"COMPOSE_PROJECT_NAME": "preview-test"}
    environment = {"INFRAHUB_ADDRESS": "http://localhost:8080", "INFRAHUB_API_TOKEN": "local-token"}

    def _refuse_the_schema_load(command: str, *, env: dict[str, str]) -> None:
        del env
        commands.append(command)
        if "schema load" in command:
            raise UnexpectedExit(Result(exited=1))

    monkeypatch.setattr(preview, "load_preview_env", lambda: values)
    monkeypatch.setattr(preview, "_runtime_env", lambda _values: environment)
    monkeypatch.setattr(preview, "_infrahub_reachable", lambda _address: True)
    monkeypatch.setattr(preview, "ensure_smoke_branch", lambda _env: commands.append("branch"))
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", _refuse_the_schema_load)

    with pytest.raises(UnexpectedExit):
        cast("Task", preview.smoke).body(context)

    assert [command for command in commands if "pytest" in command] == [], commands


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
    monkeypatch.setattr(preview, "_infrahub_reachable", lambda _address: True)
    monkeypatch.setattr(preview, "ensure_smoke_branch", lambda _env: None)
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(context, "run", lambda command, *, env: events.append((command, env.copy())))

    cast("Task", preview.smoke).body(context)

    smoke_environments = [env for command, env in events if "pytest" in command]
    assert len(smoke_environments) == 1
    assert smoke_environments[0]["AWS_ACCESS_KEY_ID"] == "preview-minio-access"
    assert smoke_environments[0]["AWS_SECRET_ACCESS_KEY"] == PREVIEW_MINIO_SECRET


def test_the_smoke_task_leaves_an_unreachable_environment_to_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stopped stack is not seeded at all; the suite reports the unreachable endpoint."""
    events: list[object] = []
    context = Context()
    values = {"COMPOSE_PROJECT_NAME": "preview-test"}
    environment = {"INFRAHUB_ADDRESS": "http://localhost:8080", "INFRAHUB_API_TOKEN": "local-token"}

    monkeypatch.setattr(preview, "load_preview_env", lambda: values)
    monkeypatch.setattr(preview, "_runtime_env", lambda _values: environment)
    monkeypatch.setattr(preview, "_infrahub_reachable", lambda _address: False)
    monkeypatch.setattr(preview, "ensure_smoke_branch", lambda env: events.append(("branch", env)))
    monkeypatch.setattr(context, "cd", lambda _path: nullcontext())
    monkeypatch.setattr(
        context,
        "run",
        lambda command, *, env: events.append(("run", command, env)),
    )

    cast("Task", preview.smoke).body(context)

    assert events == [("run", "uv run pytest -m preview tests/preview -q", environment)]


def test_netbox_tutorial_uses_one_checkout_for_code_and_configuration() -> None:
    tutorial = (REPO_ROOT / "docs/docs/tutorials/netbox-demo-to-infrahub.mdx").read_text(encoding="utf-8")

    assert "git clone https://github.com/opsmill/infrahub-sync.git ../infrahub-sync" in tutorial
    assert 'uv add --editable "../infrahub-sync[service]"' in tutorial
    assert "cp ../infrahub-sync/examples/netbox_to_infrahub/config.yml" in tutorial
    assert "v3-preview.1" not in tutorial
