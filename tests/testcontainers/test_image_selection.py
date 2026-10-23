"""`TestInfrahubSyncDocker` picks its image, writes its `.env` and orders its Compose files.

Nothing here starts a container: the Compose project is only written to disk and its
command line read back. The module skips where the `testcontainers` extra is not
installed (Python 3.10 and the base install).
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shutil
import subprocess  # noqa: S404 -- runs `docker compose config`, which needs no daemon
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("infrahub_testcontainers")

from infrahub_sync.testcontainers import TestInfrahubSyncDocker
from infrahub_sync.testcontainers.helpers import (
    PACKAGED_COMPOSE_FILE,
    TEST_DEFAULTS,
    InfrahubSyncDockerCompose,
    compose_variables,
    default_image,
    env_file_line,
    split_image,
)

INSTALLED_VERSION = importlib.metadata.version("infrahub-sync")
EXTRA_FILE = Path(__file__).resolve().parent / "extra.override.yml"
# A variable the root file requires with `${NAME:?message}`.
REQUIRED_VARIABLE = re.compile(r"\$\{(?P<name>[A-Z_][A-Z0-9_]*):\?")


# A value Compose would change if `.env` held it unquoted: `$NAME`, `$$` and `${NAME}` are
# interpolated, and the space and `#` end an unquoted value. Not a real credential.
LITERAL_VALUE = "dummy-$token $$double ${NAME} it's #kept"
# A variable the root file passes to `sync-worker` and that no test default sets.
LITERAL_VARIABLE = "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN"


def _env_file(directory: Path) -> dict[str, str]:
    """Read the generated `.env` back, undoing the single quotes `env_file_line` adds."""
    lines = (directory / ".env").read_text(encoding="utf-8").splitlines()
    pairs = (line.split("=", 1) for line in lines)
    return {name: quoted.removeprefix("'").removesuffix("'").replace("\\'", "'") for name, quoted in pairs}


def _resolved_services(compose: InfrahubSyncDockerCompose) -> dict[str, Any]:
    """Return the services `docker compose config` resolves, run with no exported variable.

    Only what the CLI needs reaches it, so a value exported in the caller's shell cannot
    reach Compose and the project resolves from its `.env` alone.
    """
    if shutil.which("docker") is None:
        pytest.skip("the docker CLI is not installed; resolving a Compose file needs the CLI, not a daemon")
    environment = {name: os.environ[name] for name in ("PATH", "HOME", "DOCKER_CONFIG") if name in os.environ}
    result = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        [*compose.compose_command_property, "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=60,
    )
    assert result.returncode == 0, "docker compose config refused the generated project"
    return json.loads(result.stdout)["services"]


def _compose_files(compose: InfrahubSyncDockerCompose) -> list[str]:
    command = compose.compose_command_property
    return [command[index + 1] for index, part in enumerate(command) if part == "-f"]


@pytest.fixture(name="clean_image_environment")
def _clean_image_environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.delenv("INFRAHUB_SYNC_DOCKER_IMAGE", raising=False)
    monkeypatch.delenv("VERSION", raising=False)
    return monkeypatch


def test_the_default_image_is_the_registry_image_at_the_installed_version(
    clean_image_environment: pytest.MonkeyPatch,  # noqa: ARG001
) -> None:
    assert default_image() == f"registry.opsmill.io/opsmill/infrahub-sync:{INSTALLED_VERSION}"


def test_infrahub_sync_docker_image_replaces_the_repository(clean_image_environment: pytest.MonkeyPatch) -> None:
    clean_image_environment.setenv("INFRAHUB_SYNC_DOCKER_IMAGE", "infrahub-sync")

    assert default_image() == f"infrahub-sync:{INSTALLED_VERSION}"


def test_version_replaces_the_tag(clean_image_environment: pytest.MonkeyPatch) -> None:
    clean_image_environment.setenv("VERSION", "s010-ci")

    assert default_image() == "registry.opsmill.io/opsmill/infrahub-sync:s010-ci"


@pytest.mark.parametrize(
    "image", ["infrahub-sync", "localhost:5000/infrahub-sync", "infrahub-sync@sha256:0123", "infrahub-sync:"]
)
def test_an_image_without_a_plain_tag_is_refused(image: str) -> None:
    with pytest.raises(ValueError, match="repository:tag"):
        split_image(image)


def test_the_image_reaches_the_env_file(tmp_path: Path) -> None:
    InfrahubSyncDockerCompose.create(directory=tmp_path, image="localhost:5000/infrahub-sync:s010-ci", environ={})

    env = _env_file(tmp_path)
    assert env["INFRAHUB_SYNC_DOCKER_IMAGE"] == "localhost:5000/infrahub-sync"
    assert env["VERSION"] == "s010-ci"


def test_an_exported_variable_reaches_the_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_API_PORT", "18123")
    monkeypatch.setenv("INFRAHUB_SYNC_S3_BUCKET", "s010-bucket")

    compose = InfrahubSyncDockerCompose.create(directory=tmp_path, image="infrahub-sync:s010-ci")

    env = _env_file(tmp_path)
    assert env["INFRAHUB_SYNC_API_PORT"] == "18123"
    assert env["INFRAHUB_SYNC_S3_BUCKET"] == "s010-bucket"
    assert compose.get_env_var("INFRAHUB_SYNC_API_PORT") == "18123"


def test_an_unset_variable_gets_its_test_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INFRAHUB_SYNC_API_PORT", raising=False)
    monkeypatch.delenv("INFRAHUB_SYNC_S3_BUCKET", raising=False)

    InfrahubSyncDockerCompose.create(directory=tmp_path, image="infrahub-sync:s010-ci")

    env = _env_file(tmp_path)
    assert env["INFRAHUB_SYNC_API_PORT"] == TEST_DEFAULTS["INFRAHUB_SYNC_API_PORT"] == "0"
    # No test default: the root file's own `${INFRAHUB_SYNC_S3_BUCKET:-infrahub-sync}` applies.
    assert "INFRAHUB_SYNC_S3_BUCKET" not in env


def test_every_required_variable_of_the_root_file_has_a_test_default() -> None:
    required = set(REQUIRED_VARIABLE.findall(PACKAGED_COMPOSE_FILE.read_text(encoding="utf-8")))

    assert required, "the root file requires no variable; the pattern no longer matches it"
    assert required <= set(TEST_DEFAULTS)
    assert required <= set(compose_variables())


def test_a_value_with_a_line_break_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_S3_BUCKET", "first\nsecond")

    with pytest.raises(ValueError, match="INFRAHUB_SYNC_S3_BUCKET"):
        InfrahubSyncDockerCompose.create(directory=tmp_path, image="infrahub-sync:s010-ci")


def test_the_project_has_its_own_name_and_env_file(tmp_path: Path) -> None:
    compose = InfrahubSyncDockerCompose.create(directory=tmp_path, image="infrahub-sync:s010-ci", environ={})
    command = compose.compose_command_property

    assert command[command.index("--project-name") + 1].startswith("infrahub-sync-test-")
    assert command[command.index("--env-file") + 1] == str(tmp_path / ".env")
    assert (tmp_path / "docker-compose.yml").read_bytes() == PACKAGED_COMPOSE_FILE.read_bytes()


def test_compose_resolves_the_project_with_the_test_defaults(tmp_path: Path) -> None:
    """Every required variable is set, so Compose accepts the project before any container starts."""
    compose = InfrahubSyncDockerCompose.create(directory=tmp_path, image="infrahub-sync:s010-ci", environ={})

    services = _resolved_services(compose)

    assert services["sync-worker"]["image"] == "infrahub-sync:s010-ci"
    (published,) = services["sync-api"]["ports"]
    assert published["host_ip"] == "127.0.0.1"
    assert str(published.get("published", "")) in {"", "0"}


def test_compose_takes_an_exported_value_literally(tmp_path: Path) -> None:
    compose = InfrahubSyncDockerCompose.create(
        directory=tmp_path, image="infrahub-sync:s010-ci", environ={LITERAL_VARIABLE: LITERAL_VALUE}
    )

    services = _resolved_services(compose)

    # `docker compose config` writes each `$` of a resolved value as `$$`, so its output
    # stays valid Compose; doubling the expected value compares the value Compose resolved.
    assert services["sync-worker"]["environment"][LITERAL_VARIABLE] == LITERAL_VALUE.replace("$", "$$")


@pytest.mark.parametrize(
    ("value", "line"),
    [
        ("plain", "NAME='plain'\n"),
        ("a $b ${C} $$d", "NAME='a $b ${C} $$d'\n"),
        ("it's", "NAME='it\\'s'\n"),
        ("back\\slash", "NAME='back\\slash'\n"),
    ],
)
def test_a_value_is_written_single_quoted(value: str, line: str) -> None:
    assert env_file_line("NAME", value) == line


@pytest.mark.parametrize("value", ["ends-with\\", "backslash\\'quote"])
def test_a_value_single_quotes_cannot_carry_is_refused(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_S3_BUCKET", value)

    with pytest.raises(ValueError, match="INFRAHUB_SYNC_S3_BUCKET") as refused:
        InfrahubSyncDockerCompose.create(directory=tmp_path, image="infrahub-sync:s010-ci")

    assert value not in str(refused.value)


class TestTheDefaultFileList(TestInfrahubSyncDocker):
    def test_only_the_packaged_root_file_is_passed(  # noqa: PLR6301 -- pytest calls test methods on an instance
        self, infrahub_sync_compose_files: list[Path], infrahub_sync_compose: InfrahubSyncDockerCompose
    ) -> None:
        assert infrahub_sync_compose_files == []
        files = _compose_files(infrahub_sync_compose)
        assert len(files) == 1
        assert Path(files[0]).name == "docker-compose.yml"
        assert Path(files[0]).read_bytes() == PACKAGED_COMPOSE_FILE.read_bytes()


class TestAnExtraComposeFile(TestInfrahubSyncDocker):
    @pytest.fixture(scope="class")
    @classmethod
    def infrahub_sync_compose_files(cls) -> list[Path]:
        return [EXTRA_FILE]

    def test_the_extra_file_follows_the_packaged_root_file(  # noqa: PLR6301 -- pytest calls test methods on an instance
        self, infrahub_sync_compose: InfrahubSyncDockerCompose
    ) -> None:
        files = _compose_files(infrahub_sync_compose)

        assert len(files) == 2
        assert Path(files[0]).read_bytes() == PACKAGED_COMPOSE_FILE.read_bytes()
        assert files[1] == str(EXTRA_FILE)
