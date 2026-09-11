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
import re
import shutil
import signal
import subprocess  # noqa: S404 -- an interrupted run needs the process, not its finished output
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.compose.conftest import (
    BINDING_CONFIG_DIGEST,
    BINDING_FILE,
    BINDING_INDEX_REFERENCE,
    BINDING_MANIFEST_DIGEST,
    BUNDLE,
    instance_setting,
    write_binding,
)
from tests.compose.redaction import SECRETS, Captured, capture

if TYPE_CHECKING:
    from collections.abc import Mapping

ENTRY_POINT = "infrahub-sync-compose"

# The lowest Compose the bundle is qualified against, restated here rather than
# parsed out of the script: a test that reads the value it checks would accept
# any value the script happened to hold.
MINIMUM_COMPOSE = "2.17.3"

# The source credentials the worker resolves. An operator sets these in the file
# the bundle owns, so an exported shell value of the same name must not reach
# Compose -- the same rule every other interpolated setting already follows.
SOURCE_TOKEN_SETTINGS = ("NETBOX_TOKEN", "NAUTOBOT_TOKEN")

DOCKER_SHIM = r"""#!/bin/sh
# A Docker stand-in for the preflight suite. It answers exactly the questions the
# entry point asks and refuses anything else loudly, so a new question added to
# preflight cannot pass unnoticed -- and so a reintroduced unbounded `docker ps`
# scan fails here rather than quietly working on the developer's machine.

if [ "$1" = "compose" ]; then
    [ -n "${SHIM_ARGV_LOG:-}" ] && printf '%s\n' "$*" >> "$SHIM_ARGV_LOG"
    # Which Compose subcommand, ignoring the global flags in front of it.
    sub=""
    for word in "$@"; do
        case "$word" in
            version | ps | run | logs | stop | restart | down) sub=$word; break ;;
        esac
    done
    if [ "$sub" != "version" ] && [ "${SHIM_REQUIRE_CLEAN_COMPOSE_ENVIRONMENT:-}" = "1" ]; then
        guarded="INFRAHUB_SYNC_IMAGE INFRAHUB_SYNC_INSTANCE INFRAHUB_SYNC_API_PORT"
        guarded="$guarded NETBOX_TOKEN NAUTOBOT_TOKEN"
        for name in $guarded; do
            eval "value=\${$name-}"
            if [ -n "$value" ]; then
                printf 'docker shim: ambient %s reached Compose\n' "$name" >&2
                exit 96
            fi
        done
    fi
    case "$sub" in
        version)
            printf '%s\n' "${SHIM_COMPOSE_VERSION}"
            exit 0
            ;;
        ps)
            # The one container of a named service, or nothing.
            [ "${SHIM_COMPOSE_PS_RC:-0}" = "0" ] || exit "${SHIM_COMPOSE_PS_RC}"
            printf '%s\n' "${SHIM_OWNED_CONTAINER:-}"
            exit 0
            ;;
        run)
            # Two callers: preflight's destination probe, and the worker-state
            # query `status` and `restart` read a deployment's readiness from.
            printf '%s\n' "${SHIM_WORKER_STATE:-}"
            exit "${SHIM_DESTINATION_RC:-0}"
            ;;
        logs | stop | restart | down)
            # The lifecycle calls that resolve no image. Answered rather than
            # refused so a row can record the argument chain each one carried.
            exit 0
            ;;
    esac
    printf 'docker shim: unexpected compose call: %s\n' "$*" >&2
    exit 97
fi

case "$1 $2" in
    "image inspect")
        # Which reference, and whether a format was asked for. Both matter: the
        # binding is resolved by asking about one encoding and then the other,
        # and the architecture of whichever resolved is read the same way.
        shift 2
        format=''
        reference=''
        while [ $# -gt 0 ]; do
            case "$1" in
                --format) format=${2:-}; shift 2 ;;
                *) reference=$1; shift ;;
            esac
        done
        [ -z "${SHIM_INSPECT_LOG:-}" ] || printf '%s\n' "$reference" >> "$SHIM_INSPECT_LOG"
        [ "${SHIM_IMAGE_RESOLVES:-0}" = "0" ] || exit "${SHIM_IMAGE_RESOLVES}"
        # Unset means every reference resolves, which is what every row that is
        # not about selection wants. Set means exactly these do.
        if [ -n "${SHIM_RESOLVABLE+set}" ]; then
            held=0
            for candidate in ${SHIM_RESOLVABLE:-}; do
                [ "$candidate" = "$reference" ] && held=1
            done
            [ "$held" = "1" ] || exit 1
        fi
        case "$format" in
            *Architecture*) printf '%s\n' "${SHIM_ARCHITECTURE:-amd64}" ;;
        esac
        exit 0
        ;;
    "manifest inspect")
        # Recorded, because the binding is a local-only question: a row proves a
        # refusal happened without a registry ever having been asked.
        [ -z "${SHIM_MANIFEST_LOG:-}" ] || printf '%s\n' "${3:-}" >> "$SHIM_MANIFEST_LOG"
        exit "${SHIM_MANIFEST_RESOLVES:-${SHIM_IMAGE_RESOLVES:-0}}"
        ;;
    "container inspect" | "volume inspect" | "network inspect")
        printf '%s\n' "${SHIM_OWNED_LABEL:-}"
        exit 0
        ;;
esac

case "$1" in
    inspect)
        # The health of one named container -- `docker inspect` with no object
        # noun in front of it -- which is what a required dependency is judged by.
        printf '%s\n' "${SHIM_HEALTH:-}"
        exit 0
        ;;
    ps)
        [ "${SHIM_DOCKER_PS_RC:-97}" = "0" ] || exit "${SHIM_DOCKER_PS_RC:-97}"
        printf '%s\n' "${SHIM_RUNNING_CONTAINERS:-}"
        exit 0
        ;;
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
    """A private copy of the shipped bundle, with the member a release generates.

    The repository tracks no binding — it is derived from a candidate's digests
    and only ever exists inside an archive — so an extracted bundle is what this
    reproduces: the committed tree plus that one generated member.
    """
    copy = tmp_path / "compose"
    shutil.copytree(BUNDLE, copy)
    write_binding(copy)
    return copy


@pytest.fixture
def unbound(tmp_path: Path) -> Path:
    """A copy of the committed tree with no binding member at all."""
    copy = tmp_path / "unbound"
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


def run(
    bundle: Path, shim: Path, command: str, *arguments: str, environment: Mapping[str, str] | None = None
) -> Captured:
    """Run one lifecycle command with Docker shimmed out.

    Through the redaction boundary, like every other retained stream in this
    suite: the entry point prints Compose's own output, and a refusal is exactly
    what gets rendered into a failure message.
    """
    return capture(
        [str(bundle / ENTRY_POINT), command, *arguments],
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
    """A bundle that has been through `init` and needs no operator edit to start.

    Nothing is filled in afterwards any more. `init` generates every credential
    the deployment needs and the archive already names the image, so what an
    operator has after this is a deployment that starts.
    """
    created = run(bundle, shim, "init")
    assert created.returncode == 0, created.stderr
    return bundle


def recorded(log: Path) -> list[str]:
    """Return the lines a shim log holds; the shim creates one only when it writes."""
    return log.read_text(encoding="utf-8").splitlines() if log.is_file() else []


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


def test_the_instance_state_file_holds_the_identity_and_the_image_and_nothing_else(bundle: Path, shim: Path) -> None:
    """It is non-secret, and closing its content is what keeps it one.

    Asserting that some particular credential is absent would pass for any file
    holding a different one. The whole file is these two assignments, so any
    addition — a credential, a path, an endpoint — fails here. Both are labels:
    an instance identity and the image the package names.
    """
    run(bundle, shim, "init")

    lines = (bundle / ".instance").read_text(encoding="utf-8").splitlines()

    assert [line.partition("=")[0] for line in lines] == ["INFRAHUB_SYNC_INSTANCE", "INFRAHUB_SYNC_IMAGE"], lines
    identity = instance_setting(bundle, "INFRAHUB_SYNC_INSTANCE")
    assert len(identity) >= 16, identity
    assert all(character in "0123456789abcdef" for character in identity), identity


# ---------------------------------------------------------------------------
# The package-generated image binding
# ---------------------------------------------------------------------------
# The archive names the image it was qualified against. An operator never copies
# a digest, and there is no setting of theirs that could name a different one.


def test_the_generated_settings_ask_the_operator_for_no_image_at_all(bundle: Path, shim: Path) -> None:
    """The whole reason the binding exists: one fewer thing to get wrong.

    Both halves. The operator file must not name the setting — an assignment
    there would read as something to fill in — and it must not tell them to.
    """
    created = run(bundle, shim, "init")

    lines = (bundle / "operator.env").read_text(encoding="utf-8").splitlines()

    assert [line for line in lines if line.startswith("INFRAHUB_SYNC_IMAGE=")] == [], lines
    assert [line for line in lines if "REPLACE-ME" in line] == [], lines
    assert "INFRAHUB_SYNC_IMAGE" not in created.stdout, created.stdout


def test_init_copies_the_binding_index_reference_into_the_generated_state(bundle: Path, shim: Path) -> None:
    """Compose interpolates that name before anything has resolved an image.

    Without it, `init` followed by `status`, `logs`, `stop` or `reset` would meet
    a raw interpolation refusal from Compose rather than this bundle's own answer.
    """
    run(bundle, shim, "init")

    assert instance_setting(bundle, "INFRAHUB_SYNC_IMAGE") == BINDING_INDEX_REFERENCE


def test_init_asks_docker_nothing(bundle: Path, shim: Path, tmp_path: Path) -> None:
    """`init` is the one command a host runs before it has the image.

    Resolving anything here would make the first command of the procedure
    require a daemon, a loaded candidate, or both.
    """
    log = tmp_path / "inspected.log"

    created = run(bundle, shim, "init", environment={"SHIM_INSPECT_LOG": str(log), "SHIM_RESOLVABLE": ""})

    assert created.returncode == 0, created.stderr
    assert not log.exists(), log.read_text(encoding="utf-8")
    assert instance_setting(bundle, "INFRAHUB_SYNC_IMAGE") == BINDING_INDEX_REFERENCE


def test_a_repeated_init_keeps_the_image_a_resolution_already_selected(initialized: Path, shim: Path) -> None:
    """A second `init` must not undo the encoding this host proved it can run."""
    passed = run(initialized, shim, "preflight", environment={"SHIM_RESOLVABLE": BINDING_CONFIG_DIGEST})
    assert passed.returncode == 0, passed.stderr
    assert instance_setting(initialized, "INFRAHUB_SYNC_IMAGE") == BINDING_CONFIG_DIGEST

    repeated = run(initialized, shim, "init")

    assert repeated.returncode == 0, repeated.stderr
    assert instance_setting(initialized, "INFRAHUB_SYNC_IMAGE") == BINDING_CONFIG_DIGEST


def test_a_bundle_with_no_binding_is_refused(unbound: Path, shim: Path) -> None:
    """An archive that lost its record names no image, and cannot be made to guess one."""
    result = run(unbound, shim, "init")

    assert family(result) == "image-binding-missing", result.stderr


def test_a_binding_nothing_can_read_is_refused(unbound: Path, shim: Path) -> None:
    """Unreadable is not empty: a record that cannot be read says nothing.

    Mode bits are the predicate under test, because they are what `[ -r ]` in
    the entry point reads. That makes this a non-root check: root ignores them,
    so under a root pytest the record stays readable and this row proves
    nothing about it. The suite's own entry points and CI both run as an
    ordinary user, and a dangling symlink is not a substitute -- it is an
    absent file, which the row above this one already covers.
    """
    (unbound / BINDING_FILE).write_text("INFRAHUB_SYNC_IMAGE_INDEX=x\n", encoding="utf-8")
    (unbound / BINDING_FILE).chmod(0o000)
    try:
        result = run(unbound, shim, "init")
    finally:
        (unbound / BINDING_FILE).chmod(0o644)

    assert family(result) == "image-binding-missing", result.stderr


@pytest.mark.parametrize(
    "dropped",
    [
        "INFRAHUB_SYNC_IMAGE_PLATFORM",
        "INFRAHUB_SYNC_IMAGE_INDEX",
        "INFRAHUB_SYNC_IMAGE_MANIFEST",
        "INFRAHUB_SYNC_IMAGE_CONFIG",
    ],
)
def test_an_incomplete_binding_is_refused(unbound: Path, shim: Path, dropped: str) -> None:
    """Each of the four settings is load-bearing, so each absence is its own refusal."""
    write_binding(unbound, **{dropped: None})

    result = run(unbound, shim, "init")

    assert family(result) == "image-binding-invalid", result.stderr


@pytest.mark.parametrize(
    "reference",
    ["infrahub-sync:latest", "ghcr.io/opsmill/infrahub-sync:3.0.0", "sha256:short", "sha256:" + "z" * 64],
)
def test_a_binding_naming_a_mutable_or_malformed_reference_is_refused(
    unbound: Path, shim: Path, reference: str
) -> None:
    """A tag can be re-pointed between qualification and the run that trusts it.

    The check that used to read an operator's setting now reads the record, and
    what it refuses is unchanged: anything that is not an immutable digest.
    """
    write_binding(unbound, INFRAHUB_SYNC_IMAGE_CONFIG=reference)

    result = run(unbound, shim, "init")

    assert family(result) == "image-binding-invalid", result.stderr


@pytest.mark.parametrize("reference", ["infrahub-sync:latest", "sha256:short", "sha256:" + "z" * 64])
def test_a_binding_naming_a_malformed_manifest_digest_is_refused(unbound: Path, shim: Path, reference: str) -> None:
    """The third identity is checked by the same grammar as the other two.

    It is derived from the archive's bytes rather than copied from an engine,
    so a value that is not a digest means the record was generated wrong, and
    resolving it would be this script guessing what it should have been.
    """
    write_binding(unbound, INFRAHUB_SYNC_IMAGE_MANIFEST=reference)

    result = run(unbound, shim, "init")

    assert family(result) == "image-binding-invalid", result.stderr


def test_a_binding_naming_a_platform_the_release_did_not_qualify_is_refused(unbound: Path, shim: Path) -> None:
    """The qualified lifecycle platform is one, and the record has to say which."""
    write_binding(unbound, INFRAHUB_SYNC_IMAGE_PLATFORM="linux/arm64")

    result = run(unbound, shim, "init")

    assert family(result) == "image-binding-invalid", result.stderr


def test_a_state_image_the_record_does_not_name_is_refused(initialized: Path, shim: Path) -> None:
    """The state file is generated, and an edit to it is not a third image channel."""
    identity = instance_setting(initialized, "INFRAHUB_SYNC_INSTANCE")
    (initialized / ".instance").write_text(
        f"INFRAHUB_SYNC_INSTANCE={identity}\nINFRAHUB_SYNC_IMAGE=sha256:{'d' * 64}\n", encoding="utf-8"
    )

    result = run(initialized, shim, "preflight")

    assert family(result) == "image-binding-mismatch", result.stderr


def test_a_state_image_naming_the_manifest_identity_is_accepted(initialized: Path, shim: Path) -> None:
    """What a host resolved once is what it keeps, and all three are the record's own.

    A containerd-store host settles on the manifest identity, so the check that
    guards the state file has to recognise it as the package's image rather
    than as something an edit introduced.
    """
    identity = instance_setting(initialized, "INFRAHUB_SYNC_INSTANCE")
    (initialized / ".instance").write_text(
        f"INFRAHUB_SYNC_INSTANCE={identity}\nINFRAHUB_SYNC_IMAGE={BINDING_MANIFEST_DIGEST}\n", encoding="utf-8"
    )

    result = run(initialized, shim, "preflight", environment={"SHIM_RESOLVABLE": BINDING_MANIFEST_DIGEST})

    assert result.returncode == 0, result.stderr + result.stdout
    assert instance_setting(initialized, "INFRAHUB_SYNC_IMAGE") == BINDING_MANIFEST_DIGEST


def test_the_index_encoding_is_selected_when_this_host_holds_the_original_export(
    initialized: Path, shim: Path, tmp_path: Path
) -> None:
    """Index first: a host that kept the exported layout runs the artifact it holds."""
    manifests = tmp_path / "manifest.log"

    result = run(
        initialized,
        shim,
        "preflight",
        environment={"SHIM_RESOLVABLE": BINDING_INDEX_REFERENCE, "SHIM_MANIFEST_LOG": str(manifests)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert instance_setting(initialized, "INFRAHUB_SYNC_IMAGE") == BINDING_INDEX_REFERENCE
    assert not manifests.exists(), manifests.read_text(encoding="utf-8")


def test_the_index_wins_when_this_host_holds_every_encoding(initialized: Path, shim: Path) -> None:
    """Order is the property, and only a host holding all three can observe it.

    Each of the rows beside this one makes exactly one encoding resolvable, so
    every one of them passes just as happily with the order reversed. This is
    the case that fails when it is: the index is what the record names first,
    and what a host that kept the original export should run.
    """
    result = run(
        initialized,
        shim,
        "preflight",
        environment={"SHIM_RESOLVABLE": f"{BINDING_INDEX_REFERENCE} {BINDING_MANIFEST_DIGEST} {BINDING_CONFIG_DIGEST}"},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert instance_setting(initialized, "INFRAHUB_SYNC_IMAGE") == BINDING_INDEX_REFERENCE


def test_the_manifest_encoding_is_selected_when_a_containerd_store_loaded_the_archive(
    initialized: Path, shim: Path, tmp_path: Path
) -> None:
    """Docker's containerd image store names a loaded archive by neither other identity.

    It synthesizes a manifest for an archive that carries none and keeps that
    digest as the image ID, so this is the only one of the three such a host
    resolves -- and the state file has to end up naming it.
    """
    manifests = tmp_path / "manifest.log"

    result = run(
        initialized,
        shim,
        "preflight",
        environment={"SHIM_RESOLVABLE": BINDING_MANIFEST_DIGEST, "SHIM_MANIFEST_LOG": str(manifests)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert instance_setting(initialized, "INFRAHUB_SYNC_IMAGE") == BINDING_MANIFEST_DIGEST
    assert not manifests.exists(), manifests.read_text(encoding="utf-8")


def test_the_manifest_wins_over_the_configuration_digest(initialized: Path, shim: Path) -> None:
    """A host holding both is running a containerd store, whose own ID is the manifest.

    The configuration digest resolving there says the classic store still holds
    a copy, not that it is what this engine will run.
    """
    result = run(
        initialized,
        shim,
        "preflight",
        environment={"SHIM_RESOLVABLE": f"{BINDING_MANIFEST_DIGEST} {BINDING_CONFIG_DIGEST}"},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert instance_setting(initialized, "INFRAHUB_SYNC_IMAGE") == BINDING_MANIFEST_DIGEST


def test_the_configuration_encoding_is_selected_when_the_index_is_absent(
    initialized: Path, shim: Path, tmp_path: Path
) -> None:
    """A classic `docker load` of the candidate leaves the configuration digest and no index."""
    manifests = tmp_path / "manifest.log"

    result = run(
        initialized,
        shim,
        "preflight",
        environment={"SHIM_RESOLVABLE": BINDING_CONFIG_DIGEST, "SHIM_MANIFEST_LOG": str(manifests)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert instance_setting(initialized, "INFRAHUB_SYNC_IMAGE") == BINDING_CONFIG_DIGEST
    assert not manifests.exists(), manifests.read_text(encoding="utf-8")


def test_a_registry_is_never_asked_to_settle_the_binding(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """A registry answering says nothing about what this host can run.

    The refusal has to happen with a reachable registry that would gladly have
    resolved the reference, or the local-only rule is only being observed by
    accident on a disconnected machine.
    """
    manifests = tmp_path / "manifest.log"

    result = run(
        initialized,
        shim,
        "preflight",
        environment={
            "SHIM_RESOLVABLE": "",
            "SHIM_MANIFEST_RESOLVES": "0",
            "SHIM_MANIFEST_LOG": str(manifests),
        },
    )

    assert family(result) == "image-unresolvable", result.stderr
    assert not manifests.exists(), manifests.read_text(encoding="utf-8")


def test_the_unresolvable_refusal_names_every_identity_the_record_holds(initialized: Path, shim: Path) -> None:
    """An operator whose host holds none of them has to be told what to look for.

    Three identities and one archive to load: the message carries the values
    themselves, because a host that resolves none of them is exactly the host
    that cannot be asked to print them.
    """
    result = run(initialized, shim, "preflight", environment={"SHIM_RESOLVABLE": ""})

    assert family(result) == "image-unresolvable", result.stderr
    for value in (BINDING_INDEX_REFERENCE, BINDING_MANIFEST_DIGEST, BINDING_CONFIG_DIGEST):
        assert value in result.stderr, result.stderr
    assert "image-linux-amd64.tar" in result.stderr, result.stderr


def test_a_resolved_image_of_another_architecture_is_refused_rather_than_run(initialized: Path, shim: Path) -> None:
    """An arm64 image under the amd64 record is not a fallback, it is the wrong image."""
    result = run(
        initialized,
        shim,
        "preflight",
        environment={"SHIM_RESOLVABLE": BINDING_CONFIG_DIGEST, "SHIM_ARCHITECTURE": "arm64"},
    )

    assert family(result) == "image-platform-unqualified", result.stderr


def test_resolving_twice_leaves_the_same_state(initialized: Path, shim: Path) -> None:
    """A preflight is repeated on every start, so its state update has to settle."""
    first = run(initialized, shim, "preflight", environment={"SHIM_RESOLVABLE": BINDING_CONFIG_DIGEST})
    assert first.returncode == 0, first.stderr
    settled = (initialized / ".instance").read_text(encoding="utf-8")

    second = run(initialized, shim, "preflight", environment={"SHIM_RESOLVABLE": BINDING_CONFIG_DIGEST})

    assert second.returncode == 0, second.stderr
    assert (initialized / ".instance").read_text(encoding="utf-8") == settled


def test_the_derived_state_update_changes_nothing_an_operator_owns(initialized: Path, shim: Path) -> None:
    """It persists which encoding resolved, and touches nothing else.

    The operator's settings and the mounted credential are theirs; the instance
    identity is what every volume this deployment created is labelled with.
    """
    before = (
        (initialized / "operator.env").read_bytes(),
        (initialized / "secrets" / "postgres-admin-password").read_bytes(),
        instance_setting(initialized, "INFRAHUB_SYNC_INSTANCE"),
    )

    result = run(initialized, shim, "preflight", environment={"SHIM_RESOLVABLE": BINDING_CONFIG_DIGEST})

    assert result.returncode == 0, result.stderr
    assert (
        (initialized / "operator.env").read_bytes(),
        (initialized / "secrets" / "postgres-admin-password").read_bytes(),
        instance_setting(initialized, "INFRAHUB_SYNC_INSTANCE"),
    ) == before


# A `grep` stand-in for the two rows below. The entry point filters the old
# state through `grep` to replace one of its settings, and those two rows are
# about what happens when that filtering does not finish: a read that fails, and
# a run interrupted while it is in progress. Neither answer can be produced on
# demand by the real `grep`, and both decide whether the instance identity -- the
# label every volume this deployment created carries -- survives.
#
# Every other call is the real `grep`, including the entry point's own use of it
# to read a container's published ports.
GREP_SHIM = """#!/bin/sh
# Inert unless a row names the file it is interested in, so every other command
# in the same bundle -- `init` above all -- filters through the real `grep`,
# and so does the entry point's own reading of a container's published ports.
state=0
if [ -n "${SHIM_STATE_FILE_NAME:-}" ]; then
    for word in "$@"; do
        case "$word" in
            *"$SHIM_STATE_FILE_NAME") state=1 ;;
        esac
    done
fi
if [ "$state" = "1" ] && [ -n "${SHIM_STATE_GREP_RC:-}" ]; then
    exit "$SHIM_STATE_GREP_RC"
fi
if [ "$state" = "1" ] && [ -n "${SHIM_STATE_GREP_HANG:-}" ]; then
    # Filter as usual, so the scratch this write is building really exists, then
    # stop where an interrupted operator would leave it.
    "REAL_GREP" "$@"
    : > "$SHIM_STATE_GREP_HANG"
    sleep 60
    exit 1
fi
exec "REAL_GREP" "$@"
"""

STATE_FILE_NAME = ".instance"


@pytest.fixture
def state_shim(shim: Path) -> Path:
    """The Docker stand-in directory, with a `grep` that can fail the state read."""
    real = shutil.which("grep")
    assert real, "this suite needs a real grep to defer to"
    executable = shim / "grep"
    executable.write_text(GREP_SHIM.replace("REAL_GREP", real), encoding="utf-8")
    executable.chmod(0o755)
    return shim


def scratch_files(bundle: Path) -> list[Path]:
    """Every private scratch of the state file left behind in a bundle."""
    return sorted(bundle.glob(f"{STATE_FILE_NAME}.next*"))


def test_a_state_file_that_cannot_be_read_is_not_replaced_by_what_could_be_read_of_it(
    initialized: Path, state_shim: Path
) -> None:
    """A failed read is not an empty file, and the identity is what it would cost.

    The setting being replaced is one line of a generated file whose other line
    names this instance. Treating a read failure as "no other lines" writes back
    a state file with the image and no identity, and every volume the deployment
    created is labelled with that identity.
    """
    before = (initialized / STATE_FILE_NAME).read_bytes()

    result = run(
        initialized,
        state_shim,
        "preflight",
        environment={
            "SHIM_RESOLVABLE": BINDING_CONFIG_DIGEST,
            "SHIM_STATE_FILE_NAME": STATE_FILE_NAME,
            "SHIM_STATE_GREP_RC": "2",
        },
    )

    assert family(result) == "path-unreadable", result.stderr
    assert (initialized / STATE_FILE_NAME).read_bytes() == before
    assert scratch_files(initialized) == []


def test_an_interrupted_state_write_leaves_the_old_state_and_no_scratch_beside_it(
    initialized: Path, state_shim: Path, tmp_path: Path
) -> None:
    """Ctrl-C during a write is ordinary, and it must leave the bundle as it was.

    The write is stopped after its private scratch holds the filtered old state
    and before anything is moved into place, which is the whole window the
    scratch exists in. What the bundle has afterwards is the state file it
    started with and nothing else -- a stray scratch beside it is a file no
    later command owns, under a name a second attempt would collide with.

    `SIGKILL` is not covered here and is not claimed anywhere: no handler runs.
    """
    ready = tmp_path / "filtered"
    before = (initialized / STATE_FILE_NAME).read_bytes()
    process = subprocess.Popen(  # noqa: S603 -- the entry point under test, with a fixed argv
        [str(initialized / ENTRY_POINT), "preflight"],
        start_new_session=True,
        env={
            **os.environ,
            "PATH": f"{state_shim}{os.pathsep}{os.environ['PATH']}",
            "SHIM_COMPOSE_VERSION": MINIMUM_COMPOSE,
            "SHIM_RESOLVABLE": BINDING_CONFIG_DIGEST,
            "SHIM_STATE_FILE_NAME": STATE_FILE_NAME,
            "SHIM_STATE_GREP_HANG": str(ready),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not ready.is_file():
            time.sleep(0.2)
        assert ready.is_file(), "the interrupted run never reached the state write"
        os.killpg(os.getpgid(process.pid), signal.SIGINT)
        process.wait(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)

    assert (initialized / STATE_FILE_NAME).read_bytes() == before
    assert scratch_files(initialized) == [], "an interrupted write left a scratch state file behind"


def test_a_resolved_binding_is_not_looked_up_a_second_time(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """One resolution answers it: what resolved, and what architecture it is.

    Asking again after that decides nothing -- the answer is already held -- and
    the repeat is what a registry fallback used to hang off. Counted rather than
    merely checked for absence of a registry call, because a second local lookup
    is the thing that makes a fallback look reasonable to add back.
    """
    inspects = tmp_path / "inspect.log"
    manifests = tmp_path / "manifest.log"

    result = run(
        initialized,
        shim,
        "preflight",
        environment={
            "SHIM_RESOLVABLE": BINDING_CONFIG_DIGEST,
            "SHIM_INSPECT_LOG": str(inspects),
            "SHIM_MANIFEST_LOG": str(manifests),
        },
    )

    assert result.returncode == 0, result.stderr + result.stdout
    # The index and then the manifest, each refused because this host does not
    # hold it, then the configuration digest, and that same digest once more
    # for its architecture.
    assert recorded(inspects) == [
        BINDING_INDEX_REFERENCE,
        BINDING_MANIFEST_DIGEST,
        BINDING_CONFIG_DIGEST,
        BINDING_CONFIG_DIGEST,
    ]
    assert not manifests.exists(), manifests.read_text(encoding="utf-8")


def test_an_operator_named_image_cannot_displace_the_one_that_was_checked(
    initialized: Path, shim: Path, tmp_path: Path
) -> None:
    """The generated layer is last, so an operator edit of this name loses.

    The run passes with the binding's own image while the operator file names
    another, and the recorded chain shows why: Compose reads the generated state
    after the operator's own file.
    """
    settings = initialized / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8") + f"INFRAHUB_SYNC_IMAGE=sha256:{'e' * 64}\n", encoding="utf-8"
    )
    argv_log = tmp_path / "compose-argv.log"

    result = run(
        initialized,
        shim,
        "preflight",
        environment={"SHIM_RESOLVABLE": BINDING_INDEX_REFERENCE, "SHIM_ARGV_LOG": str(argv_log)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert instance_setting(initialized, "INFRAHUB_SYNC_IMAGE") == BINDING_INDEX_REFERENCE
    chain = re.findall(r"--env-file (\S+)", argv_log.read_text(encoding="utf-8"))
    assert [Path(entry).name for entry in chain[:3]] == ["defaults.conf", "operator.env", ".instance"], chain[:3]


def test_init_writes_its_comments_literally_and_runs_nothing_they_name(
    bundle: Path, shim: Path, tmp_path: Path
) -> None:
    """The generated file is written from an interpolating heredoc.

    A comment there that quotes a command in backticks is a command
    substitution: the text vanishes from the file an operator reads, and
    whatever is first on PATH under that name runs as this script's own user.
    Both halves are asserted — the comment survives, and the command was never
    called.
    """
    marker = tmp_path / "restart-was-called"
    injected = shim / "restart"
    injected.write_text(f"#!/bin/sh\n: > {marker}\n", encoding="utf-8")
    injected.chmod(0o755)

    created = run(bundle, shim, "init")

    assert created.returncode == 0, created.stderr
    assert not marker.exists(), "init executed a command named by one of its own comments"
    assert "`restart`" in (bundle / "operator.env").read_text(encoding="utf-8")


def test_every_interpolated_setting_is_removed_from_composes_ambient_environment() -> None:
    """A setting added to the model cannot silently regain shell precedence."""
    script = (BUNDLE / ENTRY_POINT).read_text(encoding="utf-8")
    declared = re.search(r"COMPOSE_SETTINGS='([^']*)'", script)
    assert declared is not None, "the entry point declares no closed Compose environment boundary"
    sanitized = set(declared.group(1).split())
    interpolated = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", (BUNDLE / "compose.yaml").read_text(encoding="utf-8")))

    assert sanitized == interpolated


@pytest.mark.parametrize("setting", SOURCE_TOKEN_SETTINGS)
def test_the_closed_compose_environment_covers_the_source_credentials(setting: str) -> None:
    """Named directly, so the closed set cannot lose one and stay self-consistent.

    The equality above would also pass for a bundle that interpolates neither
    token, which is exactly the state this work leaves behind.
    """
    script = (BUNDLE / ENTRY_POINT).read_text(encoding="utf-8")
    declared = re.search(r"COMPOSE_SETTINGS='([^']*)'", script)
    assert declared is not None

    assert setting in set(declared.group(1).split())


def test_an_ambient_source_token_cannot_reach_compose(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """The operator file owns the value; an exported shell variable does not.

    Both halves together are the property. The operator file declares one token
    while the shell exports a different one under the same name, and the shim
    refuses the run if either name reaches Compose with a value — so a wrapper
    that stopped clearing the environment fails here. The recorded argv then
    shows the value was not merely dropped: Compose is given the three env files
    in the order that lets the operator file supply it.
    """
    settings = initialized / "operator.env"
    settings.write_text(
        settings.read_text(encoding="utf-8") + "NETBOX_TOKEN=preflight-declared-netbox-token-9c2f41\n",
        encoding="utf-8",
    )
    argv_log = tmp_path / "compose-argv.log"

    result = run(
        initialized,
        shim,
        "preflight",
        environment={
            "SHIM_REQUIRE_CLEAN_COMPOSE_ENVIRONMENT": "1",
            "SHIM_ARGV_LOG": str(argv_log),
            "NETBOX_TOKEN": "preflight-ambient-netbox-token-must-lose",
            "NAUTOBOT_TOKEN": "preflight-ambient-nautobot-token-must-lose",
        },
    )

    assert result.returncode == 0, result.stderr + result.stdout
    recorded = argv_log.read_text(encoding="utf-8")
    chain = re.findall(r"--env-file (\S+)", recorded)
    assert [Path(entry).name for entry in chain[:3]] == ["defaults.conf", "operator.env", ".instance"], chain[:3]


@pytest.mark.parametrize("setting", SOURCE_TOKEN_SETTINGS)
def test_init_documents_the_optional_source_credentials(bundle: Path, shim: Path, setting: str) -> None:
    """An operator has to be told the key exists before they can set it.

    Commented, because neither is required: an uncommented empty assignment
    would read like something the deployment needs.
    """
    run(bundle, shim, "init")

    lines = (bundle / "operator.env").read_text(encoding="utf-8").splitlines()
    mentions = [line for line in lines if setting in line]

    assert mentions, f"the generated operator settings never mention {setting}"
    assert all(line.lstrip().startswith("#") for line in mentions), mentions


def test_init_prints_no_value_from_the_settings_it_generates(bundle: Path, shim: Path) -> None:
    """It reports what it wrote, not what it wrote there.

    Compared privately: the assertion reports the names that leaked, never the
    values it searched for.
    """
    result = run(bundle, shim, "init")

    generated = {
        name: value
        for name, _, value in (
            line.partition("=") for line in (bundle / "operator.env").read_text(encoding="utf-8").splitlines()
        )
        if value and not name.lstrip().startswith("#") and len(value) >= 16
    }
    assert SECRETS.leaked(result.unredacted(), generated) == []


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def test_preflight_passes_at_the_minimum_supported_compose(initialized: Path, shim: Path) -> None:
    """The declared minimum has to be one the bundle actually passes on."""
    result = run(initialized, shim, "preflight")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "preflight passed" in result.stdout


def test_ambient_bundle_settings_cannot_override_the_checked_files(initialized: Path, shim: Path) -> None:
    """The files checked by preflight are the same settings Compose executes."""
    result = run(
        initialized,
        shim,
        "preflight",
        environment={
            "SHIM_REQUIRE_CLEAN_COMPOSE_ENVIRONMENT": "1",
            "INFRAHUB_SYNC_IMAGE": "unexpected:latest",
            "INFRAHUB_SYNC_INSTANCE": "foreign-identity",
            "INFRAHUB_SYNC_API_PORT": "9999",
        },
    )

    assert result.returncode == 0, result.stderr + result.stdout


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


def test_preflight_needs_no_declared_configuration_at_all(initialized: Path, shim: Path) -> None:
    """A deployment starts empty, so the example package is not a startup input."""
    (initialized / "configuration" / "qualification.yaml").unlink()

    result = run(initialized, shim, "preflight")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "preflight passed" in result.stdout


@pytest.mark.parametrize(
    "setting",
    [
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
        "\n".join(line for line in text.splitlines() if not line.startswith("INFRAHUB_SYNC_PRODUCT_PASSWORD=")) + "\n",
        encoding="utf-8",
    )

    result = run(initialized, shim, "preflight")

    # Raw, not redacted: the boundary would remove this value from anything
    # rendered, so searching what it returns would pass whether the entry point
    # printed the credential or not. The refusal itself is what is under test.
    assert secret not in result.unredacted()


def test_preflight_passes_on_what_init_alone_produced(bundle: Path, shim: Path) -> None:
    """The archive names the image and `init` generates the rest, so nothing is left to fill in.

    This is the operator-facing claim of the whole binding: extract, `init`,
    `start`. A required setting reintroduced without a generated value fails
    here rather than in a procedure somebody is following.
    """
    created = run(bundle, shim, "init")
    assert created.returncode == 0, created.stderr

    result = run(bundle, shim, "preflight")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "preflight passed" in result.stdout


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
    identity = instance_setting(initialized, "INFRAHUB_SYNC_INSTANCE")

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
    identity = instance_setting(initialized, "INFRAHUB_SYNC_INSTANCE")

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


def test_preflight_reaches_no_destination(initialized: Path, shim: Path, tmp_path: Path) -> None:
    """READY means the Sync-owned dependencies answer; no destination is consulted.

    Both halves: an unreachable destination does not refuse the start, and the
    recorded argv shows no containerized probe was run to ask.
    """
    argv_log = tmp_path / "compose-argv.log"

    result = run(
        initialized,
        shim,
        "preflight",
        environment={"SHIM_DESTINATION_RC": "1", "SHIM_ARGV_LOG": str(argv_log)},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "preflight passed" in result.stdout
    recorded = argv_log.read_text(encoding="utf-8").splitlines() if argv_log.is_file() else []
    # By subcommand: every containerized probe is a `compose run`, and the paths
    # in a recorded line carry the test's own directory names.
    subcommands = [word for line in recorded for word in line.split() if word in {"version", "ps", "run"}]
    assert "run" not in subcommands, recorded


def test_status_refuses_when_docker_cannot_enumerate_the_instance(initialized: Path, shim: Path) -> None:
    """Failure to ask Docker is not evidence that this deployment is stopped."""
    result = run(initialized, shim, "status")

    assert family(result) == "docker-unavailable", result.stderr + result.stdout
    assert "STOPPED" not in result.stdout


def test_status_refuses_when_compose_cannot_inspect_a_required_dependency(initialized: Path, shim: Path) -> None:
    """A later Docker query failure is not degraded service health either."""
    result = run(
        initialized,
        shim,
        "status",
        environment={
            "SHIM_DOCKER_PS_RC": "0",
            "SHIM_RUNNING_CONTAINERS": "cafe1234",
            "SHIM_COMPOSE_PS_RC": "1",
        },
    )

    assert family(result) == "docker-unavailable", result.stderr + result.stdout
    assert "DEGRADED" not in result.stdout


def test_stop_refuses_when_docker_cannot_prove_ownership(initialized: Path, shim: Path) -> None:
    """A destructive command fails closed when its discovery query fails."""
    identity = instance_setting(initialized, "INFRAHUB_SYNC_INSTANCE")

    result = run(initialized, shim, "stop", environment={"SHIM_OWNED_LABEL": identity})

    assert family(result) == "docker-unavailable", result.stderr + result.stdout


# ---------------------------------------------------------------------------
# lifecycle before anything has resolved an image
# ---------------------------------------------------------------------------

# The lifecycle commands that never call `check_image`. Compose interpolates
# `${INFRAHUB_SYNC_IMAGE:?}` on every one of them, including the ones an
# operator reaches before the candidate has been loaded, so `init` copies the
# record's index reference into the generated state to keep them working.
UNCHECKED_LIFECYCLE = ("status", "logs", "stop", "restart")

# What the shim answers so each of those reaches its Compose call: a container
# of this project and its label, the dependency health `status` reads, and the
# worker state `status` and `restart` wait for. `SHIM_RESOLVABLE` holds nothing,
# so any question asked about the image is answered "this host has neither".
UNRESOLVED = {
    "SHIM_RESOLVABLE": "",
    "SHIM_DOCKER_PS_RC": "0",
    "SHIM_RUNNING_CONTAINERS": "cafe1234",
    "SHIM_OWNED_CONTAINER": "cafe1234",
    "SHIM_HEALTH": "healthy",
    "SHIM_WORKER_STATE": "ready",
}


def settings_chain(bundle: Path) -> str:
    """The three settings layers as one Compose call carries them, in order."""
    return " ".join(f"--env-file {bundle / name}" for name in ("defaults.conf", "operator.env", ".instance"))


@pytest.mark.parametrize("command", UNCHECKED_LIFECYCLE)
def test_a_lifecycle_command_names_a_listed_image_before_anything_resolves_one(
    bundle: Path, shim: Path, tmp_path: Path, command: str
) -> None:
    """`init` alone is enough to run these, on a host that holds neither encoding.

    Nothing here resolves, the ambient name is hostile and the guard against it
    is armed, so the generated state is the only place a value can come from --
    and it has to be one of the two the record lists, because an image Compose
    could interpolate but this bundle never qualified is not an answer either.
    """
    argv_log = tmp_path / f"{command}-argv.log"
    inspect_log = tmp_path / f"{command}-inspect.log"
    created = run(bundle, shim, "init", environment=UNRESOLVED)
    assert created.returncode == 0, created.stderr

    result = run(
        bundle,
        shim,
        command,
        environment={
            **UNRESOLVED,
            "SHIM_OWNED_LABEL": instance_setting(bundle, "INFRAHUB_SYNC_INSTANCE"),
            "SHIM_ARGV_LOG": str(argv_log),
            "SHIM_INSPECT_LOG": str(inspect_log),
            "SHIM_REQUIRE_CLEAN_COMPOSE_ENVIRONMENT": "1",
            "INFRAHUB_SYNC_IMAGE": "unexpected-ambient:latest",
        },
    )

    assert result.returncode == 0, result.stderr + result.stdout
    # Not a resolution that happened to succeed: no image question was asked at
    # all, which is what keeps these commands independent of a loaded candidate.
    assert recorded(inspect_log) == []
    calls = recorded(argv_log)
    assert calls, "the command reached Compose not at all"
    for call in calls:
        assert settings_chain(bundle) in call, call
    assert instance_setting(bundle, "INFRAHUB_SYNC_IMAGE") in {BINDING_INDEX_REFERENCE, BINDING_CONFIG_DIGEST}


def test_reset_removes_an_instance_that_has_never_been_started(bundle: Path, shim: Path, tmp_path: Path) -> None:
    """A bundle can be reset straight after `init`, and only on its exact identity.

    This is the one destructive command reachable before a first start -- an
    `init` in the wrong directory, an archive extracted twice -- and it goes
    through Compose like every other lifecycle call, so it needs the same
    recorded image. The wrong identity runs first as the control: a `reset` that
    quietly removed nothing would satisfy the rows after it just as well.
    """
    argv_log = tmp_path / "reset-argv.log"
    inspect_log = tmp_path / "reset-inspect.log"
    created = run(bundle, shim, "init", environment=UNRESOLVED)
    assert created.returncode == 0, created.stderr
    identity = instance_setting(bundle, "INFRAHUB_SYNC_INSTANCE")
    answers = {
        **UNRESOLVED,
        "SHIM_RUNNING_CONTAINERS": "",
        "SHIM_OWNED_LABEL": identity,
        "SHIM_ARGV_LOG": str(argv_log),
        "SHIM_INSPECT_LOG": str(inspect_log),
        "SHIM_REQUIRE_CLEAN_COMPOSE_ENVIRONMENT": "1",
        "INFRAHUB_SYNC_IMAGE": "unexpected-ambient:latest",
    }

    refused = run(bundle, shim, "reset", "not-this-instance", environment=answers)

    assert family(refused) == "confirmation-required", refused.stderr
    assert recorded(argv_log) == [], "a refused reset reached Compose"
    assert instance_setting(bundle, "INFRAHUB_SYNC_IMAGE") in {BINDING_INDEX_REFERENCE, BINDING_CONFIG_DIGEST}

    removed = run(bundle, shim, "reset", identity, environment=answers)

    assert removed.returncode == 0, removed.stderr + removed.stdout
    assert recorded(inspect_log) == []
    assert [call for call in recorded(argv_log) if settings_chain(bundle) in call], recorded(argv_log)
    assert not (bundle / ".instance").exists()
    assert (bundle / "operator.env").is_file()
