"""Preflight refuses each thing it exists to refuse, through the real entry point.

Every case runs `deploy/compose/infrahub-sync-compose` itself against a copy of
the bundle, so what is under test is the shipped script rather than a
restatement of it. Docker is replaced by a shim that answers exactly the
questions preflight asks it — the installed Compose version, whether an image
resolves, which container a named service resolves to and what it publishes and
is labelled with, whether the engine can take a required bind, and whether the
declared destination answered — because those answers are the inputs whose
handling is the point, and a real daemon cannot be made to give the wrong ones
on demand. The Docker-backed suite plants real occupants of a real port; this
one covers the answers a real daemon will not produce to order.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import BUNDLE
from tests.compose.redaction import Captured, capture

if TYPE_CHECKING:
    from collections.abc import Mapping

ENTRY_POINT = "infrahub-sync-compose"

# The lowest Compose the bundle is qualified against, restated here rather than
# parsed out of the script: a test that reads the value it checks would accept
# any value the script happened to hold.
MINIMUM_COMPOSE = "2.17.3"

DOCKER_SHIM = r"""#!/bin/sh
# A Docker stand-in for the preflight suite. It answers exactly the questions the
# entry point asks and refuses anything else loudly, so a new question added to
# preflight cannot pass unnoticed -- and so a reintroduced unbounded `docker ps`
# scan fails here rather than quietly working on the developer's machine.

if [ "$1" = "compose" ]; then
    # Which Compose subcommand, ignoring the global flags in front of it.
    sub=""
    for word in "$@"; do
        case "$word" in
            version | ps | run) sub=$word; break ;;
        esac
    done
    case "$sub" in
        version)
            printf '%s\n' "${SHIM_COMPOSE_VERSION}"
            exit 0
            ;;
        ps)
            # The one container of a named service, or nothing.
            printf '%s\n' "${SHIM_OWNED_CONTAINER:-}"
            exit 0
            ;;
        run)
            # The only `compose run` preflight makes is the destination probe.
            exit "${SHIM_DESTINATION_RC:-0}"
            ;;
    esac
    printf 'docker shim: unexpected compose call: %s\n' "$*" >&2
    exit 97
fi

case "$1 $2" in
    "image inspect" | "manifest inspect")
        exit "${SHIM_IMAGE_RESOLVES:-0}"
        ;;
    "container inspect" | "volume inspect" | "network inspect")
        printf '%s\n' "${SHIM_OWNED_LABEL:-}"
        exit 0
        ;;
esac

case "$1" in
    port)
        # The host bindings of one named container.
        printf '%s\n' "${SHIM_OWNED_PORTS:-}"
        exit 0
        ;;
    rm)
        exit 0
        ;;
    run)
        # The disposable bind probe. With `--publish` it answers whether the
        # engine could take that exact bind; without one it answers whether the
        # probe itself works, which is how a held port is told apart from a
        # broken probe.
        publish=""
        previous=""
        for word in "$@"; do
            [ "$previous" = "--publish" ] && publish=$word
            previous=$word
        done
        if [ -n "$publish" ]; then
            case "$publish" in
                *":${SHIM_HELD_PORT:-no-such-port}:"*) exit 125 ;;
            esac
            exit 0
        fi
        exit "${SHIM_PROBE_BROKEN:-0}"
        ;;
esac
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
    executable.write_text(DOCKER_SHIM, encoding="utf-8")
    executable.chmod(0o755)
    return directory


def run(bundle: Path, shim: Path, command: str, *, environment: Mapping[str, str] | None = None) -> Captured:
    """Run one lifecycle command with Docker shimmed out.

    Through the redaction boundary, like every other retained stream in this
    suite: the entry point prints Compose's own output, and a refusal is exactly
    what gets rendered into a failure message.
    """
    return capture(
        [str(bundle / ENTRY_POINT), command],
        timeout=120,
        env={
            **os.environ,
            "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
            "SHIM_COMPOSE_VERSION": MINIMUM_COMPOSE,
            **(environment or {}),
        },
    )


@pytest.fixture
def initialized(bundle: Path, shim: Path) -> Path:
    """A bundle that has been through `init` and had its two placeholders filled."""
    created = run(bundle, shim, "init")
    assert created.returncode == 0, created.stderr
    settings = bundle / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8")
        .replace("INFRAHUB_SYNC_IMAGE=REPLACE-ME", f"INFRAHUB_SYNC_IMAGE=sha256:{'a' * 64}")
        .replace("INFRAHUB_API_TOKEN=REPLACE-ME", "INFRAHUB_API_TOKEN=preflight-destination-token"),
        encoding="utf-8",
    )
    return bundle


def family(result: Captured) -> str:
    """Return the refusal family a run reported, or '' when it did not refuse."""
    for line in result.stderr.splitlines():
        if line.startswith("infrahub-sync: "):
            return line.removeprefix("infrahub-sync: ").split(":", 1)[0]
    return ""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_generates_an_identity_and_operator_settings(bundle: Path, shim: Path) -> None:
    """Everything an operator owns is generated locally; nothing is shipped."""
    result = run(bundle, shim, "init")

    assert result.returncode == 0, result.stderr
    identity = (bundle / ".instance").read_text(encoding="utf-8")
    assert identity.startswith("INFRAHUB_SYNC_INSTANCE=")
    assert (bundle / "operator.env").is_file()
    assert (bundle / "secrets" / "postgres-admin-password").read_text(encoding="utf-8").strip()


def test_init_keeps_the_identity_and_credentials_it_already_generated(bundle: Path, shim: Path) -> None:
    """A repeated `init` must not orphan the volumes the first one's identity labelled."""
    run(bundle, shim, "init")
    before = ((bundle / ".instance").read_text(), (bundle / "operator.env").read_text())

    run(bundle, shim, "init")

    assert ((bundle / ".instance").read_text(), (bundle / "operator.env").read_text()) == before


def test_the_instance_state_file_holds_the_identity_and_nothing_else(bundle: Path, shim: Path) -> None:
    """It is a non-secret label, and closing its content is what keeps it one.

    Asserting that some particular credential is absent would pass for any file
    holding a different one. The whole file is one assignment, so any addition —
    a credential, a path, an endpoint — fails here.
    """
    run(bundle, shim, "init")

    lines = (bundle / ".instance").read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1, lines
    name, _, value = lines[0].partition("=")
    assert name == "INFRAHUB_SYNC_INSTANCE"
    assert len(value) >= 16, value
    assert all(character in "0123456789abcdef" for character in value), value


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def test_preflight_passes_at_the_minimum_supported_compose(initialized: Path, shim: Path) -> None:
    """The declared minimum has to be one the bundle actually passes on."""
    result = run(initialized, shim, "preflight")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "preflight passed" in result.stdout


@pytest.mark.parametrize("version", ["2.17.2", "2.16.9", "1.29.2"])
def test_preflight_refuses_a_compose_older_than_the_minimum(initialized: Path, shim: Path, version: str) -> None:
    """Below the qualified floor the bundle's own features stop being answerable."""
    result = run(initialized, shim, "preflight", environment={"SHIM_COMPOSE_VERSION": version})

    assert family(result) == "compose-too-old", result.stderr


@pytest.mark.parametrize("version", ["", "v2", "unknown", "2.x.3"])
def test_preflight_refuses_a_compose_version_it_cannot_read(initialized: Path, shim: Path, version: str) -> None:
    """An unreadable version is not a passing one: nothing about the floor is known."""
    result = run(initialized, shim, "preflight", environment={"SHIM_COMPOSE_VERSION": version})

    assert family(result) == "compose-unreadable", result.stderr


def test_preflight_refuses_an_unwritable_bundle_path(initialized: Path, shim: Path) -> None:
    """The deployment writes its own state here; discovering that at teardown is too late."""
    secrets = initialized / "secrets"
    secrets.chmod(0o500)
    try:
        result = run(initialized, shim, "preflight")
    finally:
        secrets.chmod(0o700)

    assert family(result) == "path-unwritable", result.stderr


def test_preflight_refuses_a_declared_configuration_it_cannot_read(initialized: Path, shim: Path) -> None:
    """Bootstrap registers that file; an absent one fails after the stack is up."""
    (initialized / "configuration" / "qualification.yaml").unlink()

    result = run(initialized, shim, "preflight")

    assert family(result) == "path-unreadable", result.stderr


@pytest.mark.parametrize(
    "setting",
    [
        "INFRAHUB_SYNC_IMAGE",
        "INFRAHUB_API_TOKEN",
        "INFRAHUB_SYNC_PRODUCT_PASSWORD",
        "INFRAHUB_SYNC_PREFECT_PASSWORD",
        "INFRAHUB_SYNC_S3_ACCESS_KEY",
        "INFRAHUB_SYNC_S3_SECRET_KEY",
        "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS",
        "INFRAHUB_SYNC_DATABASE_URL",
        "INFRAHUB_SYNC_PREFECT_DATABASE_URL",
    ],
)
def test_preflight_refuses_a_missing_credential_by_name(initialized: Path, shim: Path, setting: str) -> None:
    """Each one is required, and the refusal names the setting and never its value."""
    settings = initialized / "operator.env"
    kept = [line for line in settings.read_text(encoding="utf-8").splitlines() if not line.startswith(f"{setting}=")]
    settings.write_text("\n".join(kept) + "\n", encoding="utf-8")

    result = run(initialized, shim, "preflight")

    assert family(result) == "credentials-missing", result.stderr
    assert setting in result.stderr


def test_a_missing_credential_refusal_renders_no_value(initialized: Path, shim: Path) -> None:
    """It reports which settings have no value, which is not the same as showing them."""
    settings = initialized / "operator.env"
    text = settings.read_text(encoding="utf-8")
    secret = next(
        line.split("=", 1)[1] for line in text.splitlines() if line.startswith("INFRAHUB_SYNC_S3_SECRET_KEY=")
    )
    settings.write_text(
        "\n".join(line for line in text.splitlines() if not line.startswith("INFRAHUB_API_TOKEN=")) + "\n",
        encoding="utf-8",
    )

    result = run(initialized, shim, "preflight")

    # Raw, not redacted: the boundary would remove this value from anything
    # rendered, so searching what it returns would pass whether the entry point
    # printed the credential or not. The refusal itself is what is under test.
    assert secret not in result.unredacted()


def test_preflight_refuses_the_placeholder_init_leaves_behind(bundle: Path, shim: Path) -> None:
    """`init` cannot know the image or the destination token, and says so by refusing."""
    run(bundle, shim, "init")

    result = run(bundle, shim, "preflight")

    assert family(result) == "credentials-missing", result.stderr
    assert "INFRAHUB_SYNC_IMAGE" in result.stderr


@pytest.mark.parametrize(
    "reference",
    ["infrahub-sync:latest", "ghcr.io/opsmill/infrahub-sync:3.0.0", "sha256:short", "sha256:" + "z" * 64],
)
def test_preflight_refuses_a_sync_image_that_is_not_immutable(initialized: Path, shim: Path, reference: str) -> None:
    """A tag can be re-pointed between qualification and the run that trusts it."""
    settings = initialized / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace(
            f"INFRAHUB_SYNC_IMAGE=sha256:{'a' * 64}", f"INFRAHUB_SYNC_IMAGE={reference}"
        ),
        encoding="utf-8",
    )

    result = run(initialized, shim, "preflight")

    assert family(result) == "image-not-immutable", result.stderr


@pytest.mark.parametrize("reference", ["sha256:" + "a" * 64, "ghcr.io/opsmill/infrahub-sync@sha256:" + "b" * 64])
def test_preflight_accepts_both_immutable_image_forms(initialized: Path, shim: Path, reference: str) -> None:
    """The local image ID before publication, and the repository digest after it."""
    settings = initialized / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8").replace(
            f"INFRAHUB_SYNC_IMAGE=sha256:{'a' * 64}", f"INFRAHUB_SYNC_IMAGE={reference}"
        ),
        encoding="utf-8",
    )

    result = run(initialized, shim, "preflight")

    assert result.returncode == 0, result.stderr


def test_preflight_refuses_an_image_docker_cannot_resolve(initialized: Path, shim: Path) -> None:
    """Whether the repository half names anything is Docker's question, and it is asked."""
    result = run(initialized, shim, "preflight", environment={"SHIM_IMAGE_RESOLVES": "1"})

    assert family(result) == "image-unresolvable", result.stderr


@pytest.mark.parametrize("port", ["8000", "4200"])
def test_preflight_refuses_a_required_bind_something_else_holds(initialized: Path, shim: Path, port: str) -> None:
    """Whatever holds it, and however it holds it, the bind is not available.

    The question preflight asks is the one that matters -- can this deployment
    take this exact bind -- and it asks the engine by trying. That answer covers
    a foreign container published on any address, a container of no project at
    all, and a host process that is not a container; a scan of container
    publications covers only the first, and only when it published on loopback.
    """
    result = run(initialized, shim, "preflight", environment={"SHIM_HELD_PORT": port})

    assert family(result) == "port-occupied", result.stderr
    assert port in result.stderr


def test_preflight_accepts_the_publication_this_instance_already_made(initialized: Path, shim: Path) -> None:
    """A repeated start on a running deployment is not a port conflict with itself.

    The bind is genuinely unavailable here -- this instance is holding it -- so
    a probe alone would refuse. What distinguishes the two is the named service
    resolving to a container that carries this instance's label and publishes
    that exact port.
    """
    identity = (initialized / ".instance").read_text(encoding="utf-8").split("=", 1)[1].strip()

    result = run(
        initialized,
        shim,
        "preflight",
        environment={
            "SHIM_HELD_PORT": "8000",
            "SHIM_OWNED_CONTAINER": "cafe1234",
            "SHIM_OWNED_LABEL": identity,
            "SHIM_OWNED_PORTS": "8000/tcp -> 127.0.0.1:8000",
        },
    )

    assert result.returncode == 0, result.stderr


def test_preflight_refuses_a_publication_made_under_another_instance_label(initialized: Path, shim: Path) -> None:
    """A container the project name resolves to is not therefore this instance's.

    Compose would hand back whatever holds that service name. The label is what
    says it was this identity that created it, and without a match the bind is
    treated as any other foreign occupant.
    """
    result = run(
        initialized,
        shim,
        "preflight",
        environment={
            "SHIM_HELD_PORT": "8000",
            "SHIM_OWNED_CONTAINER": "cafe1234",
            "SHIM_OWNED_LABEL": "someone-else",
            "SHIM_OWNED_PORTS": "8000/tcp -> 127.0.0.1:8000",
        },
    )

    assert family(result) == "port-occupied", result.stderr


def test_preflight_refuses_a_publication_of_a_port_it_does_not_need(initialized: Path, shim: Path) -> None:
    """Owning *a* publication is not owning *this* one.

    A container of this instance that publishes some other port says nothing
    about the bind in question, so the probe still has to answer for it.
    """
    identity = (initialized / ".instance").read_text(encoding="utf-8").split("=", 1)[1].strip()

    result = run(
        initialized,
        shim,
        "preflight",
        environment={
            "SHIM_HELD_PORT": "8000",
            "SHIM_OWNED_CONTAINER": "cafe1234",
            "SHIM_OWNED_LABEL": identity,
            "SHIM_OWNED_PORTS": "8000/tcp -> 127.0.0.1:9999",
        },
    )

    assert family(result) == "port-occupied", result.stderr


def test_preflight_refuses_when_it_cannot_prove_the_bind_either_way(initialized: Path, shim: Path) -> None:
    """A probe that cannot run has not found the port free.

    Treating a broken probe as a pass would turn the whole check into something
    that silently stops holding. Treating it as an occupied port would send an
    operator hunting for a conflict that does not exist, so it is its own
    refusal.
    """
    result = run(initialized, shim, "preflight", environment={"SHIM_HELD_PORT": "8000", "SHIM_PROBE_BROKEN": "1"})

    assert family(result) == "port-unprovable", result.stderr


def test_preflight_passes_when_both_required_binds_are_free(initialized: Path, shim: Path) -> None:
    """Nothing holds either one, and no publication has to be recognised."""
    result = run(initialized, shim, "preflight")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "free" in result.stdout


def test_preflight_refuses_a_destination_that_did_not_answer(initialized: Path, shim: Path) -> None:
    """The probe runs in the Sync image; this script only learns whether it passed."""
    result = run(initialized, shim, "preflight", environment={"SHIM_DESTINATION_RC": "1"})

    assert family(result) == "destination-unavailable", result.stderr
