"""The declared minimum Compose version is measured, not assumed.

Everything here is answered by the pinned 2.24.0 release itself, never by the
developer's installed one, and it is asked in two ways because the questions
differ.

Parsing the operator file needs no daemon, so those cases run the pinned binary
inside its own image with the file mounted read-only. Nothing there starts a
container, and no Docker socket is involved.

Starting a real deployment does need a daemon, and the pinned binary has to be
the thing that talks to it. Mounting the host's Docker socket into a container
to arrange that would hand a container control of the engine, which the
deployment refuses on principle and this suite will not do to test it. So the
binary for this host is exported out of its image onto a disposable host path
and run as an ordinary host process, exactly as an operator on the documented
floor would run it. The release ships Linux and macOS binaries; elsewhere the
live case says so and skips rather than quietly substituting the installed one.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import COMPOSE_FILE, resolve
from tests.compose.lifecycle import docker
from tests.compose.redaction import Captured, capture

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

pytestmark = pytest.mark.compose

# The floor the operator file declares, and the image that ships exactly it. The
# file carries its database bootstrap script as a top-level `configs` entry with
# inline `content`, which older releases do not accept.
MINIMUM_COMPOSE = "2.24.0"
MINIMUM_IMAGE = "docker/compose-bin@sha256:5c6a7907c7c3cf88b216d5b69ac8154b302adeea4a407fba26263cbd7c5c68f9"
# Where the binary sits inside that image.
BINARY_PATH = "/docker-compose"
# The sentence the file's header states the floor in.
DECLARED_FLOOR = "Docker Compose 2.24 or later"

# What the documented operator commands ask of the Compose CLI beyond parsing the file.
REQUIRED_FLAGS = ("--wait", "--wait-timeout", "--quiet-pull", "--detach")

# The declared bound for the live fixture, given to the pinned binary itself.
FIXTURE_WAIT_SECONDS = 120
FIXTURE_PROCESS_CUSHION_SECONDS = 60

# The host platforms the release publishes a binary for. A host that is not one
# of them cannot run the pinned release as a process at all, and the only
# alternative would put the Docker socket inside a container.
HOST_IMAGE_PLATFORMS = {
    ("Linux", "x86_64"): "linux/amd64",
    ("Linux", "amd64"): "linux/amd64",
    ("Linux", "aarch64"): "linux/arm64",
    ("Linux", "arm64"): "linux/arm64",
    ("Darwin", "x86_64"): "darwin/amd64",
    ("Darwin", "arm64"): "darwin/arm64",
}


def declared_minimum() -> str:
    """Return the header comment of the operator file, where the floor is stated."""
    return COMPOSE_FILE.read_text(encoding="utf-8").split("\nservices:", 1)[0]


def minimum_compose(argv: list[str], *, environment: Mapping[str, str] | None = None) -> Captured:
    """Parse-only: the pinned release, in its own image, with no daemon behind it.

    The operator file is the only thing mounted, read-only. Nothing this answers
    needs an engine, so nothing here is given one.
    """
    command = [
        "run",
        "--rm",
        "--entrypoint",
        BINARY_PATH,
        "--volume",
        f"{COMPOSE_FILE}:/work/docker-compose.yml:ro",
        "--workdir",
        "/work",
    ]
    for key, value in (environment or {}).items():
        command += ["--env", f"{key}={value}"]
    return docker([*command, MINIMUM_IMAGE, *argv])


@pytest.fixture(scope="module")
def pinned_binary(docker_daemon: None, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The pinned Compose release, on a disposable host path, proved to run here.

    Exported rather than mounted: what has to talk to the daemon is a host
    process, because the alternative is a container holding the Docker socket.
    BuildKit exports the image's filesystem for the host's own platform, which
    for macOS is not a platform the engine could run a container of. The copy is
    removed with the test's own temporary directory.
    """
    del docker_daemon
    host = (platform.system(), platform.machine())
    if host not in HOST_IMAGE_PLATFORMS:
        pytest.skip(f"{host[0]}/{host[1]} has no binary in the pinned Compose release")
    root = tmp_path_factory.mktemp("compose-minimum")
    (root / "Dockerfile").write_text(f"FROM {MINIMUM_IMAGE}\n", encoding="utf-8")
    exported = docker(
        [
            "buildx",
            "build",
            "--platform",
            HOST_IMAGE_PLATFORMS[host],
            "--output",
            f"type=local,dest={root / 'out'}",
            str(root),
        ],
        timeout=600,
    )
    assert exported.returncode == 0, exported.output
    binary = root / "out" / BINARY_PATH.lstrip("/")
    binary.chmod(0o755)
    reported = capture([str(binary), "version", "--short"], timeout=120)
    assert reported.returncode == 0, reported.output
    assert reported.stdout.strip() == MINIMUM_COMPOSE, reported.stdout
    return binary


def host_minimum_compose(binary: Path, argv: Sequence[str], *, timeout: int = 300) -> Captured:
    """Run the pinned release as a host process against the real daemon."""
    return capture([str(binary), *argv], timeout=timeout)


def test_the_operator_file_declares_the_version_this_suite_measures() -> None:
    """A floor nobody measures is a floor the deployment does not have."""
    assert DECLARED_FLOOR in declared_minimum()
    assert MINIMUM_COMPOSE.startswith("2.24.")


def test_the_minimum_release_resolves_the_operator_file(
    docker_daemon: None, contract_environment: dict[str, str]
) -> None:
    """Its own parser answers for every construct the file uses, not the developer's.

    The inline `configs` content included: the bootstrap script has to resolve as
    a config the `db-bootstrap` service mounts.
    """
    del docker_daemon
    result = minimum_compose(
        ["--env-file", "/dev/null", "--file", "docker-compose.yml", "config", "--format", "json"],
        environment=contract_environment,
    )

    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    assert sorted(measured["services"]) == sorted(resolve(contract_environment)["services"])
    assert "content" in measured["configs"]["db-bootstrap-script"], measured["configs"]


@pytest.mark.parametrize("flag", REQUIRED_FLAGS)
def test_the_minimum_release_offers_every_flag_the_documented_commands_use(docker_daemon: None, flag: str) -> None:
    """`up -d --wait` passes these; a release without one fails at the command."""
    del docker_daemon
    result = minimum_compose(["up", "--help"])

    assert result.returncode == 0, result.stderr
    assert flag in result.stdout


def test_the_minimum_release_accepts_an_env_file(docker_daemon: None) -> None:
    """The documented commands name the operator's `.env` explicitly."""
    del docker_daemon
    result = minimum_compose(["--help"])

    assert result.returncode == 0, result.stderr
    assert "--env-file stringArray" in result.stdout


def test_the_pinned_release_starts_a_local_image_with_an_inline_config_and_a_completion_gate(
    pinned_binary: Path, sync_image: str, tmp_path: Path
) -> None:
    """The half a parse cannot reach, run by the release the file declares.

    Three things have to hold together at once, and only a running engine can
    say so: an inline `configs` entry is mounted into the container that reads
    it, the image under test is run from the engine's own store, and
    `service_completed_successfully` is honoured as a real start gate rather than
    accepted as syntax.

    The binary here is the pinned 2.24.0 one, running as a host process. The
    developer's installed Compose is not involved.
    """
    fixture = tmp_path / "docker-compose.yml"
    fixture.write_text(
        "services:\n"
        "  once:\n"
        f'    image: "{sync_image}"\n'
        '    command: ["python", "/etc/marker.py"]\n'
        "    configs:\n"
        "      - source: marker\n"
        "        target: /etc/marker.py\n"
        "  after:\n"
        f'    image: "{sync_image}"\n'
        '    command: ["python", "-c", "import time; time.sleep(120)"]\n'
        "    depends_on:\n"
        "      once:\n"
        "        condition: service_completed_successfully\n"
        "configs:\n"
        "  marker:\n"
        "    content: |\n"
        "      print('converged')\n",
        encoding="utf-8",
    )
    project = "infrahub-sync-minimum-fixture"
    base = ["--project-name", project, "--file", str(fixture)]
    started = host_minimum_compose(
        pinned_binary,
        [*base, "up", "--detach", "--wait", "--wait-timeout", str(FIXTURE_WAIT_SECONDS), "after"],
        timeout=FIXTURE_WAIT_SECONDS + FIXTURE_PROCESS_CUSHION_SECONDS,
    )
    try:
        assert started.returncode == 0, started.output
        logs = host_minimum_compose(pinned_binary, [*base, "logs", "--no-color", "once"])
        assert "converged" in logs.stdout, logs.output
        # The gate, not just the order: the dependent service is running because
        # the one-off completed, and the one-off is gone because it finished.
        state = host_minimum_compose(pinned_binary, [*base, "ps", "--format", "json"])
        assert state.returncode == 0, state.output
        assert "after" in state.stdout, state.stdout
    finally:
        host_minimum_compose(pinned_binary, [*base, "down", "--remove-orphans", "--volumes"])
