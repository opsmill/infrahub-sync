"""The driver's own shell functions, executed rather than read.

Most of what this suite can say about a driver that runs on a host it cannot
reach is structural. Some of it need not be: a shell function is a shell
function, and the ones that decide whether a credential list survives, whether a
private directory really went, and whether a signal can speak over a diagnosis
can be lifted out of the driver verbatim and run.

Verbatim is the point. Each test below extracts the exact text of the functions
it names from `clean-host.sh` and runs that text in a real `sh`, so what passes
is the driver's own code and not a paraphrase of it. What the harness supplies is
only the surrounding scaffolding — a working directory, and stubs for the parts
of the driver a lifted function refers to but does not decide.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess  # noqa: S404 - this suite exists to run the driver's own shell
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER = REPO_ROOT / "tests" / "compose" / "clean_host" / "clean-host.sh"


def driver_source() -> str:
    return DRIVER.read_text(encoding="utf-8")


def shell_function(name: str) -> str:
    """Return one shell function's whole text, exactly as the driver declares it."""
    source = driver_source()
    opened = source.index(f"{name}() {{")
    closed = source.index("\n}", opened) + len("\n}")
    return source[opened:closed]


def driver_assignment(name: str) -> str:
    """Return one top-level assignment the driver makes, exactly as it makes it."""
    found = re.search(rf"^{name}=(\S*)$", driver_source(), re.MULTILINE)
    assert found is not None, f"the driver makes no {name} assignment"
    return f"{name}={found.group(1)}"


def run_shell(script: str, *, work: Path, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Run one assembled script under `sh`, with the driver's working directory set."""
    return subprocess.run(  # noqa: S603 - a script this test assembled, under the shell the driver targets
        ["/bin/sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=work,
        check=False,
    )


# ---------------------------------------------------------------------------
# The plaintext list of every credential this run generated
# ---------------------------------------------------------------------------
CANARY = "a-generated-credential-abc123"


def canary_harness(work: Path) -> str:
    """The driver's own canary primitives, with a working directory to use them in."""
    return "\n".join(
        [
            "set -eu",
            f"WORK={work}",
            driver_assignment("CANARIES"),
            driver_assignment("DIAGNOSTIC_CANARIES"),
            shell_function("carries_a_canary"),
            shell_function("discard_canaries"),
        ]
    )


def test_the_credential_list_is_removed_and_its_removal_is_reported(tmp_path: Path) -> None:
    """This file holds every credential the run generated, in plaintext, unredacted.

    The contract forbids a generated credential in retained evidence, and the
    list of them is not exempt from that: it exists to be searched and for
    nothing else. So it goes, and its going is checked — `rm` reports success for
    a great many things it did not remove.
    """
    script = canary_harness(tmp_path) + "\n".join(
        [
            "",
            f'printf "%s\\n" "{CANARY}" > "$CANARIES"',
            f'printf "%s\\n" "{CANARY}" > "$DIAGNOSTIC_CANARIES"',
            "discard_canaries",
            'echo "removed=$?"',
        ]
    )

    answered = run_shell(script, work=tmp_path)

    assert answered.returncode == 0, answered.stderr
    assert "removed=0" in answered.stdout
    assert not (tmp_path / "canaries").exists(), "the row's credential list survives the run"
    assert not (tmp_path / "diagnostic.canaries").exists(), "the diagnostic's credential list survives the run"


def test_a_credential_list_that_could_not_be_removed_is_reported_rather_than_assumed(tmp_path: Path) -> None:
    """A survivor is the leak itself, so the removal cannot be best-effort."""
    # A directory where the file should be: `rm -f` will not take it, which is the
    # shape of every removal that silently did not happen.
    script = f'{canary_harness(tmp_path)}\nmkdir -p "$CANARIES"\ndiscard_canaries || echo "reported"'

    answered = run_shell(script, work=tmp_path)

    assert "reported" in answered.stdout, "a credential list still on the host reads as removed"


def test_the_sweep_primitive_still_finds_a_credential_in_the_bytes_it_is_given(tmp_path: Path) -> None:
    """What makes the removal above meaningful: the list works right up until it goes."""
    script = canary_harness(tmp_path) + "\n".join(
        [
            "",
            f'printf "%s\\n" "{CANARY}" > "$CANARIES"',
            f'printf "some log line with {CANARY} in it\\n" > swept',
            'if carries_a_canary swept "$CANARIES"; then echo "found"; else echo "missed"; fi',
            "printf 'a clean log line\\n' > clean",
            'if carries_a_canary clean "$CANARIES"; then echo "false-positive"; else echo "clear"; fi',
        ]
    )

    answered = run_shell(script, work=tmp_path)

    assert "found" in answered.stdout, "the sweep cannot find a credential that is present"
    assert "clear" in answered.stdout, "the sweep reports a credential in bytes that carry none"


# ---------------------------------------------------------------------------
# The private control directories
# ---------------------------------------------------------------------------
def test_a_control_directory_that_did_not_go_is_reported(tmp_path: Path) -> None:
    """`rm -rf` on a directory it cannot take still exits zero on some hosts.

    The verdict has to come from the directory being gone, because a private
    writable channel left on the host is the thing this removal is about.
    """
    script = "\n".join(
        [
            "set -eu",
            f"WORK={tmp_path}",
            f"PROXY_CONTROL={tmp_path}/row8-control",
            f"ROW6_CONTROL={tmp_path}/row6-control",
            shell_function("discard_control_state"),
            'mkdir -p "$PROXY_CONTROL" "$ROW6_CONTROL"',
            'discard_control_state || echo "reported"',
            '[ -d "$PROXY_CONTROL" ] || echo "proxy-gone"',
            '[ -d "$ROW6_CONTROL" ] || echo "row6-gone"',
        ]
    )

    answered = run_shell(script, work=tmp_path)

    assert answered.returncode == 0, answered.stderr
    assert "proxy-gone" in answered.stdout, "row 8's control directory is left on the host"
    assert "row6-gone" in answered.stdout, "row 6's control directory is left on the host"


def test_control_state_that_was_never_created_is_not_a_failure_to_remove(tmp_path: Path) -> None:
    """A run that failed before either directory existed has nothing to report."""
    script = "\n".join(
        [
            "set -eu",
            f"WORK={tmp_path}",
            "PROXY_CONTROL=",
            "ROW6_CONTROL=",
            shell_function("discard_control_state"),
            'discard_control_state && echo "clean"',
        ]
    )

    answered = run_shell(script, work=tmp_path)

    assert "clean" in answered.stdout, "a run that created no control state is reported as having left some"


# ---------------------------------------------------------------------------
# A signal arriving while the teardown is running
# ---------------------------------------------------------------------------
def test_a_signal_during_the_teardown_cannot_replace_the_status_it_is_reporting(tmp_path: Path) -> None:
    """The diagnosis is already in hand by the time the teardown starts.

    `on_signal` ends the run with a status of its own, which is right before the
    teardown and wrong inside it: a late interrupt would exit over a failure that
    has already been read and report its own number instead.

    Run for real, with the driver's own handler and its own trap statements, and a
    signal delivered while the teardown is in flight.
    """
    marker = tmp_path / "in-teardown"
    script = "\n".join(
        [
            "set -eu",
            "ROW=a-row",
            'fail() { echo "clean-host: $ROW: $1" >&2; exit 1; }',
            'report() { echo "clean-host: $ROW: $1"; }',
            shell_function("on_signal"),
            # The driver's own status handling and signal-dropping, then a window
            # long enough for a signal to land inside it.
            "cleanup() {",
            "    status=$?",
            "    trap '' INT TERM",
            f'    : > "{marker}"',
            "    i=0",
            "    while [ $i -lt 40 ]; do sleep 0.1; i=$((i + 1)); done",
            '    exit "$status"',
            "}",
            *[line for line in driver_source().splitlines() if line.strip().startswith("trap ")],
            'fail "the row this run failed in"',
        ]
    )

    running = subprocess.Popen(  # noqa: S603 - a script this test assembled
        ["/bin/sh", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
    )
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "the teardown never started, so no signal could be delivered inside it"
        os.kill(running.pid, signal.SIGTERM)
        _, errors = running.communicate(timeout=30)
    finally:
        if running.poll() is None:
            running.kill()
            running.communicate()

    assert running.returncode == 1, (
        f"a signal during the teardown replaced the original status with {running.returncode}"
    )
    assert "did not finish its matrix" not in errors, "the signal handler ran during the teardown"


def test_a_signal_before_the_teardown_still_ends_the_run_as_a_failure(tmp_path: Path) -> None:
    """What makes the case above mean something: outside the teardown the handler runs.

    A signal is not a pass. Before the teardown there is no diagnosis to protect,
    so the handler is what gives the run its status.
    """
    script = "\n".join(
        [
            "set -eu",
            "ROW=a-row",
            'fail() { echo "clean-host: $ROW: $1" >&2; exit 1; }',
            'report() { echo "clean-host: $ROW: $1"; }',
            shell_function("on_signal"),
            'cleanup() { status=$?; exit "$status"; }',
            *[line for line in driver_source().splitlines() if line.strip().startswith("trap ")],
            f': > "{tmp_path}/waiting"',
            "i=0",
            "while [ $i -lt 200 ]; do sleep 0.1; i=$((i + 1)); done",
        ]
    )

    running = subprocess.Popen(  # noqa: S603 - a script this test assembled
        ["/bin/sh", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
    )
    try:
        waiting = tmp_path / "waiting"
        deadline = time.monotonic() + 20
        while not waiting.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        os.kill(running.pid, signal.SIGTERM)
        _, errors = running.communicate(timeout=30)
    finally:
        if running.poll() is None:
            running.kill()
            running.communicate()

    assert running.returncode != 0, "a signal left this gate reporting success"
    assert "did not finish its matrix" in errors, "a signalled run says nothing about not having finished"


@pytest.mark.parametrize("handled", ["INT", "TERM"])
def test_each_signal_has_a_handler_of_its_own_rather_than_the_exit_trap(handled: str) -> None:
    """Read from the driver, because the tests above supply their own `cleanup`."""
    assert re.search(rf"^\s*trap 'on_signal \d+' {handled}$", driver_source(), re.MULTILINE), (
        f"{handled} is not given a handler that ends the run with a status of its own"
    )


# ---------------------------------------------------------------------------
# A name is not an identity: `--rm` frees these three the moment they exit
# ---------------------------------------------------------------------------
# Two of this gate's three `docker run` containers use `--rm`, so their names
# stop belonging to this run as soon as they finish — and the third's name is
# free from the moment a `docker run` fails. Anything on the host may then take
# the name, and a teardown that removed it by name would delete state this run
# never made while reporting that it left the host as it found it.
FOREIGN_RUN = "another-runs-identity"
OWNED_RUN = "this-runs-identity"
OWNED_ID = "sha256:0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"


def docker_stub(work: Path, *, inspect: str, status: int = 0, error: str = "") -> Path:
    """A `docker` that answers one inspect the way a host would, and records every call."""
    stub = work / "bin"
    stub.mkdir(exist_ok=True)
    executable = stub / "docker"
    executable.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                f'printf "%s\\n" "$*" >> "{work}/calls"',
                'if [ "$1" = "inspect" ]; then',
                f'    printf "%s" "{error}" >&2',
                f'    printf "%s" "{inspect}"',
                f"    exit {status}",
                "fi",
                "exit 0",
            ]
        ),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return stub


def ownership_harness(work: Path, *, run_identity: str = OWNED_RUN) -> str:
    """The driver's own ownership check and removal, with a `docker` to ask."""
    return "\n".join(
        [
            "set -eu",
            f"WORK={work}",
            f'PATH="{work}/bin:$PATH"',
            f"RUN_IDENTITY={run_identity}",
            driver_assignment("FIXTURE_LABEL"),
            shell_function("owned_fixture_container"),
            shell_function("remove_owned_fixture_container"),
        ]
    )


def calls_made(work: Path) -> list[str]:
    """Every `docker` invocation the script made, in order."""
    recorded = work / "calls"
    return recorded.read_text(encoding="utf-8").splitlines() if recorded.exists() else []


def test_a_name_that_holds_nothing_is_a_clean_teardown(tmp_path: Path) -> None:
    """The usual case for two of the three: the container exited and `--rm` took it.

    Nothing to remove is not a failure to remove, and a teardown that reported
    one would fail every passing run.
    """
    docker_stub(tmp_path, inspect="", status=1, error="Error: No such object: clean-host-row8-x")
    script = f'{ownership_harness(tmp_path)}\nremove_owned_fixture_container clean-host-row8-x && echo "clean"'

    answered = run_shell(script, work=tmp_path)

    assert "clean" in answered.stdout, answered.stderr
    assert not [call for call in calls_made(tmp_path) if call.startswith("rm ")], (
        "a name that holds nothing was still removed"
    )


def test_a_name_another_container_has_taken_is_preserved_and_reported(tmp_path: Path) -> None:
    """This is the whole finding: `--rm` freed the name and something else took it.

    Removing it would be this gate destroying state it did not create — the exact
    thing rows 9 and 11 exist to say it does not do.
    """
    docker_stub(tmp_path, inspect=f"{OWNED_ID} {FOREIGN_RUN}")
    script = f'{ownership_harness(tmp_path)}\nremove_owned_fixture_container clean-host-row8-x || echo "reported"'

    answered = run_shell(script, work=tmp_path)

    assert "reported" in answered.stdout, "a recycled name reads as removed"
    assert not [call for call in calls_made(tmp_path) if call.startswith("rm ")], (
        "a container this run did not create was removed"
    )


def test_a_name_holding_a_container_with_no_label_at_all_is_preserved(tmp_path: Path) -> None:
    """An unlabelled container is every container on the host that predates this run."""
    docker_stub(tmp_path, inspect=f"{OWNED_ID} ")
    script = f'{ownership_harness(tmp_path)}\nremove_owned_fixture_container clean-host-row8-x || echo "reported"'

    answered = run_shell(script, work=tmp_path)

    assert "reported" in answered.stdout, "an unlabelled container reads as this run's"
    assert not [call for call in calls_made(tmp_path) if call.startswith("rm ")]


def test_a_container_this_run_labelled_is_removed_by_its_own_identifier(tmp_path: Path) -> None:
    """Removed by the identifier the ownership check read, never by the name.

    Between reading the name and removing it the name can move again. The
    identifier cannot: it is the container the check actually looked at.
    """
    docker_stub(tmp_path, inspect=f"{OWNED_ID} {OWNED_RUN}")
    script = f'{ownership_harness(tmp_path)}\nremove_owned_fixture_container clean-host-row8-x && echo "removed"'

    answered = run_shell(script, work=tmp_path)

    assert "removed" in answered.stdout, answered.stderr
    removals = [call for call in calls_made(tmp_path) if call.startswith("rm ")]
    assert removals, "the container this run created was not removed"
    assert OWNED_ID in removals[0], f"the removal named something other than the identifier: {removals[0]}"
    assert "clean-host-row8-x" not in removals[0], "the removal named the container by a name that can move"


def test_a_host_that_could_not_answer_is_not_an_absent_container(tmp_path: Path) -> None:
    """`docker inspect` exits non-zero for an absent name and for a daemon it cannot reach.

    Read as absence, the second one reports a clean host nobody measured — and it
    is the case where something of this run's is most likely still running.
    """
    docker_stub(tmp_path, inspect="", status=1, error="Cannot connect to the Docker daemon")
    script = f'{ownership_harness(tmp_path)}\nremove_owned_fixture_container clean-host-row8-x || echo "reported"'

    answered = run_shell(script, work=tmp_path)

    assert "reported" in answered.stdout, "a question this host declined reads as nothing being there"
    assert not [call for call in calls_made(tmp_path) if call.startswith("rm ")]


def test_a_run_with_no_identity_of_its_own_owns_nothing(tmp_path: Path) -> None:
    """A run that failed before it took an identity cannot match any label.

    Without this, an empty identity would match an empty label and the teardown
    would remove whichever unlabelled container happened to hold the name.
    """
    docker_stub(tmp_path, inspect=f"{OWNED_ID} ")
    script = "\n".join(
        [
            ownership_harness(tmp_path, run_identity=""),
            'remove_owned_fixture_container clean-host-row8-x || echo "reported"',
        ]
    )

    answered = run_shell(script, work=tmp_path)

    assert "reported" in answered.stdout, "a run with no identity claimed a container anyway"
    assert not [call for call in calls_made(tmp_path) if call.startswith("rm ")]


def test_ownership_and_identity_are_read_in_one_question(tmp_path: Path) -> None:
    """Two questions leave a window between them, and the name can move inside it."""
    docker_stub(tmp_path, inspect=f"{OWNED_ID} {OWNED_RUN}")
    script = f"{ownership_harness(tmp_path)}\nremove_owned_fixture_container clean-host-row8-x"

    run_shell(script, work=tmp_path)

    asked = [call for call in calls_made(tmp_path) if call.startswith("inspect ")]
    assert len(asked) == 1, f"the name is inspected {len(asked)} times, and it can move between them"
    assert ".Id" in asked[0], "the one question does not ask what the name holds"
    assert "FIXTURE_LABEL" not in asked[0], "the label is asked for by name rather than expanded"
    assert "io.infrahub-sync.clean-host-fixture" in asked[0], "the one question does not ask whose it is"
