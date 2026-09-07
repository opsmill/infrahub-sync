"""What the clean-host driver must be, read from the driver itself.

The driver runs on a host this suite cannot reach, so what is checkable here is
its shape: that it makes a claim about every mandatory row, that it cannot report
a row it did not run, and that it carries the guards the gate rests on. None of
that is evidence the driver runs — only that it cannot quietly run less than it
says.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER = REPO_ROOT / "tests" / "compose" / "clean_host" / "clean-host.sh"
CHECKS = REPO_ROOT / "tests" / "compose" / "clean_host" / "checks"

# Every row the accepted matrix requires. Written out rather than read from the
# driver, so a row deleted from the driver fails here instead of narrowing the
# list it is checked against.
MANDATORY_ROWS = (
    "artifact_identity",
    "cold_start_and_idempotence",
    "managed_execution",
    "keyed_write_policy",
    "schema_change",
    "status",
    "restart",
    "recovery",
    "ownership_and_reset",
    "alpha_replacement",
    "secrets",
)

# The host tools the gate refuses to reach, and the guards it cannot run without.
REFUSED_HOST_TOOLS = ("python", "python3", "uv", "uvx", "pip", "pytest", "infrahubctl")


def driver() -> str:
    return DRIVER.read_text(encoding="utf-8")


def test_the_driver_is_executable() -> None:
    """A bare host runs this file directly; a mode lost in transit stops the gate."""
    assert DRIVER.stat().st_mode & 0o111


def declared_matrix() -> tuple[str, ...]:
    """Return the rows the driver's own matrix names."""
    declared = re.search(r"^MATRIX='([^']*)'", driver(), re.MULTILINE)
    assert declared is not None, "the driver declares no matrix"
    return tuple(declared.group(1).split())


@pytest.mark.parametrize("row", MANDATORY_ROWS)
def test_the_driver_defines_and_runs_every_mandatory_row(row: str) -> None:
    """A row absent from the matrix is a claim the gate silently stops making."""
    assert f"row_{row}()" in driver(), f"the driver defines no row_{row}"
    assert row in declared_matrix(), f"the driver's matrix omits {row}"


def test_the_matrix_holds_nothing_but_the_mandatory_rows() -> None:
    """Read from the driver, so a row added without a decision fails here."""
    assert declared_matrix() == MANDATORY_ROWS


def executable_lines() -> str:
    """Return the driver with its comments removed, so prose cannot satisfy a check."""
    return "\n".join(line for line in driver().splitlines() if not line.lstrip().startswith("#"))


def test_the_driver_expresses_no_way_to_skip_a_row() -> None:
    """A skip is a failed gate, so unreachability is not something the driver can say.

    Every row either returns or ends the run. There is no continue, no early exit
    reporting success, and no marker a row could set to be passed over.
    """
    body = executable_lines()

    assert "exit 0" not in body
    assert not re.search(r"^\s*continue\b", body, re.MULTILINE)
    assert "skip" not in body.lower()


@pytest.mark.parametrize("tool", REFUSED_HOST_TOOLS)
def test_the_driver_refuses_every_host_tool_by_recording_its_use(tool: str) -> None:
    """Absence is a claim about the host; an invocation marker is a claim about the run."""
    body = driver()

    assert re.search(rf"HOST_TOOLS=.*\b{re.escape(tool)}\b", body, re.DOTALL)
    assert "host-tool-used" in body
    assert "require_no_host_tool_was_used" in body


def test_the_driver_verifies_the_bundle_before_it_extracts_or_edits_it() -> None:
    """The checksum is a claim about the archive, never about the tree afterwards."""
    body = driver()
    verified = body.index("sha256sum -c")
    extracted = body.index("tar -xzf")
    edited = body.index("point_configuration_at_destination")

    assert verified < extracted < edited


def test_every_check_the_driver_runs_is_in_the_kit() -> None:
    """A named check that does not exist fails the row at the host, not here."""
    named = set(re.findall(r"^\s*check ([a-z_]+)", driver(), re.MULTILINE))
    named |= set(re.findall(r"\$\(check ([a-z_]+)", driver()))

    assert named, "the driver runs no checks"
    for name in sorted(named):
        assert (CHECKS / f"{name}.py").is_file(), f"the driver runs {name}, which the kit does not carry"


def test_every_read_of_the_candidate_record_is_checked() -> None:
    """A record read inside an argument discards its own exit status.

    The reader runs in the candidate image, so a read attempted before that image
    is loaded fails -- and a command substitution in an argument position turns
    that failure into an empty string the message carries anyway.
    """
    unchecked = [
        line.strip()
        for line in executable_lines().splitlines()
        if "$(record " in line and not re.match(r"^\s*\w+=\$\(record ", line)
    ]

    assert unchecked == []


def test_the_bundle_root_is_found_rather_than_derived_from_a_filename() -> None:
    """The archive's top-level directory is not part of what the record promises.

    Deriving it from the archive name, or moving it into a directory that already
    exists, puts the entry point somewhere the driver then reports as missing.
    """
    body = executable_lines()

    assert "-name infrahub-sync-compose" in body
    assert "BUNDLE=$(dirname" in body
    # The extracted tree is used where it lands. Relocating it into a directory
    # that already exists nests rather than renames, and the nesting is what put
    # the entry point somewhere the driver then called missing.
    assert not re.search(r"^\s*mv\b.*EXTRACTED", body, re.MULTILINE)
    # Teardown may ignore a failure. Nothing that establishes the bundle may.
    establishing = [line for line in body.splitlines() if re.search(r"\b(tar|find|sha256sum)\b", line)]
    assert establishing
    assert [line for line in establishing if "|| true" in line] == []


def test_an_absent_entry_point_and_an_unexecutable_one_are_reported_apart() -> None:
    """They have different causes, so a single message sends a reader to the wrong one."""
    body = driver()

    assert "holds no lifecycle entry point" in body
    assert "present but not executable" in body


def test_every_refusal_the_driver_captures_is_checked_for_its_reason() -> None:
    """A refusal accepted for being non-zero passes on whichever refusal came first.

    The lifecycle entry point stops at its first failed check, so a probe placed
    before the check it means to exercise is refused for something else entirely
    -- and an assertion that only requires failure cannot tell the difference.
    """
    body = executable_lines()
    captured = set(re.findall(r'>"\$WORK/([a-z-]+-refusal)"', body))

    assert captured
    for name in sorted(captured):
        assert re.search(rf'grep -q "[a-z-]+" "\$WORK/{name}"', body), (
            f"{name} is captured but never checked for the reason it names"
        )


def test_every_kit_file_a_check_reads_is_carried_by_the_kit() -> None:
    """A check reading `/checks/<name>` fails at the host if the kit omits it.

    The kit is assembled by a task with its own idea of what to copy, so the two
    have to be asserted against each other rather than assumed to agree.
    """
    referenced = set()
    for module in sorted(CHECKS.glob("*.py")):
        referenced |= set(re.findall(r"/checks/([A-Za-z0-9_.-]+\.(?:yml|yaml))", module.read_text(encoding="utf-8")))

    assert referenced
    carried = {path.name for path in (CHECKS.parent / "destination").iterdir()}
    carried |= {"infra_device.yml"}
    assert referenced <= carried, f"{sorted(referenced - carried)} are read but not carried"


def test_the_secret_sweep_reads_the_bundle_as_shipped_bytes() -> None:
    """A plaintext search of a gzip stream cannot match, so it cannot fail."""
    body = executable_lines()

    assert "gzip -dc" in body
    assert not re.search(r"swept=.*CANDIDATE/\$bundle_name", body)


def test_no_docker_invocation_takes_an_unguarded_substitution_as_its_subject() -> None:
    """An empty value reaches Docker as a missing argument, not as a refusal.

    A substitution that produced nothing hands Docker an empty container or image
    and the run fails on Docker's own wording rather than on what the driver was
    asking about. Every such value comes from a helper that refuses first.
    """
    guarding = ("deployment_container", "instance_identity", "record", "setting")
    unguarded = []
    for line in executable_lines().splitlines():
        # Only a line that runs Docker directly hands it arguments. A `$(docker …)`
        # inside a test is a query whose output is read, not a value passed on.
        if not re.match(r"\s*docker\s", line):
            continue
        unguarded += [call for call in re.findall(r"\$\(([a-z_]+)", line) if call not in guarding]

    assert unguarded == []


def test_every_docker_query_read_through_a_pipeline_is_checked_for_emptiness() -> None:
    """A pipeline reports its last element's status, so the query's failure is lost.

    `x=$(docker … | filter)` succeeds whenever the filter does, so the only thing
    left to notice a failed query is the emptiness of what it produced.
    """
    lines = executable_lines().splitlines()
    unchecked = []
    for index, line in enumerate(lines):
        found = re.match(r"\s*(\w+)=\$\(docker [^)]*\|", line)
        if not found:
            continue
        name = found.group(1)
        following = " ".join(lines[index + 1 : index + 3])
        if f'[ -n "${name}" ]' not in following:
            unchecked.append(line.strip())

    assert unchecked == []


def test_the_container_helper_refuses_rather_than_returning_nothing() -> None:
    """The case above trusts this helper by name, so the guard has to be inside it.

    Its status comes from the query rather than from the filter that trims the
    result, and an empty result ends the run where it happened instead of
    reaching Docker as a missing argument.
    """
    body = executable_lines()
    opened = body.index("deployment_container() {")
    helper = body[opened : body.index("\n}", opened)]

    assert '> "$WORK/containers"' in helper
    assert "|| fail" in helper
    assert '[ -n "$container" ]' in helper
    assert "| head -1" not in helper


# Everything the driver creates on the host. Written out, so a new creating verb
# fails here until teardown is taught to remove what it makes.
RESOURCE_CREATING = ("compose_bundle start", "destination_compose up", "docker volume create")


def teardown_body() -> str:
    body = executable_lines()
    opened = body.index("cleanup() {")
    return body[opened : body.index("\n}", opened)]


def test_the_driver_creates_no_resource_its_teardown_does_not_know_about() -> None:
    """The asymmetry to design against: one creating path torn down and another not."""
    body = executable_lines()
    found = {verb for verb in RESOURCE_CREATING if verb in body}

    assert found == set(RESOURCE_CREATING)
    for verb, removal in (
        ("compose_bundle start", 'compose_bundle reset "$INSTANCE"'),
        ("destination_compose up", "stop_destination"),
        ("docker volume create", 'docker volume rm "$FOREIGN_VOLUME"'),
    ):
        assert verb in body
        assert removal in teardown_body(), f"{verb} has no matching removal in teardown"


def test_the_teardown_reaches_only_what_this_run_owns() -> None:
    """A prefix sweep would take deployments this run never made.

    This host carries unrelated `infrahub-sync-*` containers, so every removal is
    named by the identity the run generated rather than matched by name.
    """
    body = executable_lines()

    assert "system prune" not in body
    for line in teardown_body().splitlines():
        if "--filter" in line or "--project-name" in line:
            assert "$INSTANCE" in line, f"teardown reaches beyond this run: {line.strip()}"


def test_the_teardown_preserves_the_failure_that_caused_it() -> None:
    """A cleanup that swallows the diagnosis is worse than one that leaves containers."""
    body = teardown_body()

    assert "status=$?" in body
    assert 'exit "$status"' in body
    assert '[ "$status" -ne 0 ] || status=1' in body


def test_the_teardown_reports_what_it_could_not_remove() -> None:
    """Silence is how a stale deployment becomes the next run's empty state."""
    assert "owned_resources" in teardown_body()
    assert "resources this run owns are still present" in driver()
