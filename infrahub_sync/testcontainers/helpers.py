"""`TestInfrahubSyncDocker`: start Infrahub, then Sync from the packaged root Compose file.

The class extends `infrahub_testcontainers.helpers.TestInfrahubDocker`, so every Infrahub
fixture stays available unchanged. Infrahub starts first, from `infrahub-testcontainers`.
Sync then starts as a second Compose project from `docker-compose.yml` in this package, a
byte-equal copy of the repository's root file, plus each file that
`infrahub_sync_compose_files` returns. `sync-worker` joins the Infrahub project's default
network, so a run reaches `infrahub-server` by its service name.

Each variable the root file reads is written to the Sync project's `.env` as
`os.environ.get(name, test_default)`, the way `InfrahubDockerCompose.create_env_file` does:
a test or CI job sets a value by exporting it before the class starts. A variable with no
test default and no exported value is left out, so the root file's own default applies.

`InfrahubDockerCompose` does not pass its variables to the `docker compose` command, so
Compose reads them from `.env` only. Each value is written single-quoted, the `.env` form
Compose takes literally: a `$` in an exported password or token is not interpolated.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess  # noqa: S404 -- only `docker compose` runs, through testcontainers
import uuid
import warnings
from itertools import starmap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from infrahub_testcontainers.container import PROJECT_ENV_VARIABLES, InfrahubDockerCompose
from infrahub_testcontainers.helpers import TestInfrahubDocker
from testcontainers.core.exceptions import ContainerIsNotRunning

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence

PACKAGED_COMPOSE_FILE = Path(__file__).resolve().parent / "docker-compose.yml"
DEFAULT_IMAGE_REPOSITORY = "registry.opsmill.io/opsmill/infrahub-sync"
IMAGE_REPOSITORY_VARIABLE = "INFRAHUB_SYNC_DOCKER_IMAGE"
IMAGE_TAG_VARIABLE = "VERSION"

SYNC_API_SERVICE = "sync-api"
SYNC_API_CONTAINER_PORT = 8000
SYNC_WORKER_SERVICE = "sync-worker"
# The services whose logs a failed class reports.
LOGGED_SERVICES = ("sync-bootstrap", "sync-api", "sync-worker", "prefect-server")

# The API principal of the test deployment. `INFRAHUB_SYNC_API_TOKEN` is what the root
# file's `cli` service presents, so the two defaults name the same principal.
TEST_API_TOKEN = "infrahub-sync-test-token-0123456789"

# Test-only values for the variables the root file requires, and the ports and tokens a
# test deployment needs. None of them is a real credential. The passwords are URL-safe
# because the root file builds connection strings from them. Port `0` asks Docker for a
# free loopback port; `sync_api_url` reads the port Docker chose.
TEST_DEFAULTS: dict[str, str] = {
    "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD": "test-administrator-password",
    "INFRAHUB_SYNC_PRODUCT_PASSWORD": "test-product-password",
    "INFRAHUB_SYNC_PREFECT_PASSWORD": "test-prefect-password",
    "INFRAHUB_SYNC_PREFECT_AUTH_STRING": "test-prefect-auth-string",
    "INFRAHUB_SYNC_S3_ACCESS_KEY": "test-access-key",
    "INFRAHUB_SYNC_S3_SECRET_KEY": "test-secret-key",
    "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS": json.dumps(
        {"test": {"token": TEST_API_TOKEN, "administrator": True}}, separators=(",", ":")
    ),
    "INFRAHUB_SYNC_API_TOKEN": TEST_API_TOKEN,
    # The Infrahub test deployment's administrator token, so a run can write to it. An
    # exported `INFRAHUB_TESTING_INITIAL_ADMIN_TOKEN` replaces it in `environment_values`.
    "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN": PROJECT_ENV_VARIABLES["INFRAHUB_TESTING_INITIAL_ADMIN_TOKEN"],
    "INFRAHUB_SYNC_API_PORT": "0",
}
# The variable `InfrahubDockerCompose.create_env_file` reads the Infrahub administrator token
# from, and the Sync variable that carries the same token to the worker.
INFRAHUB_ADMIN_TOKEN_VARIABLE = "INFRAHUB_TESTING_INITIAL_ADMIN_TOKEN"
INFRAHUB_CREDENTIAL_VARIABLE = "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"

# `${NAME`, but not the escaped `$${NAME` Compose passes through to a container.
_VARIABLE_REFERENCE = re.compile(r"(?<!\$)\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)")


def compose_variables(compose_file: Path = PACKAGED_COMPOSE_FILE) -> list[str]:
    """Return the names of the variables `compose_file` reads, in first-use order."""
    names = (match["name"] for match in _VARIABLE_REFERENCE.finditer(compose_file.read_text(encoding="utf-8")))
    return list(dict.fromkeys(names))


def default_image() -> str:
    """Return the Sync image reference, from the environment or the installed package version.

    `INFRAHUB_SYNC_DOCKER_IMAGE` replaces the repository and `VERSION` replaces the tag; they
    are the variables the root file reads, so CI sets both to the image it built.
    """
    repository = os.environ.get(IMAGE_REPOSITORY_VARIABLE) or DEFAULT_IMAGE_REPOSITORY
    tag = os.environ.get(IMAGE_TAG_VARIABLE) or importlib.metadata.version("infrahub-sync")
    return f"{repository}:{tag}"


def split_image(image: str) -> tuple[str, str]:
    """Split `repository:tag` into its repository and tag.

    Raises:
        ValueError: when `image` carries no tag, or names a digest, which the root file's
            `${INFRAHUB_SYNC_DOCKER_IMAGE}:${VERSION}` form cannot express.
    """
    repository, separator, tag = image.rpartition(":")
    if not separator or not repository or not tag or "/" in tag or "@" in image:
        msg = f"'{image}' is not a 'repository:tag' image reference"
        raise ValueError(msg)
    return repository, tag


def environment_values(
    image: str,
    variables: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the `.env` values for `variables`: the environment's value, else the test default.

    The image variables come from `image`, the `infrahub_sync_image` fixture's value, which
    already honours the environment. A variable with a test default gets that default when it
    is unset or exported empty, as the image variables do. A variable without a test default
    keeps an exported empty value, which can be intentional, and is left out when it is
    unset, so the root file's own default applies.

    Raises:
        ValueError: when a value holds a line break, or a backslash that ends it or comes
            before a single quote, which a single-quoted `.env` value cannot carry.
    """
    environ = os.environ if environ is None else environ
    repository, tag = split_image(image)
    # The Infrahub project takes its administrator token from the same environment, so the
    # worker's credential follows an exported token instead of keeping the package default.
    infrahub_token = environ.get(INFRAHUB_ADMIN_TOKEN_VARIABLE) or TEST_DEFAULTS[INFRAHUB_CREDENTIAL_VARIABLE]
    defaults = {
        **TEST_DEFAULTS,
        INFRAHUB_CREDENTIAL_VARIABLE: infrahub_token,
        IMAGE_REPOSITORY_VARIABLE: repository,
        IMAGE_TAG_VARIABLE: tag,
    }
    values: dict[str, str] = {}
    for name in variables:
        if name in {IMAGE_REPOSITORY_VARIABLE, IMAGE_TAG_VARIABLE}:
            value: str | None = defaults[name]
        else:
            value = environ.get(name)
            if not value:
                value = defaults.get(name, value)
        if value is None:
            continue
        if "\n" in value or "\r" in value:
            msg = f"the value of {name} holds a line break, which a .env file cannot carry"
            raise ValueError(msg)
        if value.endswith("\\") or "\\'" in value:
            msg = (
                f"the value of {name} ends with a backslash or holds a backslash before a single quote,"
                " which a single-quoted .env value cannot carry"
            )
            raise ValueError(msg)
        values[name] = value
    return values


def env_file_line(name: str, value: str) -> str:
    """Return the `.env` line that sets `name` to `value`, which Compose reads unchanged.

    The value is single-quoted, so Compose does not interpolate `$NAME`, `${NAME}` or `$$`
    and keeps spaces and `#`. Inside single quotes Compose reads `\\'` as `'` and every
    other backslash as itself; `environment_values` refuses the values this cannot carry.
    """
    escaped = value.replace("'", "\\'")
    return f"{name}='{escaped}'\n"


class InfrahubSyncDockerCompose(InfrahubDockerCompose):
    """The Sync Compose project: the packaged root file plus the extra files, in order."""

    @classmethod
    def generate_project_name(cls) -> str:
        """Return a unique project name, so parallel runs never share a container or volume."""
        return f"infrahub-sync-test-{uuid.uuid4().hex[:8]}"

    @classmethod
    def create(
        cls,
        directory: Path,
        image: str,
        extra_files: Sequence[Path] = (),
        environ: Mapping[str, str] | None = None,
    ) -> InfrahubSyncDockerCompose:
        """Copy the packaged root file into `directory`, write its `.env`, and return the project.

        `directory` must not be the Infrahub project's directory: both projects name their
        files `docker-compose.yml` and `.env`. Compose resolves a relative path inside any
        file against `directory`, the first file's location.
        """
        directory.mkdir(parents=True, exist_ok=True)
        compose_file = directory / "docker-compose.yml"
        shutil.copyfile(PACKAGED_COMPOSE_FILE, compose_file)

        values = environment_values(image, compose_variables(compose_file), environ)
        env_file = directory / ".env"
        env_file.write_text("".join(starmap(env_file_line, values.items())), encoding="utf-8")

        compose = cls(
            context=directory,
            compose_file_name=[str(compose_file), *(str(Path(path).resolve()) for path in extra_files)],
            env_file=str(env_file),
            project_name=cls.generate_project_name(),
        )
        compose.env_vars.update(values)
        return compose

    def connect_to_network(self, service: str, network: str) -> None:
        """Attach the running container of `service` to `network`."""
        container = self.get_container(service)
        self._run_command(cmd=[self.docker_command_path or "docker", "network", "connect", network, str(container.ID)])


class TestInfrahubSyncDocker(TestInfrahubDocker):
    """Start Infrahub, then Sync from the packaged root Compose file, once per test class."""

    @pytest.fixture(scope="class")
    @classmethod
    def infrahub_sync_image(cls) -> str:
        """The image every Sync-image service runs: `INFRAHUB_SYNC_DOCKER_IMAGE:VERSION`.

        Defaults to `registry.opsmill.io/opsmill/infrahub-sync` and the installed
        `infrahub-sync` version.
        """
        return default_image()

    @pytest.fixture(scope="class")
    @classmethod
    def infrahub_sync_compose_files(cls) -> list[Path]:
        """Extra Compose files for the Sync project, passed with `-f` after the packaged root file.

        This is the hook for an override file: a subclass overrides this fixture and returns
        the files to add, in order. The default adds none.
        """
        return []

    @pytest.fixture(scope="class")
    @classmethod
    def infrahub_sync_compose(
        cls,
        tmp_directory: Path,
        infrahub_sync_image: str,
        infrahub_sync_compose_files: list[Path],
    ) -> InfrahubSyncDockerCompose:
        """The Sync Compose project, written to disk but not started."""
        return InfrahubSyncDockerCompose.create(
            directory=tmp_directory / "infrahub-sync",
            image=infrahub_sync_image,
            extra_files=infrahub_sync_compose_files,
        )

    @pytest.fixture(scope="class")
    @classmethod
    def infrahub_sync_app(
        cls,
        request: pytest.FixtureRequest,
        infrahub_app: dict[str, int],  # noqa: ARG003  # pylint: disable=unused-argument  # Infrahub starts first
        infrahub_compose: InfrahubDockerCompose,
        infrahub_sync_compose: InfrahubSyncDockerCompose,
    ) -> Generator[InfrahubSyncDockerCompose, None, None]:
        """Start the Sync project after Infrahub, and stop it with its volumes afterwards.

        `sync-worker` joins the Infrahub project's default network. When a test of the class
        failed, the Sync services' logs are reported as a warning, as the parent class does
        for Infrahub.
        """
        tests_failed_before_class = request.session.testsfailed
        try:
            infrahub_sync_compose.start()
            infrahub_sync_compose.connect_to_network(SYNC_WORKER_SERVICE, f"{infrahub_compose.project_name}_default")
        except (subprocess.CalledProcessError, ContainerIsNotRunning) as exc:
            # Unlike the parent class, stop the project here: a class whose setup failed
            # never reaches the teardown below, and its containers would stay behind.
            # `get_logs` raises when `docker compose logs` fails, so the stop is in `finally`.
            try:
                stdout, stderr = infrahub_sync_compose.get_logs()
            finally:
                infrahub_sync_compose.stop()
            msg = f"Failed to start the Sync Compose project:\nStdout:\n{stdout}\nStderr:\n{stderr}"
            raise RuntimeError(msg) from exc

        yield infrahub_sync_compose

        try:
            if request.session.testsfailed - tests_failed_before_class > 0:
                stdout, stderr = infrahub_sync_compose.get_logs(*LOGGED_SERVICES)
                warnings.warn(f"Sync container logs:\nStdout:\n{stdout}\nStderr:\n{stderr}", stacklevel=2)
        finally:
            infrahub_sync_compose.stop()

    @pytest.fixture(scope="class")
    @classmethod
    def sync_api_url(cls, infrahub_sync_app: InfrahubSyncDockerCompose) -> str:
        """The Sync API's URL on the loopback address, at the port Docker published it on."""
        port = infrahub_sync_app.get_service_port(service_name=SYNC_API_SERVICE, port=SYNC_API_CONTAINER_PORT)
        return f"http://127.0.0.1:{port}"

    @pytest.fixture(scope="class")
    @classmethod
    def sync_api_token(cls, infrahub_sync_compose: InfrahubSyncDockerCompose) -> str:
        """The API token the test deployment's `cli` service presents (`INFRAHUB_SYNC_API_TOKEN`)."""
        return infrahub_sync_compose.get_env_var("INFRAHUB_SYNC_API_TOKEN")
