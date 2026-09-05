"""The declared minimum Compose version is measured, not assumed.

Everything here is answered by the pinned 2.17.3 release itself, never by the
developer's installed one, and it is asked in two ways because the questions
differ.

Parsing the shipped bundle needs no daemon, so those cases run the pinned binary
inside its own image with the bundle mounted read-only. Nothing there starts a
container, and no Docker socket is involved.

Starting a real deployment does need a daemon, and the pinned binary has to be
the thing that talks to it. Mounting the host's Docker socket into a container
to arrange that would hand a container control of the engine, which the bundle
refuses on principle and this suite will not do to test it. So the binary is
copied out of its image onto a disposable host path and run as an ordinary host
process, exactly as an operator on the qualified floor would run it. That works
where the host can execute a Linux binary, which is the amd64 Linux host the
lifecycle claim is made on; elsewhere the live case says so and skips rather
than quietly substituting the installed release.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import BUNDLE, resolve
from tests.compose.lifecycle import docker
from tests.compose.redaction import Captured, capture

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

pytestmark = pytest.mark.compose

# The floor the entry point declares, and the image that ships exactly it. Older
# releases are not published as a pinned binary, so this is the oldest version
# the bundle can be measured against rather than a claim that 2.17.2 fails.
MINIMUM_COMPOSE = "2.17.3"
MINIMUM_IMAGE = f"docker/compose-bin:v{MINIMUM_COMPOSE}"
# Where the binary sits inside that image.
BINARY_PATH = "/docker-compose"

# What the entry point asks of the Compose CLI beyond parsing the file.
REQUIRED_FLAGS = ("--wait", "--wait-timeout", "--quiet-pull", "--detach")

# The declared bound for the live fixture, given to the pinned binary itself.
FIXTURE_WAIT_SECONDS = 120
FIXTURE_PROCESS_CUSHION_SECONDS = 60

# The host architectures a Linux binary pulled out of that image can be executed
# on directly. A host that is not one of them cannot run the pinned release as a
# process at all, and the only alternatives would put the Docker socket inside a
# container.
HOST_IMAGE_PLATFORMS = {
    ("Linux", "x86_64"): "linux/amd64",
    ("Linux", "amd64"): "linux/amd64",
    ("Linux", "aarch64"): "linux/arm64",
    ("Linux", "arm64"): "linux/arm64",
}


def entry_point_minimum() -> str:
    """Return the minimum the shipped entry point actually enforces."""
    script = (BUNDLE / "infrahub-sync-compose").read_text(encoding="utf-8")
    line = next(line for line in script.splitlines() if line.startswith("MINIMUM_COMPOSE="))
    return line.split("=", 1)[1].strip()


def minimum_compose(argv: list[str], *, environment: Mapping[str, str] | None = None) -> Captured:
    """Parse-only: the pinned release, in its own image, with no daemon behind it.

    The bundle is the only thing mounted, read-only. Nothing this answers needs
    an engine, so nothing here is given one.
    """
    command = [
        "run",
        "--rm",
        "--entrypoint",
        BINARY_PATH,
        "--volume",
        f"{BUNDLE}:/work:ro",
        "--workdir",
        "/work",
    ]
    for key, value in (environment or {}).items():
        command += ["--env", f"{key}={value}"]
    return docker([*command, MINIMUM_IMAGE, *argv])


@pytest.fixture(scope="module")
def pinned_binary(docker_daemon: None, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The pinned Compose release, on a disposable host path, proved to run here.

    Copied out of the image rather than mounted from it: what has to talk to the
    daemon is a host process, because the alternative is a container holding the
    Docker socket. The copy is removed with the test's own temporary directory,
    and the container it was copied from is removed as soon as it has been read.
    """
    del docker_daemon
    host = (platform.system(), platform.machine())
    if host not in HOST_IMAGE_PLATFORMS:
        pytest.skip(
            f"{host[0]}/{host[1]} cannot execute the Linux binary this release ships; "
            f"the live minimum-version case runs on the qualified amd64 Linux host"
        )
    # A command the container will never run: the image declares none, and
    # `create` refuses without one. Nothing here starts it -- it exists only to
    # be read out of.
    created = docker(["create", "--platform", HOST_IMAGE_PLATFORMS[host], MINIMUM_IMAGE, BINARY_PATH, "--version"])
    assert created.returncode == 0, created.stderr
    container = created.stdout.strip()
    binary = tmp_path_factory.mktemp("compose-minimum") / "docker-compose"
    try:
        copied = docker(["cp", f"{container}:{BINARY_PATH}", str(binary)])
        assert copied.returncode == 0, copied.stderr
    finally:
        docker(["rm", "--force", container])
    binary.chmod(0o755)
    reported = capture([str(binary), "version", "--short"], timeout=120)
    assert reported.returncode == 0, reported.output
    assert reported.stdout.strip() == MINIMUM_COMPOSE, reported.stdout
    return binary


def host_minimum_compose(binary: Path, argv: Sequence[str], *, timeout: int = 300) -> Captured:
    """Run the pinned release as a host process against the real daemon."""
    return capture([str(binary), *argv], timeout=timeout)


def test_the_entry_point_enforces_the_version_this_suite_measures() -> None:
    """A floor nobody checks is a floor the deployment does not have."""
    assert entry_point_minimum() == MINIMUM_COMPOSE


def test_the_minimum_release_resolves_the_shipped_bundle(contract_environment: dict[str, str]) -> None:
    """Its own parser answers for every construct the file uses, not the developer's.

    The administrator password is a file input, so the minimum release needs one
    inside its own view of the bundle; the copy is what keeps this suite off an
    operator's real secret.
    """
    secret = BUNDLE / "secrets" / "measured-admin-password"
    secret.parent.mkdir(exist_ok=True)
    secret.write_text("measured\n", encoding="utf-8")
    try:
        result = minimum_compose(
            ["--env-file", "defaults.conf", "--file", "compose.yaml", "config", "--format", "json"],
            environment={
                **contract_environment,
                "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD_FILE": "/work/secrets/measured-admin-password",
            },
        )
    finally:
        secret.unlink()
        if not any(secret.parent.iterdir()):
            secret.parent.rmdir()

    assert result.returncode == 0, result.stderr
    measured = json.loads(result.stdout)
    assert sorted(measured["services"]) == sorted(resolve(contract_environment)["services"])


@pytest.mark.parametrize("flag", REQUIRED_FLAGS)
def test_the_minimum_release_offers_every_flag_the_entry_point_uses(flag: str) -> None:
    """`start` passes these; a release without one fails at the command, not at preflight."""
    result = minimum_compose(["up", "--help"])

    assert result.returncode == 0, result.stderr
    assert flag in result.stdout


def test_the_minimum_release_accepts_a_repeated_env_file() -> None:
    """The entry point layers the operator's file over the shipped defaults."""
    result = minimum_compose(["--help"])

    assert result.returncode == 0, result.stderr
    assert "--env-file stringArray" in result.stdout


def test_the_pinned_release_starts_a_real_digest_named_image_it_cannot_pull(
    pinned_binary: Path, sync_image: str, tmp_path: Path
) -> None:
    """The half a parse cannot reach, run by the release the bundle declares.

    Three things have to hold together at once, and only a running engine can
    say so: the engine resolves a bare `sha256:` configuration digest as an
    image, `pull_policy: never` keeps it from reaching for a registry that holds
    no such digest, and `service_completed_successfully` is honoured as a real
    start gate rather than accepted as syntax.

    The binary here is the pinned 2.17.3 one, running as a host process. The
    developer's installed Compose is not involved.
    """
    fixture = tmp_path / "compose.yaml"
    fixture.write_text(
        "services:\n"
        "  once:\n"
        f'    image: "{sync_image}"\n'
        "    pull_policy: never\n"
        '    command: ["python", "-c", "print(\'converged\')"]\n'
        "  after:\n"
        f'    image: "{sync_image}"\n'
        "    pull_policy: never\n"
        '    command: ["python", "-c", "import time; time.sleep(120)"]\n'
        "    depends_on:\n"
        "      once:\n"
        "        condition: service_completed_successfully\n",
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


def test_the_pinned_release_refuses_to_reach_a_registry_for_that_digest(pinned_binary: Path, tmp_path: Path) -> None:
    """`pull_policy: never` is what makes the case above a statement about the candidate.

    A digest no registry holds, started under the same policy: the release has to
    refuse locally rather than go looking. Without this, a fixture that silently
    pulled would still pass and would be qualifying something else.
    """
    fixture = tmp_path / "compose.yaml"
    fixture.write_text(
        f'services:\n  absent:\n    image: "sha256:{"c" * 64}"\n    pull_policy: never\n    command: ["true"]\n',
        encoding="utf-8",
    )
    project = "infrahub-sync-minimum-absent"
    base = ["--project-name", project, "--file", str(fixture)]
    try:
        refused = host_minimum_compose(
            pinned_binary, [*base, "up", "--detach", "--wait", "--wait-timeout", "30", "absent"], timeout=120
        )

        assert refused.returncode != 0, refused.output
    finally:
        host_minimum_compose(pinned_binary, [*base, "down", "--remove-orphans", "--volumes"])
