"""The wrapper's `cli` command, through the shipped entry point.

Every case runs `deploy/compose/infrahub-sync-compose` itself against a copy of
the bundle, with Docker replaced by a shim that records what Compose was asked
to run and reports what the staged package looked like at that moment. The stage
is removed when the command ends, so a test that looked afterwards would find
nothing: the shim is what observes it while it exists.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess  # noqa: S404 -- the interrupt case drives the entry point directly
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import BUNDLE, CONTRACT_ENVIRONMENT, DEFAULTS_FILE, compose
from tests.compose.redaction import SECRETS, Captured, capture

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ENTRY_POINT = "infrahub-sync-compose"
MINIMUM_COMPOSE = "2.17.3"

# Where the bundle mounts a staged package inside the CLI container, and the
# image user that has to be able to read it.
MOUNT_TARGET = "/input/package.yaml"
IMAGE_UID = 10001

DOCKER_SHIM = r"""#!/bin/sh
# A Docker stand-in for the container-CLI suite. It answers the two questions the
# `cli` command asks -- the Compose version, and the one `compose run` that
# executes the CLI -- and refuses anything else loudly.

mode_of() {
    stat -f '%Lp' "$1" 2>/dev/null || stat -c '%a' "$1"
}

if [ "$1" = "compose" ]; then
    # One line per call, and one line per call with every argument delimited, so
    # a test can tell an argument vector from a re-split string.
    [ -n "${SHIM_ARGV_LOG:-}" ] && printf '%s\n' "$*" >> "$SHIM_ARGV_LOG"
    if [ -n "${SHIM_ARGV_VECTOR:-}" ]; then
        for word in "$@"; do printf '[%s]' "$word" >> "$SHIM_ARGV_VECTOR"; done
        printf '\n' >> "$SHIM_ARGV_VECTOR"
    fi
    sub=""
    for word in "$@"; do
        case "$word" in
            version | run) sub=$word; break ;;
        esac
    done
    case "$sub" in
        version)
            printf '%s\n' "${SHIM_COMPOSE_VERSION}"
            exit 0
            ;;
        run)
            if [ -n "${SHIM_STAGE_RECORD:-}" ]; then
                for word in "$@"; do
                    case "$word" in
                        *":$MOUNT_TARGET_PATTERN:ro")
                            source=${word%%":$MOUNT_TARGET_PATTERN:ro"}
                            {
                                printf 'mount=%s\n' "$word"
                                printf 'source=%s\n' "$source"
                                printf 'file-mode=%s\n' "$(mode_of "$source")"
                                printf 'dir-mode=%s\n' "$(mode_of "$(dirname "$source")")"
                                printf 'content=%s\n' "$(od -An -tx1 < "$source" | tr -d ' \n')"
                            } >> "$SHIM_STAGE_RECORD"
                            ;;
                    esac
                done
            fi
            [ "${SHIM_CLI_HANG:-0}" = "1" ] && sleep 30
            exit "${SHIM_CLI_RC:-0}"
            ;;
    esac
    printf 'docker shim: unexpected compose call: %s\n' "$*" >&2
    exit 97
fi
printf 'docker shim: unexpected call: %s\n' "$*" >&2
exit 97
"""


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """A private copy of the shipped bundle this test may write into."""
    copy = tmp_path / "compose"
    shutil.copytree(BUNDLE, copy)
    return copy


@pytest.fixture
def shim(tmp_path: Path) -> Path:
    """A directory holding the Docker stand-in, to be put first on PATH."""
    directory = tmp_path / "bin"
    directory.mkdir()
    executable = directory / "docker"
    executable.write_text(DOCKER_SHIM.replace("$MOUNT_TARGET_PATTERN", MOUNT_TARGET), encoding="utf-8")
    executable.chmod(0o755)
    return directory


def run(
    bundle: Path,
    shim: Path,
    *arguments: str,
    environment: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Captured:
    """Run one lifecycle command with Docker shimmed out."""
    return capture(
        [str(bundle / ENTRY_POINT), *arguments],
        timeout=120,
        cwd=cwd,
        env={
            **os.environ,
            "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
            "SHIM_COMPOSE_VERSION": MINIMUM_COMPOSE,
            **(environment or {}),
        },
    )


@pytest.fixture
def initialized(bundle: Path, shim: Path) -> Path:
    """A bundle that has been through `init` and had its one placeholder filled."""
    created = run(bundle, shim, "init")
    assert created.returncode == 0, created.stderr
    settings = bundle / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace(
            "INFRAHUB_SYNC_IMAGE=REPLACE-ME", f"INFRAHUB_SYNC_IMAGE=sha256:{'a' * 64}"
        ),
        encoding="utf-8",
    )
    return bundle


def family(result: Captured) -> str:
    """Return the refusal family a run reported, or '' when it did not refuse."""
    for line in result.stderr.splitlines():
        if line.startswith("infrahub-sync: "):
            return line.removeprefix("infrahub-sync: ").split(":", 1)[0]
    return ""


def setting(bundle: Path, name: str) -> str:
    """Read one operator setting the way the entry point does."""
    for line in (bundle / "operator.env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1]
    return ""


def staged(record: Path) -> dict[str, str]:
    """What the shim saw of the staged package while the invocation was running."""
    assert record.is_file(), "no `compose run` reported a staged package mount"
    return dict(line.split("=", 1) for line in record.read_text(encoding="utf-8").splitlines() if "=" in line)


def vector(record: Path) -> list[list[str]]:
    """Every recorded Compose call, as the argument vector it was given."""
    return [re.findall(r"\[([^\]]*)\]", line) for line in record.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------------------
# The token pair `init` generates
# ---------------------------------------------------------------------------


def test_init_writes_one_generated_token_into_both_settings_that_name_it(bundle: Path, shim: Path) -> None:
    """The server principal and the client setting are one credential, not two.

    Two independently generated values would leave the shipped CLI unable to
    authenticate against the deployment the same `init` created.
    """
    result = run(bundle, shim, "init")

    assert result.returncode == 0, result.stderr
    principals = setting(bundle, "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS")
    client_token = setting(bundle, "INFRAHUB_SYNC_API_TOKEN")
    assert client_token, "init generated no client token"
    assert f'"token": "{client_token}"' in principals, "the client token is not the server principal's"


def test_init_prints_neither_value_of_the_token_pair(bundle: Path, shim: Path) -> None:
    """It reports what it wrote, not what it wrote there.

    Compared privately: the assertion reports the names that leaked, never the
    values it searched for.
    """
    result = run(bundle, shim, "init")

    generated = {
        "INFRAHUB_SYNC_API_TOKEN": setting(bundle, "INFRAHUB_SYNC_API_TOKEN"),
        "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS": setting(bundle, "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"),
    }
    assert SECRETS.leaked(result.unredacted(), generated) == []


def test_init_leaves_an_existing_operator_file_and_its_token_pair_alone(bundle: Path, shim: Path) -> None:
    """A regenerated token would lock the CLI out of the deployment it already runs."""
    run(bundle, shim, "init")
    before = (bundle / "operator.env").read_text(encoding="utf-8")

    run(bundle, shim, "init")

    assert (bundle / "operator.env").read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------


def test_cli_forwards_its_arguments_as_one_vector(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """An argument holding a space is one argument, not two.

    Re-splitting shell text would turn a reason into several arguments and a
    quoted identifier into something the CLI never sees.
    """
    recorded = tmp_path / "argv-vector.log"

    result = run(
        initialized,
        shim,
        "cli",
        "configs",
        "register",
        MOUNT_TARGET,
        "--reason",
        "register my configuration",
        environment={"SHIM_ARGV_VECTOR": str(recorded)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    run_call = next(call for call in vector(recorded) if "run" in call)
    tail = run_call[run_call.index("sync-cli") + 1 :]
    assert tail == ["configs", "register", MOUNT_TARGET, "--reason", "register my configuration"], tail


def test_cli_runs_one_transient_container_and_starts_no_dependency(
    initialized: Path, shim: Path, tmp_path: Path
) -> None:
    """Calling the CLI must not bring a deployment up as a side effect."""
    recorded = tmp_path / "argv-vector.log"

    result = run(initialized, shim, "cli", "configs", "list", environment={"SHIM_ARGV_VECTOR": str(recorded)})

    assert result.returncode == 0, result.stderr + result.stdout
    run_call = next(call for call in vector(recorded) if "run" in call)
    for expected in ("--rm", "--no-deps", "-T", "sync-cli"):
        assert expected in run_call, f"the CLI call is missing {expected}: {run_call}"
    assert not [call for call in vector(recorded) if "up" in call], "the CLI command started services"


def test_cli_preserves_the_exit_status_of_the_command_it_ran(initialized: Path, shim: Path) -> None:
    """An operator scripting against this needs the CLI's own verdict."""
    refused = run(initialized, shim, "cli", "configs", "list", environment={"SHIM_CLI_RC": "2"})

    assert refused.returncode == 2, refused.stderr + refused.stdout


def test_cli_help_is_the_clis_own_help(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """`--help` after `cli` belongs to the CLI, not to this wrapper."""
    recorded = tmp_path / "argv-vector.log"

    result = run(initialized, shim, "cli", "--help", environment={"SHIM_ARGV_VECTOR": str(recorded)})

    assert result.returncode == 0, result.stderr + result.stdout
    run_call = next(call for call in vector(recorded) if "run" in call)
    assert run_call[run_call.index("sync-cli") + 1 :] == ["--help"]


def test_the_wrapper_usage_documents_the_cli_command() -> None:
    """An operator cannot call what the usage does not name."""
    text = (BUNDLE / ENTRY_POINT).read_text(encoding="utf-8")

    assert "cli [--package FILE] [--] CLI_ARGUMENTS" in text


# ---------------------------------------------------------------------------
# Staging one package file
# ---------------------------------------------------------------------------


def _package(directory: Path, name: str = "package.yml") -> Path:
    """One declared package on the caller's filesystem, in the mode R1 refused."""
    path = directory / name
    path.write_text("format_version: 1\nconfiguration:\n  name: staged-example\n", encoding="utf-8")
    # 0640 is what an operator's own file carries, and what the container UID
    # could not read when the caller's file was mounted directly.
    path.chmod(0o640)
    return path


def test_a_staged_package_is_mounted_read_only_as_the_one_file_the_call_needs(
    initialized: Path, shim: Path, tmp_path: Path
) -> None:
    """One file, at the fixed target, read-only, and nothing else from that directory."""
    record = tmp_path / "stage.log"
    package = _package(tmp_path)

    result = run(
        initialized,
        shim,
        "cli",
        "--package",
        str(package),
        "--",
        "configs",
        "register",
        MOUNT_TARGET,
        environment={"SHIM_STAGE_RECORD": str(record)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    fields = staged(record)
    assert fields["mount"].endswith(f":{MOUNT_TARGET}:ro")
    assert Path(fields["source"]).name == "package.yaml"


def test_a_staged_package_carries_the_callers_bytes_unchanged(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """The registry checksums declared content, so a rewritten byte is a different package."""
    record = tmp_path / "stage.log"
    package = _package(tmp_path)
    expected = package.read_bytes().hex()

    run(
        initialized,
        shim,
        "cli",
        "--package",
        str(package),
        "--",
        "configs",
        "register",
        MOUNT_TARGET,
        environment={"SHIM_STAGE_RECORD": str(record)},
    )

    assert staged(record)["content"] == expected


def test_a_staged_package_is_readable_by_the_image_user_inside_a_private_directory(
    initialized: Path, shim: Path, tmp_path: Path
) -> None:
    """The copy is readable by UID 10001; the directory holding it is not readable to the host.

    This is the R1 mismatch resolved without mounting the caller's directory and
    without changing the caller's own file: the copy carries the permission the
    container user needs, and the private directory is what keeps that copy from
    being readable by other users of this host.
    """
    record = tmp_path / "stage.log"
    package = _package(tmp_path)

    run(
        initialized,
        shim,
        "cli",
        "--package",
        str(package),
        "--",
        "configs",
        "register",
        MOUNT_TARGET,
        environment={"SHIM_STAGE_RECORD": str(record)},
    )

    fields = staged(record)
    assert int(fields["file-mode"], 8) & 0o004, f"the staged copy is not readable by UID {IMAGE_UID}"
    assert int(fields["dir-mode"], 8) == 0o700, fields["dir-mode"]


def test_staging_never_touches_the_callers_own_file(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """Its mode and its bytes are the caller's, before and after."""
    package = _package(tmp_path)
    before = (package.stat().st_mode & 0o777, package.read_bytes())

    run(initialized, shim, "cli", "--package", str(package), "--", "configs", "list")

    assert (package.stat().st_mode & 0o777, package.read_bytes()) == before


def test_a_relative_package_path_resolves_against_the_callers_directory(
    initialized: Path, shim: Path, tmp_path: Path
) -> None:
    """An operator names a file where they are, not where the bundle is."""
    record = tmp_path / "stage.log"
    working = tmp_path / "work"
    working.mkdir()
    package = _package(working)

    result = run(
        initialized,
        shim,
        "cli",
        "--package",
        package.name,
        "--",
        "configs",
        "list",
        environment={"SHIM_STAGE_RECORD": str(record)},
        cwd=working,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert staged(record)["content"] == package.read_bytes().hex()


def test_a_package_path_holding_spaces_is_one_path(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """Word splitting on a path is how a staged file becomes the wrong file, or none."""
    record = tmp_path / "stage.log"
    directory = tmp_path / "my packages"
    directory.mkdir()
    package = _package(directory, name="my package.yml")

    result = run(
        initialized,
        shim,
        "cli",
        "--package",
        str(package),
        "--",
        "configs",
        "list",
        environment={"SHIM_STAGE_RECORD": str(record)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert staged(record)["content"] == package.read_bytes().hex()


@pytest.mark.parametrize("kind", ["absent", "directory"])
def test_an_unusable_package_is_refused_without_running_anything(
    initialized: Path, shim: Path, tmp_path: Path, kind: str
) -> None:
    """One readable regular file, or a refusal that names no content."""
    recorded = tmp_path / "argv-vector.log"
    target = tmp_path / "missing.yml" if kind == "absent" else tmp_path

    result = run(
        initialized,
        shim,
        "cli",
        "--package",
        str(target),
        "--",
        "configs",
        "list",
        environment={"SHIM_ARGV_VECTOR": str(recorded)},
    )

    assert result.returncode != 0
    assert family(result) == "package-unusable", result.stderr
    calls = vector(recorded) if recorded.is_file() else []
    assert not [call for call in calls if "run" in call], "a refused package still ran the CLI"


def test_a_missing_package_argument_is_a_usage_refusal(initialized: Path, shim: Path) -> None:
    """`--package` with nothing after it is not a call to guess at."""
    result = run(initialized, shim, "cli", "--package")

    assert result.returncode != 0
    assert family(result) == "cli-usage", result.stderr


def test_a_refused_package_discloses_no_content(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """A package holds endpoints and credential references; a refusal names the path only."""
    canary = "staged-package-canary-4f19ac"
    package = tmp_path / "unreadable.yml"
    package.write_text(f"format_version: 1\n# {canary}\n", encoding="utf-8")
    package.chmod(0o000)
    try:
        result = run(initialized, shim, "cli", "--package", str(package), "--", "configs", "list")
    finally:
        package.chmod(0o600)

    assert result.returncode != 0
    assert family(result) == "package-unusable", result.stderr
    assert canary not in result.unredacted()


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def _stage_directory(record: Path) -> Path:
    return Path(staged(record)["source"]).parent


@pytest.mark.parametrize("status", ["0", "1"], ids=["success", "failure"])
def test_the_staged_copy_is_removed_however_the_call_ends(
    initialized: Path, shim: Path, tmp_path: Path, status: str
) -> None:
    """A staged package left behind is a declared configuration left on the host."""
    record = tmp_path / "stage.log"
    package = _package(tmp_path)

    run(
        initialized,
        shim,
        "cli",
        "--package",
        str(package),
        "--",
        "configs",
        "list",
        environment={"SHIM_STAGE_RECORD": str(record), "SHIM_CLI_RC": status},
    )

    directory = _stage_directory(record)
    assert not directory.exists(), f"the staged copy survived at {directory}"


def test_the_staged_copy_is_removed_when_the_operator_interrupts(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """Ctrl-C is the ordinary way a long call ends, so it is a path that has to clean up."""
    record = tmp_path / "stage.log"
    package = _package(tmp_path)
    process = subprocess.Popen(  # noqa: S603 -- the entry point under test, with a fixed argv
        [str(initialized / ENTRY_POINT), "cli", "--package", str(package), "--", "configs", "list"],
        start_new_session=True,
        env={
            **os.environ,
            "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
            "SHIM_COMPOSE_VERSION": MINIMUM_COMPOSE,
            "SHIM_STAGE_RECORD": str(record),
            "SHIM_CLI_HANG": "1",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not record.is_file():
            time.sleep(0.2)
        assert record.is_file(), "the interrupted call never staged a package"
        directory = _stage_directory(record)
        assert directory.exists()
        os.killpg(os.getpgid(process.pid), signal.SIGINT)
        process.wait(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)

    assert not directory.exists(), f"an interrupted call left the staged copy at {directory}"


# ---------------------------------------------------------------------------
# The exact candidate image
# ---------------------------------------------------------------------------


def _container_environment(tmp_path: Path) -> dict[str, str]:
    """Every operator input the bundle needs, with the image under test named."""
    image = os.environ.get("INFRAHUB_SYNC_IMAGE", "").strip()
    if not image:
        pytest.skip("INFRAHUB_SYNC_IMAGE names no built image; this gate runs against the candidate artifact")
    secret = tmp_path / "postgres-admin-password"
    secret.write_text("container-administrator-password\n", encoding="utf-8")
    return {
        **CONTRACT_ENVIRONMENT,
        "INFRAHUB_SYNC_IMAGE": image,
        "INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD_FILE": str(secret),
    }


def _cli(arguments: Sequence[str], environment: Mapping[str, str]) -> Captured:
    """One transient CLI container of the candidate image, with no dependency started."""
    return compose(
        ["--profile", "cli", "run", "--rm", "--no-deps", "-T", "sync-cli", *arguments],
        environment=dict(environment),
        env_files=(DEFAULTS_FILE,),
    )


@pytest.mark.docker
def test_the_candidate_image_answers_cli_help_as_its_entrypoint(docker_daemon: None, tmp_path: Path) -> None:
    """The shipped service runs the CLI, in the image the deployment runs."""
    del docker_daemon

    result = _cli(["--help"], _container_environment(tmp_path))

    assert result.returncode == 0, result.output
    assert "configs" in result.stdout, result.output
    assert "runs" in result.stdout, result.output


@pytest.mark.docker
def test_the_cli_container_holds_no_credential_beyond_its_own_api_token(docker_daemon: None, tmp_path: Path) -> None:
    """It talks to the Sync API and to nothing else, so it is given nothing else.

    Read from inside the container it actually ran in, and reported by name: a
    resolved model says what was asked for, and this says what the process got.
    """
    del docker_daemon
    probe = (
        "import json,os;"
        "print(json.dumps(sorted(name for name in os.environ"
        " if any(part in name for part in ('TOKEN','PASSWORD','SECRET','KEY','DATABASE','PREFECT','S3')))))"
    )

    result = _cli(["--", "python", "-c", probe], _container_environment(tmp_path))

    assert result.returncode == 0, result.output
