"""The service deployment names an installed module, not a file in a checkout.

A worker resolves this deployment by importing the installed distribution. Nothing here
may depend on a repository being present on the worker host, on the worker's current
working directory, or on a Prefect step that puts source there.
"""

from __future__ import annotations

import os
import sys
from asyncio import sleep
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from prefect.flows import load_flow_from_entrypoint
from prefect.utilities import importtools
from prefect.workers.process import ProcessWorker

from infrahub_sync.service import deploy
from infrahub_sync.service import flow as installed_flow
from infrahub_sync.service.orchestration import SERVICE_DEFINITION

INSTALLED_FLOW_FILE = Path(installed_flow.__file__ or "").resolve()

if TYPE_CHECKING:
    from collections.abc import Iterator

    from prefect.client.schemas.actions import DeploymentUpdate
    from typing_extensions import Self

INSTALLED_IDENTITY = "infrahub_sync.service.flow.service_sync_run"


@pytest.fixture
def _no_script_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if Prefect resolves this deployment by reading a source file.

    Prefect routes an entrypoint containing ``.py:`` to ``load_script_as_module``, which
    executes a file at a recorded absolute path. That path is this installation's, so a
    worker that does not share this filesystem has nothing to load.
    """

    def refuse(path: str) -> None:
        msg = f"the service entrypoint must not be resolved by loading the file {path!r}"
        raise AssertionError(msg)

    monkeypatch.setattr(importtools, "load_script_as_module", refuse)


def _write_shadow_package(root: Path) -> None:
    """Write a package that would answer to ``infrahub_sync`` if cwd won resolution."""
    service = root / "infrahub_sync" / "service"
    service.mkdir(parents=True)
    (root / "infrahub_sync" / "__init__.py").write_text("SHADOW = True\n", encoding="utf-8")
    (service / "__init__.py").write_text("SHADOW = True\n", encoding="utf-8")
    (service / "flow.py").write_text(
        "SHADOW = True\n\n\ndef service_sync_run(*_args, **_kwargs):\n    return 'shadow'\n",
        encoding="utf-8",
    )


@pytest.fixture
def _shadow_cwd(tmp_path: Path) -> Iterator[Path]:
    """Run the body from a directory that holds a shadow ``infrahub_sync`` package."""
    _write_shadow_package(tmp_path)
    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(previous)


@pytest.mark.usefixtures("_no_script_loading")
def test_the_deployment_resolves_through_the_installed_module_path() -> None:
    """The recorded entrypoint imports the installed distribution by dotted identity."""
    entrypoint = SERVICE_DEFINITION.entrypoint
    assert entrypoint is not None
    assert entrypoint == INSTALLED_IDENTITY
    assert ":" not in entrypoint
    assert os.sep not in entrypoint
    assert not Path(entrypoint).exists()

    resolved = load_flow_from_entrypoint(entrypoint)

    assert resolved.name == SERVICE_DEFINITION.flow_name
    assert resolved.fn is installed_flow.service_sync_run.fn


@pytest.mark.usefixtures("_no_script_loading", "_shadow_cwd")
def test_a_cwd_shadow_package_does_not_win_the_deployments_resolution() -> None:
    """Resolution from a directory holding a shadow package still yields the installation.

    Prefect prepends the working directory to ``sys.path`` on both of its entrypoint
    branches, so what keeps the installed distribution authoritative is that the flow-run
    working directory is never a source tree. This asserts the outcome that guarantee
    exists for: the resolved flow is the installed one and carries none of the shadow.
    """
    entrypoint = SERVICE_DEFINITION.entrypoint
    assert entrypoint is not None

    resolved = load_flow_from_entrypoint(entrypoint)

    assert resolved.name == SERVICE_DEFINITION.flow_name
    assert resolved.fn is installed_flow.service_sync_run.fn
    module = sys.modules[SERVICE_DEFINITION.module]
    assert Path(module.__file__ or "").resolve() == INSTALLED_FLOW_FILE
    assert not hasattr(module, "SHADOW")
    assert Path.cwd() not in INSTALLED_FLOW_FILE.parents


def test_the_flow_run_working_directory_is_prefects_own_disposable_one() -> None:
    """No configured working directory means each flow run gets a fresh empty one.

    A process worker with no ``working_dir`` in its job configuration runs every flow run
    in a new temporary directory. That is what makes a repository-resident shadow package
    unreachable, so the default has to stay unset rather than be pointed at a checkout.
    """
    configuration = ProcessWorker.job_configuration()

    assert "working_dir" in type(configuration).model_fields
    assert configuration.working_dir is None


def test_the_service_declares_no_flow_working_directory_setting() -> None:
    """The retired working-directory environment name is gone from the deploy surface."""
    assert not hasattr(deploy, "FLOW_WORKING_DIRECTORY_ENV")
    assert not hasattr(deploy, "required_flow_working_directory")
    assert not hasattr(deploy, "flow_pull_steps")
    assert not any("FLOW_WORKING_DIRECTORY" in name for name in os.environ if name.startswith("INFRAHUB_SYNC"))


class _ExistingDeploymentClient:
    """A Prefect client holding one deployment that still carries a pull step."""

    def __init__(self, pull_steps: list[dict[str, Any]] | None) -> None:
        self.deployment_id = uuid4()
        self.pull_steps = pull_steps
        self.updates: list[DeploymentUpdate] = []

    async def read_deployment_by_name(self, name: str) -> Any:  # noqa: ANN401 - Prefect model stand-in.
        assert name == SERVICE_DEFINITION.key
        return SimpleNamespace(id=self.deployment_id, pull_steps=self.pull_steps)

    async def update_deployment(self, deployment_id: UUID, deployment: DeploymentUpdate) -> None:
        assert deployment_id == self.deployment_id
        self.updates.append(deployment)
        self.pull_steps = deployment.pull_steps

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.fixture
def offline_catalogue_apply(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the catalogue apply without contacting Prefect."""
    order: list[str] = []

    async def apply(*_args: object, **_kwargs: object) -> SimpleNamespace:
        order.append("catalogue")
        await sleep(0)
        return SimpleNamespace(is_successful=True)

    monkeypatch.setenv(deploy.WORK_POOL_ENV, "service-pool")
    monkeypatch.setattr(deploy, "apply_deployments", apply)
    return order


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param(
            [{"prefect.deployments.steps.set_working_directory": {"directory": "/srv/checkout"}}],
            id="working-directory-step",
        ),
        pytest.param(
            [{"prefect.deployments.steps.git_clone": {"repository": "https://example.test/x.git"}}],
            id="source-pull-step",
        ),
    ],
)
@pytest.mark.usefixtures("offline_catalogue_apply")
async def test_deploying_the_service_converges_existing_pull_steps_to_empty(
    monkeypatch: pytest.MonkeyPatch,
    existing: list[dict[str, Any]],
) -> None:
    """A deployed pull step is removed by applying, not merely left unspecified.

    Omitting ``pull_steps`` leaves whatever a previous apply installed, so a worker would
    keep resolving source before the entrypoint runs. Convergence states the empty value.
    """
    client = _ExistingDeploymentClient(existing)
    monkeypatch.setattr(deploy, "get_client", lambda: client)

    assert await deploy._deploy() == 0

    assert client.pull_steps == []
    assert [update.pull_steps for update in client.updates] == [[]]


@pytest.mark.usefixtures("offline_catalogue_apply")
async def test_deploying_the_service_leaves_already_empty_pull_steps_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convergence is idempotent: an empty deployment is not rewritten on every apply."""
    client = _ExistingDeploymentClient([])
    monkeypatch.setattr(deploy, "get_client", lambda: client)

    assert await deploy._deploy() == 0

    assert client.pull_steps == []
    assert client.updates == []


async def test_deploying_the_service_needs_no_working_directory_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
    offline_catalogue_apply: list[str],
) -> None:
    """The deploy path completes with no working-directory setting present at all."""
    client = _ExistingDeploymentClient(None)
    monkeypatch.setattr(deploy, "get_client", lambda: client)
    for name in list(os.environ):
        if name.startswith("INFRAHUB_SYNC_SERVICE_FLOW"):
            monkeypatch.delenv(name, raising=False)

    assert await deploy._deploy() == 0

    assert client.pull_steps == []
    assert offline_catalogue_apply == ["catalogue"]
