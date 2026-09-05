"""The declared minimum Compose version is measured, not assumed.

Two halves. The pinned minimum binary is run against the shipped bundle, so the
file's own feature set — `depends_on` conditions including
`service_completed_successfully`, `pull_policy`, repeatable `--env-file` — is
answered by that release rather than by the developer's. And the installed
Compose runs a fixture that exercises the parts a parse cannot reach: a real
local image named by its configuration digest, started with `pull_policy: never`
and waited on through a completion-style dependency.
"""

from __future__ import annotations

import json
import subprocess  # noqa: S404 -- fixed argv Compose invocations
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import BUNDLE, compose, resolve
from tests.compose.lifecycle import docker

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = pytest.mark.compose

# The floor the entry point declares, and the image that ships exactly it. Older
# releases are not published as a pinned binary, so this is the oldest version
# the bundle can be measured against rather than a claim that 2.17.2 fails.
MINIMUM_COMPOSE = "2.17.3"
MINIMUM_IMAGE = f"docker/compose-bin:v{MINIMUM_COMPOSE}"

# What the entry point asks of the Compose CLI beyond parsing the file.
REQUIRED_FLAGS = ("--wait", "--wait-timeout", "--quiet-pull", "--detach")


def entry_point_minimum() -> str:
    """Return the minimum the shipped entry point actually enforces."""
    script = (BUNDLE / "infrahub-sync-compose").read_text(encoding="utf-8")
    line = next(line for line in script.splitlines() if line.startswith("MINIMUM_COMPOSE="))
    return line.split("=", 1)[1].strip()


def minimum_compose(
    argv: list[str], *, environment: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the pinned minimum Compose release against the shipped bundle."""
    command = ["run", "--rm", "--entrypoint", "/docker-compose", "--volume", f"{BUNDLE}:/work:ro", "--workdir", "/work"]
    for key, value in (environment or {}).items():
        command += ["--env", f"{key}={value}"]
    return docker([*command, MINIMUM_IMAGE, *argv])


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


def test_a_real_digest_named_image_starts_under_a_completion_style_dependency(sync_image: str, tmp_path: Path) -> None:
    """The half a parse cannot reach: the engine resolving an image ID it cannot pull.

    `pull_policy: never` is what makes this a statement about the candidate the
    image gate built — nothing here may reach a registry, and no registry holds
    this digest.
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
    started = compose(
        ["up", "--detach", "--wait", "--wait-timeout", "120", "after"],
        project=project,
        files=(fixture,),
        env_files=(),
    )
    try:
        assert started.returncode == 0, started.stderr
        logs = compose(["logs", "--no-color", "once"], project=project, files=(fixture,), env_files=())
        assert "converged" in logs.stdout, logs.stdout
    finally:
        compose(["down", "--remove-orphans"], project=project, files=(fixture,), env_files=())
