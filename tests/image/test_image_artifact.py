"""Properties of the built image, checked by running it the way it is deployed."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess  # noqa: S404 -- fixed argv Git and Docker probes for the image gate
from importlib.metadata import version as installed_version
from typing import Any

import pytest
from packaging.version import Version

from tests.image.conftest import (
    EXCLUDED_MODULES,
    READ_ONLY_ROOTS,
    REPO_ROOT,
    RUNTIME_GID,
    RUNTIME_TOOLS,
    RUNTIME_UID,
    SERVICE_RUNTIMES,
    WRITABLE_ROOTS,
    docker,
    run_in_image,
    wait_for_api,
)
from tests.image.test_version_policy import RUNTIME_PYTHON_FLOOR

pytestmark = pytest.mark.docker

EXPECTED_LABELS = {
    "org.opencontainers.image.title": "infrahub-sync",
    "org.opencontainers.image.source": "https://github.com/opsmill/infrahub-sync",
    "org.opencontainers.image.licenses": "Apache-2.0",
}
PROVENANCE_LABELS = (
    "org.opencontainers.image.version",
    "org.opencontainers.image.revision",
    "org.opencontainers.image.created",
)

WRITE_PROBE = """
import json, os, sys, uuid
result = {}
for path in json.loads(sys.argv[1]):
    candidate = os.path.join(path, "probe-" + uuid.uuid4().hex)
    try:
        with open(candidate, "w", encoding="utf-8") as handle:
            handle.write("probe")
    except OSError:
        result[path] = False
    else:
        os.unlink(candidate)
        result[path] = True
print(json.dumps(result))
"""

# `generate` renders Python and formats it through this exact command, so a shipped
# Ruff that cannot run is a broken command form rather than a missing nicety.
RUFF_PROBE = """
from infrahub_sync.generator import format_generated_python
print(format_generated_python(source="x = {  'a' :1 }\\n", filename="generated.py").strip())
"""


def git(*argv: str) -> str:
    """Return the trimmed output of one fixed-argv Git command in this repository."""
    result = subprocess.run(  # noqa: S603 -- fixed argv
        ["git", "-C", str(REPO_ROOT), *argv],  # noqa: S607 -- Git is resolved from the gate's PATH
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def image_config(image_ref: str) -> dict[Any, Any]:
    result = docker(["inspect", "--format", "{{json .Config}}", image_ref])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_runtime_interpreter_is_at_or_above_the_python_floor(image_ref: str) -> None:
    """The base tag moves; what has to hold is the patch level carrying the fix."""
    result = run_in_image(image_ref, ["python", "-c", "import platform; print(platform.python_version())"])

    assert result.returncode == 0, result.stderr
    assert Version(result.stdout.strip().splitlines()[-1]) >= RUNTIME_PYTHON_FLOOR


def test_the_image_runs_as_the_fixed_non_root_identity(image_ref: str) -> None:
    result = run_in_image(image_ref, ["python", "-c", "import os; print(os.getuid(), os.getgid())"])

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == [str(RUNTIME_UID), str(RUNTIME_GID)]


def test_the_declared_identity_is_what_the_image_configuration_records(image_ref: str) -> None:
    """A numeric user is what a read-only host can map; a name is resolved inside."""
    assert image_config(image_ref)["User"] == f"{RUNTIME_UID}:{RUNTIME_GID}"


def test_only_the_declared_roots_are_writable_under_a_read_only_root(image_ref: str) -> None:
    """Stated as a closure over both sets, so a newly writable path fails the gate."""
    probed = [*WRITABLE_ROOTS, *READ_ONLY_ROOTS]

    result = run_in_image(image_ref, ["python", "-c", WRITE_PROBE, json.dumps(probed)])

    assert result.returncode == 0, result.stderr
    writable = json.loads(result.stdout.strip().splitlines()[-1])
    assert {path: writable[path] for path in WRITABLE_ROOTS} == dict.fromkeys(WRITABLE_ROOTS, True)
    assert {path: writable[path] for path in READ_ONLY_ROOTS} == dict.fromkeys(READ_ONLY_ROOTS, False)


def test_the_default_command_serves_the_sync_api(started_container: str) -> None:
    """The image needs no argument to be the API; every other form is an override."""
    info = wait_for_api(started_container)

    assert info["title"] == "Infrahub Sync API"
    assert info["version"] == installed_version("infrahub-sync")


def test_the_worker_command_form_is_available(image_ref: str) -> None:
    result = run_in_image(image_ref, ["python", "-m", "infrahub_sync.service.worker", "--help"])

    assert result.returncode == 0, result.stderr
    assert "--pool" in result.stdout


def test_the_bootstrap_command_form_resolves_its_deployment_catalogue(image_ref: str) -> None:
    """The bootstrap module and its catalogue import; applying one needs a server."""
    result = run_in_image(
        image_ref,
        ["python", "-c", "from infrahub_sync.service.deploy import CATALOGUE, main; print(len(CATALOGUE))"],
    )

    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip().splitlines()[-1]) >= 1


def test_the_cli_command_form_is_available(image_ref: str) -> None:
    result = run_in_image(image_ref, ["infrahub-sync", "--help"])

    assert result.returncode == 0, result.stderr
    assert "configs" in result.stdout


def test_the_python_command_form_imports_the_installed_distribution(image_ref: str) -> None:
    """No source tree ships, so the only package the image can import is the installed one."""
    result = run_in_image(
        image_ref,
        ["python", "-c", "import infrahub_sync; print(infrahub_sync.__file__)"],
    )

    assert result.returncode == 0, result.stderr
    location = result.stdout.strip().splitlines()[-1]
    assert location.startswith("/opt/infrahub-sync/venv/lib/"), location
    assert "site-packages" in location, location


def test_the_image_carries_the_source_provenance_of_a_real_commit(image_ref: str) -> None:
    """`created` comes from the commit, so two builds of one commit agree on it."""
    labels = image_config(image_ref)["Labels"]
    assert isinstance(labels, dict)

    assert {key: labels[key] for key in EXPECTED_LABELS} == EXPECTED_LABELS
    assert set(labels) == set(EXPECTED_LABELS) | set(PROVENANCE_LABELS)
    revision = labels["org.opencontainers.image.revision"]
    assert re.fullmatch(r"[0-9a-f]{40}", revision), revision
    assert git("cat-file", "-t", f"{revision}^{{commit}}") == "commit"
    assert labels["org.opencontainers.image.created"] == git("show", "-s", "--format=%cI", revision)
    assert labels["org.opencontainers.image.version"] == installed_version("infrahub-sync")


@pytest.mark.parametrize("module", [*SERVICE_RUNTIMES, *RUNTIME_TOOLS])
def test_the_image_ships_everything_the_project_declares_it_needs_at_runtime(image_ref: str, module: str) -> None:
    result = run_in_image(image_ref, ["python", "-c", f"import {module}"])

    assert result.returncode == 0, f"{module}: {result.stderr}"


def test_the_shipped_ruff_can_format_generated_code(image_ref: str) -> None:
    """Importable is not enough: `generate` runs Ruff as a subprocess and reads its output.

    Exercised through the product's own helper, under the read-only root and the
    declared writable mounts, so this fails if Ruff is absent, unrunnable, or
    unable to work where the image lets it write.
    """
    result = run_in_image(image_ref, ["python", "-c", RUFF_PROBE])

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == 'x = {"a": 1}'


@pytest.mark.parametrize("module", EXCLUDED_MODULES)
def test_the_image_ships_no_development_only_module(image_ref: str, module: str) -> None:
    result = run_in_image(image_ref, ["python", "-c", f"import {module}"])

    assert result.returncode != 0, f"{module} is importable in the runtime image"


def test_the_image_ships_no_installer(image_ref: str) -> None:
    """uv builds the environment; carrying it into the runtime would widen the artifact."""
    result = run_in_image(image_ref, ["python", "-c", "import shutil,sys; sys.exit(0 if shutil.which('uv') else 1)"])

    assert result.returncode != 0, "uv is on PATH in the runtime image"


def test_the_image_records_the_committed_lock_it_was_built_from(image_ref: str) -> None:
    """The recorded lock is what makes the shipped dependency set auditable offline."""
    result = run_in_image(
        image_ref,
        [
            "python",
            "-c",
            "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('/opt/infrahub-sync/uv.lock').read_bytes()).hexdigest())",
        ],
    )

    assert result.returncode == 0, result.stderr
    expected = hashlib.sha256((REPO_ROOT / "uv.lock").read_bytes()).hexdigest()
    assert result.stdout.strip().splitlines()[-1] == expected
