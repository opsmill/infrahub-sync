"""The `cli` service: the shipped CLI, run as `docker compose run --rm cli ...`.

The removed wrapper's `cli` command is now one plain Compose call against the
root `docker-compose.yml`. These cases run that call in the image under test:
what the container is given, that it answers as the CLI, and that it reaches a
running deployment with the token the operator's `.env` carries.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import CONTRACT_ENVIRONMENT, IMAGE_REPOSITORY_ENV, IMAGE_VERSION_ENV, compose
from tests.compose.redaction import SECRETS, Captured

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from tests.compose.lifecycle import Deployment

# The service and the profile it sits behind.
CLI_SERVICE = "cli"
CLI_PROFILE = "cli"
# Where the documented recipe mounts a declared package for one call, and the
# image user that has to be able to read it.
MOUNT_TARGET = "/input/package.yaml"
IMAGE_UID = 10001


# ---------------------------------------------------------------------------
# The image under test
# ---------------------------------------------------------------------------


# The value the CLI container is meant to receive, and the two source tokens it
# must not. Distinct per name, so a private comparison can say which one crossed.
CLIENT_CREDENTIAL = "cli-container-client-token-8ad3f1"
SOURCE_TOKENS = {
    "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN": "cli-container-netbox-token-2b71ce",
    "INFRAHUB_SYNC_CREDENTIAL_NAUTOBOT_TOKEN": "cli-container-nautobot-token-6f0a94",
}


def _container_environment() -> dict[str, str]:
    """Every operator input the file needs, with the image under test named.

    The client token is non-empty on purpose: a probe run with an empty one
    proves the name is present and nothing about the value reaching the child.
    The two source tokens are planted for the same reason — a comparison against
    values nobody supplied would pass on a file that leaked both.
    """
    image = {name: os.environ.get(name, "").strip() for name in (IMAGE_REPOSITORY_ENV, IMAGE_VERSION_ENV)}
    if not all(image.values()):
        pytest.skip(f"{IMAGE_REPOSITORY_ENV} and {IMAGE_VERSION_ENV} name no built image to run")
    SECRETS.register(CLIENT_CREDENTIAL, *SOURCE_TOKENS.values())
    return {
        **CONTRACT_ENVIRONMENT,
        **SOURCE_TOKENS,
        **image,
        "INFRAHUB_SYNC_API_TOKEN": CLIENT_CREDENTIAL,
    }


# Read from inside the container: every environment name, and a digest of each
# value. A digest is not the value it covers, so what crosses back out of the
# container proves what it holds without rendering any of it.
CONTAINER_PROBE = (
    "import hashlib,json,os;"
    "print(json.dumps({name: hashlib.sha256(value.encode()).hexdigest()"
    " for name, value in os.environ.items()}))"
)


def _probe_payload(result: Captured) -> str:
    """Return the one JSON line the probe printed, from the raw stream.

    Raw, because a credential-named key's value is redacted by name and the probe
    reports a digest under one of those names. The raw text is parsed and never
    rendered: what leaves this module is a Boolean and a list of names.
    """
    candidates = [line for line in result.unredacted().splitlines() if line.startswith("{") and line.endswith("}")]
    assert candidates, "the probe printed no JSON object"
    return candidates[-1]


def _digest(value: str) -> str:
    """The digest a value is compared by, so no comparison holds the value itself."""
    return hashlib.sha256(value.encode()).hexdigest()


def _cli(
    arguments: Sequence[str],
    environment: Mapping[str, str],
    *,
    entrypoint: str | None = None,
) -> Captured:
    """One transient CLI container of the image under test, with no dependency started.

    `entrypoint` replaces the service's own, which is the only way to ask the
    container a question the CLI has no command for.
    """
    override = ["--entrypoint", entrypoint] if entrypoint is not None else []
    return compose(
        ["--profile", CLI_PROFILE, "run", "--rm", "--no-deps", "-T", *override, CLI_SERVICE, *arguments],
        environment=dict(environment),
    )


@pytest.mark.docker
def test_the_image_answers_cli_help_as_its_entrypoint(docker_daemon: None) -> None:
    """The shipped service runs the CLI, in the image the deployment runs."""
    del docker_daemon

    result = _cli(["--help"], _container_environment())

    assert result.returncode == 0, result.output
    assert "configs" in result.stdout, result.output
    assert "runs" in result.stdout, result.output


@pytest.mark.docker
def test_the_cli_container_holds_no_credential_beyond_its_own_api_token(docker_daemon: None) -> None:
    """It talks to the Sync API and to nothing else, so it is given nothing else.

    Read from inside the container it actually ran in, and reported by name: a
    resolved model says what was asked for, and this says what the process got.
    """
    del docker_daemon
    environment = _container_environment()

    result = _cli(["-c", CONTAINER_PROBE], environment, entrypoint="python")

    assert result.returncode == 0, result.output
    # Read from the raw stream, and only here. The redaction boundary replaces a
    # credential-named key's value, so on the redacted stream the digest under
    # `INFRAHUB_SYNC_API_TOKEN` is already `[redacted]` and answers nothing. The
    # obligation that comes with the exception is met below: every comparison is
    # made before its assertion, and only Booleans and names reach one.
    digests = json.loads(_probe_payload(result))
    # The file's own settings, by prefix. `GPG_KEY` and `PREFECT_HOME` belong
    # to the images underneath and are not operator inputs, which is why this
    # names what the file supplies rather than matching credential-shaped
    # words -- an over-broad match here would report the base image's own keys.
    supplied = {name for name in digests if name.startswith(("INFRAHUB_", "AWS_", "POSTGRES_", "PREFECT_API"))}

    assert supplied == {"INFRAHUB_SYNC_API_URL", "INFRAHUB_SYNC_API_TOKEN"}, sorted(supplied)

    # The intended value arrived, and no other operator input did. Both compared
    # as digests before the assertions, so what reaches a failure report is a
    # Boolean and a list of names.
    expected = _digest(CLIENT_CREDENTIAL)
    received_intended = digests.get("INFRAHUB_SYNC_API_TOKEN") == expected
    planted = {
        name: _digest(value)
        for name, value in environment.items()
        if name not in {"INFRAHUB_SYNC_API_URL", "INFRAHUB_SYNC_API_TOKEN"} and len(value) >= 8
    }
    carried = sorted(name for name, digest in planted.items() if digest in set(digests.values()))

    assert received_intended, "the CLI container did not receive the client token the deployment gave it"
    assert carried == [], f"these operator inputs reached the CLI container: {carried}"


# ---------------------------------------------------------------------------
# Against a running deployment
# ---------------------------------------------------------------------------


def _deployment_cli(deployment: Deployment, *arguments: str) -> Captured:
    """`docker compose --profile cli run --rm --no-deps -T cli ...` against one deployment."""
    return deployment.compose(["--profile", CLI_PROFILE, "run", "--rm", "--no-deps", "-T", CLI_SERVICE, *arguments])


@pytest.mark.compose
def test_the_cli_service_reaches_the_deployment_with_the_token_its_env_file_carries(deployment: Deployment) -> None:
    """The replacement for `./infrahub-sync-compose cli configs list`.

    The `.env` carries the principal's token twice: once in the API's
    `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, once as the `INFRAHUB_SYNC_API_TOKEN`
    the `cli` service presents. A call that authenticated proves both reached
    the right container.
    """
    listed = _deployment_cli(deployment, "configs", "list")

    assert listed.returncode == 0, listed.output


@pytest.mark.compose
def test_a_package_mounted_read_only_is_readable_by_the_image_user(deployment: Deployment, tmp_path: Path) -> None:
    """The documented recipe for `--package`: one file, mounted read-only for one call.

    The image's own user runs the CLI, so a package the caller's account alone
    can read would be refused inside the container. The recipe's `chmod 0644`
    is what this proves sufficient.
    """
    package = tmp_path / "package.yml"
    package.write_text("format_version: 1\nconfiguration:\n  name: mounted-example\n", encoding="utf-8")
    package.chmod(0o644)
    expected = hashlib.sha256(package.read_bytes()).hexdigest()

    read = deployment.compose(
        [
            "--profile",
            CLI_PROFILE,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--volume",
            f"{package}:{MOUNT_TARGET}:ro",
            "--entrypoint",
            "python",
            CLI_SERVICE,
            "-c",
            f"import hashlib, os; print(os.getuid(), hashlib.sha256(open({MOUNT_TARGET!r}, 'rb').read()).hexdigest())",
        ]
    )

    assert read.returncode == 0, read.output
    assert read.stdout.split()[-2:] == [str(IMAGE_UID), expected], read.stdout
