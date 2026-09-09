"""What the clean-host driver must be, read from the driver itself.

The driver runs on a host this suite cannot reach, so what is checkable here is
its shape: that it makes a claim about every mandatory row, that it cannot report
a row it did not run, and that it carries the guards the gate rests on. None of
that is evidence the driver runs — only that it cannot quietly run less than it
says.
"""

from __future__ import annotations

import ast
import re
from inspect import signature
from pathlib import Path

import pytest
import yaml

from infrahub_sync.client import RunTerminalError, RunWaitTimeoutError

REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER = REPO_ROOT / "tests" / "compose" / "clean_host" / "clean-host.sh"
CHECKS = REPO_ROOT / "tests" / "compose" / "clean_host" / "checks"
# The declared configuration the deployment's own bootstrap registers. What its
# destination side names is what every managed row plans against.
BUNDLED_CONFIGURATION = REPO_ROOT / "deploy" / "compose" / "configuration" / "qualification.yaml"
# Every stage that can record a failure, as the service names the result key.
FAILURE_STAGES = ("plan", "verify", "apply", "sync")
# The one configuration a row registers for itself, carried in the kit.
UNKEYED_CONFIGURATION = REPO_ROOT / "tests" / "compose" / "clean_host" / "destination" / "unkeyed-configuration.yaml"
UNKEYED_SCHEMA = REPO_ROOT / "tests" / "compose" / "clean_host" / "destination" / "unkeyed.yml"

# Every row the accepted matrix requires. Written out rather than read from the
# driver, so a row deleted from the driver fails here instead of narrowing the
# list it is checked against.
MANDATORY_ROWS = (
    "artifact_identity",
    "cold_start_and_idempotence",
    "managed_execution",
    "unkeyed_write_policy",
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

# The one wait the seeding performs, named once because two cases read it: one for
# what it covers, one for what it precedes.
SEEDING_WAIT = "await_kinds((*UNKEYED_KINDS, SEEDED_KIND))"

# What the drift row may report of a recorded failure, and the sentence that
# report opens with. Everything else the stage recorded stays out of the line:
# the kit prints no value read out of the deployment, because some of them are
# credentials and a destination's own text can carry anything.
DRIFT_SENTENCE = "clean-host: drift: the settled apply recorded"
PERMITTED_FAILURE_FIELDS = {"stage", "outcome", "error_type"}


def driver() -> str:
    return DRIVER.read_text(encoding="utf-8")


# A docstring or a comment can satisfy a substring assertion, and this suite has
# been caught by exactly that. Every claim about a check's behaviour is made
# against its code with its prose removed.
_DOCSTRING_HOLDERS = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def code_of(path: Path) -> str:
    """Return one module's source with its comments and docstrings gone."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    holders = [node for node in ast.walk(tree) if isinstance(node, _DOCSTRING_HOLDERS)]
    for node in holders:
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and len(node.body) > 1
        ):
            node.body.pop(0)
    return ast.unparse(tree)


def branch_literals(source: str) -> set[str]:
    """Return every string literal a module uses as a branch.

    A branch name and a configuration's declared name can be the same string --
    they are here -- so "this name does not appear in the kit" is a claim about
    the wrong thing. What matters is whether a literal reaches a branch position.
    """
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            found |= {
                entry.value.value
                for entry in node.keywords
                if entry.arg == "branch"
                and isinstance(entry.value, ast.Constant)
                and isinstance(entry.value.value, str)
            }
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            named = {target.id.lower() for target in node.targets if isinstance(target, ast.Name)}
            if any("branch" in name for name in named):
                found.add(node.value.value)
    return found


def keyword_of(source: str, *, assigned_to: str, keyword: str) -> str | None:
    """Return one keyword argument of the call whose result is assigned to a variable.

    Anchored to the call rather than to the module: the same keyword appears on
    more than one call here, and a claim about one of them is satisfied by any.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if assigned_to not in [target.id for target in node.targets if isinstance(target, ast.Name)]:
            continue
        for entry in node.value.keywords:
            if entry.arg == keyword:
                return ast.unparse(entry.value)
    return None


def attributes_of(source: str, variable: str) -> set[str]:
    """Return the attributes read from one variable, excluding the methods called on it.

    Read from the parsed module rather than matched as text: `infra_device.yml`
    contains `device.yml`, and a pattern looking for `device.<something>` finds it.
    """
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == variable
    }
    read = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == variable
    }
    return read - called


def attribute_calls(source: str) -> set[str]:
    """Return every dotted call a module makes, as written."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        target: ast.expr = node.func
        parts: list[str] = []
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
            found.add(".".join(reversed(parts)))
    return found


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


def test_the_bundle_is_bound_to_the_record_before_it_is_extracted() -> None:
    """The checksum file beside an archive is not the record's claim about it.

    `sha256sum -c` reads a file the archive travels with, so a swapped pair
    satisfies it. What binds the archive to the qualification record is the
    record's own digest, and it has to bind before anything is taken out of the
    archive: what a test suite proves about the step is proved in
    `test_clean_host_integrity.py`, which runs it.
    """
    body = executable_lines()

    bound = 'require_recorded_bundle_digest "$bundle_name"'

    assert "['bundle']['sha256']" in body, "the driver never reads the digest the record names"
    assert bound in body, "row 1 never binds the archive to the digest the record names"
    assert body.index(bound) < body.index("tar -xzf")


def test_every_check_the_driver_runs_is_in_the_kit() -> None:
    """A named check that does not exist fails the row at the host, not here.

    Every runner, not only the generic one. Two rows coordinate through a writable
    directory and each has a runner of its own, so a check reached only through
    one of those would be verified by nobody.
    """
    runners = ("check", "coordinated_check", "row8_check")
    named = set()
    for runner in runners:
        named |= set(re.findall(rf"^\s*{runner} ([a-z_]+)", driver(), re.MULTILINE))
        named |= set(re.findall(rf"\$\({runner} ([a-z_]+)", driver()))

    assert named, "the driver runs no checks"
    for runner in runners:
        assert f"{runner}() {{" in driver(), f"the driver has no {runner}"
    for reached in ("busy_worker_stays_ready", "start_apply", "reported_failures"):
        assert reached in named, f"{reached} is run through a path this check does not read"
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
        # Gated on this run having created it: teardown removing a volume it did
        # not create is this gate destroying the state row 9 came to preserve.
        ("docker volume create", "remove_foreign_volume"),
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
    assert "remaining_resources" in teardown_body()
    assert "resources this run owns are still present" in driver()


def function_body(name: str) -> str:
    """Return one shell function's body, comments already removed."""
    body = executable_lines()
    opened = body.index(f"{name}() {{")
    return body[opened : body.index("\n}", opened)]


@pytest.mark.parametrize("helper", ["remaining_resources", "instance_resources"])
def test_a_query_this_host_refused_to_answer_is_not_read_as_an_empty_answer(helper: str) -> None:
    """Teardown's own verdict rests on this list, so a failed query cannot read as none.

    Every removal is judged complete by this list being empty. A host that
    refused the question would produce the same emptiness as a host with nothing
    left on it, and the run would report a teardown it never measured.
    """
    body = function_body(helper).replace("\\\n", " ")

    queried = [line for line in body.splitlines() if re.match(r"\s*docker\s", line)]
    assert queried, f"{helper} asks this host nothing"
    for line in queried:
        assert "|| echo" in line, f"a refused query reads as an empty answer: {line.strip()}"


# What F16 settled may never reach a retained artifact, in the forms this driver
# could produce them.
FORBIDDEN_IN_A_DIAGNOSTIC = ("docker inspect", "compose config", "operator.env", "secrets/", "printenv")


def test_the_diagnostic_is_taken_before_anything_is_removed() -> None:
    """After teardown there is no state left to describe, which is the whole point."""
    body = teardown_body()
    captured = body.index("capture_diagnostic")

    for removal in (
        'compose_bundle reset "$INSTANCE"',
        "stop_destination",
        "remove_foreign_volume",
        "stop_row8_containers",
        "discard_control_state",
    ):
        assert captured < body.index(removal), f"{removal} runs before the diagnostic is taken"


def test_the_diagnostic_is_kept_outside_everything_the_teardown_removes() -> None:
    """A file inside the extracted bundle or a deployment volume dies with them."""
    declared = re.search(r"^DIAGNOSTIC=(\S+)$", driver(), re.MULTILINE)
    assert declared is not None, "the driver names no diagnostic to keep"
    path = declared.group(1)

    assert path.startswith("$WORK/")
    assert "$EXTRACTED" not in path
    assert "$BUNDLE" not in path


def test_the_diagnostic_is_written_only_once_its_own_final_bytes_are_swept() -> None:
    """Sweeping the parts and trusting the whole is how a concatenation leaks.

    So the account is assembled under a name of its own and only becomes the
    retained file after the bytes that would be retained have been read.
    """
    capture = function_body("capture_diagnostic")

    assert capture.index("carries_a_canary") < capture.index('mv "$assembled" "$DIAGNOSTIC"')
    assert not re.search(r'>\s*"?\$DIAGNOSTIC', capture), "the diagnostic is written before it is swept"


def test_a_diagnostic_the_sweep_cannot_clear_is_withheld_whole() -> None:
    """No partial file and no redaction pass: an artifact nobody has is a result.

    Two outcomes withhold it, and they are different -- bytes that carry a
    credential, and a run that cannot say what its credentials were. A sweep with
    only the first would clear the second by never looking.
    """
    capture = function_body("capture_diagnostic")

    assert len([line for line in capture.splitlines() if "withheld" in line]) == 2
    assert capture.count('rm -f "$assembled"') == 2


@pytest.mark.parametrize("forbidden", FORBIDDEN_IN_A_DIAGNOSTIC)
def test_the_diagnostic_carries_none_of_the_raw_state_the_boundary_forbids(forbidden: str) -> None:
    """Container state, the declared environment, and the credential file stay out."""
    assert forbidden not in function_body("capture_diagnostic")


def test_the_diagnostic_carries_a_bounded_tail_of_each_log_rather_than_a_stream() -> None:
    """Bounded means bounded, whichever side of the exchange the log comes from.

    The deployment's entry point and the destination fixture are asked in
    different ways -- an environment variable and a `--tail` -- and neither
    default is this file's bound.
    """
    capture = function_body("capture_diagnostic").replace("\\\n", " ")
    logged = [line for line in capture.splitlines() if " logs" in line]

    assert len(logged) == 2, "the account reads the deployment's logs and the destination's"
    for line in logged:
        assert "$DIAGNOSTIC_LINES" in line, f"an unbounded log reaches the file: {line.strip()}"


def test_the_diagnostic_reads_the_destination_side_of_a_destination_rejection() -> None:
    """A rejection is reported to the deployment as whatever the destination chose to say.

    Infrahub's GraphQL wrapper chooses to say almost nothing, so the only account
    of why a mutation was refused is on the destination's side -- and it goes away
    with the fixture.
    """
    capture = function_body("capture_diagnostic").replace("\\\n", " ")

    named = re.search(r"^DIAGNOSTIC_DESTINATION_SERVICE=(\S+)$", driver(), re.MULTILINE)
    assert named is not None, "the driver names no destination service to read"

    # The command, not the heading above it: a section label naming the service
    # satisfies nothing about what was actually asked for.
    reading = [line for line in capture.splitlines() if "destination_compose logs" in line]
    assert len(reading) == 1, "the destination's log is read once or not at all"
    assert '"$DIAGNOSTIC_DESTINATION_SERVICE"' in reading[0], (
        f"the destination's whole stack is read, not one service: {reading[0].strip()}"
    )


def test_every_sweep_looks_for_the_credentials_this_run_generated() -> None:
    """A sweep that built no list of its own would clear whatever the last row left."""
    for function in ("row_secrets", "capture_diagnostic"):
        assert "write_canaries" in function_body(function), f"{function} sweeps for a list it did not build"


def test_the_sweep_primitive_searches_for_a_literal_and_says_when_it_finds_one() -> None:
    """Both sweeps route through this, so a sweep that cannot match has one place to be."""
    primitive = function_body("carries_a_canary")

    assert "grep -qF --" in primitive
    assert "return 0" in primitive
    assert "return 1" in primitive


def test_a_credential_list_that_could_not_be_built_is_not_a_shorter_list() -> None:
    """A missing generated value ends the attempt rather than narrowing what is sought."""
    recorder = function_body("record_canary")
    builder = function_body("write_canaries")

    assert "canary-missing" in recorder
    assert "return 1" in recorder
    assert recorder.count("printf") == 2, "a value is either recorded or refused, never both"
    # The settings loop, plus one call for each credential no setting names.
    assert builder.count("record_canary") == 1 + len(GENERATED_CREDENTIALS) - len(SECRET_SETTINGS_IN_BUNDLE)


# Everything `infrahub-sync-compose init` generates, read from the entry point
# itself: a new generated credential fails here until a sweep looks for it. The
# instance identity is not among them -- it is an identifier, and it is the one
# generated value every label and message carries on purpose.
def generated_credentials() -> set[str]:
    source = (REPO_ROOT / "deploy" / "compose" / "infrahub-sync-compose").read_text(encoding="utf-8")
    opened = source.index("command_init() {")
    body = source[opened : source.index("\n}", opened)]
    return set(re.findall(r"^\s*(\w+)=\$\(random_value", body, re.MULTILINE)) | set(
        re.findall(r'random_value \d+ > "\$(\w+)"', body)
    )


# Where the driver's sweep finds each of them. Two are values inside something
# else: the principal's token lives in the bearer document, and the administrator
# password in a file the PostgreSQL image reads. Those two are what an API or a
# database log would carry, and neither is named by a setting.
GENERATED_CREDENTIALS = {
    "product": "INFRAHUB_SYNC_PRODUCT_PASSWORD",
    "prefect": "INFRAHUB_SYNC_PREFECT_PASSWORD",
    "access": "INFRAHUB_SYNC_S3_ACCESS_KEY",
    "secret": "INFRAHUB_SYNC_S3_SECRET_KEY",
    "principal": "bearer_token",
    "ADMIN_SECRET": "admin_password",
}
SECRET_SETTINGS_IN_BUNDLE = ("product", "prefect", "access", "secret")


def test_the_sweep_looks_for_every_credential_the_bundle_generates() -> None:
    """A generated value no sweep names is a value every artifact may carry."""
    generated = generated_credentials()

    assert generated == set(GENERATED_CREDENTIALS), (
        f"the bundle generates {sorted(generated - set(GENERATED_CREDENTIALS))}, which no sweep names"
    )
    body = executable_lines()
    for where in GENERATED_CREDENTIALS.values():
        assert where in body, f"{where} is where a generated credential should be swept for, and it is absent"


def test_the_token_the_checks_are_given_is_the_token_the_sweep_looks_for() -> None:
    """Extracted in two places the two could drift, and the sweep would clear the live one."""
    body = executable_lines()

    assert body.count('sed \'s/.*"token": *"//;s/".*//\'') == 1
    assert "bearer_token" in function_body("configure_deployment")
    assert "bearer_token" in function_body("write_canaries")


def test_the_teardown_states_whether_it_completed() -> None:
    """A clean teardown and a silent failure to remove anything read the same otherwise."""
    body = teardown_body()

    assert "incomplete: resources this run owns are still present" in body
    assert 'report "complete: ' in body


def test_the_kit_renders_every_field_the_clients_run_errors_carry() -> None:
    """The client's error messages are constants, so an unrendered field is a lost field.

    A closed taxonomy puts the discriminator on the exception and leaves the
    rendering to whichever boundary catches it. A check that let one reach a
    traceback would print the category -- a run ended without success -- and
    nothing about which terminal state it reached or why.
    """
    source = code_of(CHECKS / "kit.py")

    for error in (RunTerminalError, RunWaitTimeoutError):
        for field in list(signature(error.__init__).parameters)[1:]:
            assert f"error.{field}" in source, f"{error.__name__}.{field} is carried but never rendered"


def test_the_recorded_run_state_is_read_from_the_store_and_not_from_the_client() -> None:
    """The verdict under diagnosis is one the client reported, so it is not the witness."""
    source = code_of(CHECKS / "diagnostics.py")

    assert "psycopg.connect" in source
    assert "product_runs" in source
    assert "deployment()" not in source


def test_the_diagnostic_check_still_runs_when_it_is_executed_as_a_script() -> None:
    """The driver runs it with `python /checks/diagnostics.py`, and nothing imports it.

    Its rendering is importable so a test can drive it with rows the store would
    hand over. The price of that is an entry point which can be removed without
    anything else noticing: the check would exit 0 having read nothing, and the
    account would lose the one section it exists for.
    """
    source = code_of(CHECKS / "diagnostics.py")

    assert "if __name__ == '__main__':" in source
    assert "main" in attribute_calls(source)


def test_the_durable_state_check_still_runs_when_it_is_executed_as_a_script() -> None:
    """The driver runs it with `python /checks/durable_state.py`, and nothing imports it.

    Its renderings are importable so a test can drive them with the rows and
    bodies the stores would hand over. The price of that is an entry point which
    can be removed without anything else noticing: the check would exit 0 having
    read nothing, and every row comparing a snapshot would compare two empty ones.
    """
    source = code_of(CHECKS / "durable_state.py")

    assert "if __name__ == '__main__':" in source
    assert "main" in attribute_calls(source)


def test_the_recorded_run_state_prints_no_column_that_holds_destination_data() -> None:
    """Two of the columns are documents describing what a run touched; neither prints.

    What may print is the class name a stage recorded, and only while it is one.
    """
    source = code_of(CHECKS / "diagnostics.py")

    assert "{summary}" not in source
    assert "{results}" not in source
    assert "printable_type_name(evidence.get('error_type'))" in source


def seeding() -> str:
    return code_of(CHECKS / "seed_destination.py")


def kit_source() -> str:
    """The kit module the checks share, which is where the plant and its guards live."""
    return code_of(CHECKS / "kit.py")


def configured_branches() -> dict[str, str]:
    """Return the branch each side of the bundled configuration names."""
    declared = yaml.safe_load(BUNDLED_CONFIGURATION.read_text(encoding="utf-8"))["configuration"]
    return {side: declared[side]["settings"]["branch"] for side in ("source", "destination")}


def test_the_destination_branch_a_managed_row_plans_against_is_one_the_kit_creates() -> None:
    """The Compose fixtures create it in `conftest.py`, so porting the rows carried none of it.

    A row that plans against a branch nobody created fails inside the worker with
    a destination-schema refusal, and the row then reports the wrong thing about
    the candidate.
    """
    named = set(configured_branches().values())

    assert named - {"main"}, "the bundled configuration plans against no branch but main"
    assert "client.branch.create" in attribute_calls(seeding()), (
        "nothing in the kit creates the branch the configuration names"
    )


def test_the_branch_the_kit_creates_is_read_from_the_configuration_that_names_it() -> None:
    """A branch named twice can be renamed once, and the row fails where nobody looks."""
    # Rendered from the parsed module, so the quoting is the unparser's.
    assert "sides['destination']['settings']['branch']" in kit_source()

    planned = set(configured_branches().values()) - {"main"}
    for module in ("kit.py", "seed_destination.py"):
        written = branch_literals(code_of(CHECKS / module)) & planned
        assert not written, f"{sorted(written)} is named in {module} as well as in the configuration"


def test_the_seeding_check_is_given_the_configuration_whose_branch_it_creates() -> None:
    """The document lives in the extracted bundle, so the check has to be handed it."""
    # Both container forms: the seeding runs before a deployment network exists,
    # and every row that plants a difference runs on one.
    for helper in ("check", "destination_check"):
        assert '"$BUNDLE/configuration:/configuration:ro"' in function_body(helper), (
            f"{helper} does not carry the declared configuration"
        )
    assert "/configuration/qualification.yaml" in kit_source()


def test_the_seeding_refuses_a_configuration_that_reads_and_writes_one_branch() -> None:
    """Two sides on one branch read identically, so every plan against them is empty.

    A row asserting its plan proposed something would then refuse for emptiness
    rather than for the property it exists to test, which is the failure this
    sweep found in the first place.
    """
    source = kit_source()

    assert "if branch == source:" in source
    assert "no plan against it can propose anything" in source


def test_the_destination_is_prepared_in_the_order_a_branch_inherits_from() -> None:
    """Schema, object, fork, difference -- and every position was learned from a failure.

    A branch forked before the schema load carries no schema. A branch forked
    before the object does not hold it, while its human-friendly ID is registered
    across the whole destination -- so a convergent upsert can neither find the
    node on that branch nor create one under an identifier already taken. And two
    sides holding an identical object give a plan nothing to propose, which is
    what the change on `main` is for.
    """
    source = seeding()
    steps = (
        "for schema in SCHEMAS:",
        "seed_object(CREATE_DEVICE, SEEDED_DEVICE)",
        "ensure_branch(planned_branch())",
        "plant('managed')",
    )
    missing = [step.strip() for step in steps if step not in source]
    assert not missing, f"the seeding never performs {missing}"

    positions = [source.index(step) for step in steps]
    assert positions == sorted(positions), "the destination is not prepared in the order a branch inherits from"


def mapped_fields() -> set[str]:
    """Return every field the bundled configuration maps, which is what a plan reads."""
    declared = yaml.safe_load(BUNDLED_CONFIGURATION.read_text(encoding="utf-8"))["configuration"]
    return {field["name"] for mapping in declared["schema_mapping"] for field in mapping["fields"]}


def test_the_planted_difference_changes_an_attribute_the_configuration_maps() -> None:
    """A change to anything else leaves the two sides equal as far as a plan is concerned."""
    changed = attributes_of(kit_source(), "node") | attributes_of(kit_source(), "device")

    assert changed, "nothing in the seeding changes the seeded object"
    assert changed <= mapped_fields(), f"{sorted(changed - mapped_fields())} is not a field any plan reads"


def test_the_planted_difference_is_written_to_the_side_a_plan_reads_from() -> None:
    """Written to the destination branch it would converge the two sides, not separate them."""
    written = keyword_of(kit_source(), assigned_to="device", keyword="branch")

    assert written == "branch", f"the difference is planted on {written}"
    assert "source_branch()" in kit_source(), "the side written to is not read from the configuration"


def test_the_attribute_the_seeding_writes_is_established_rather_than_assumed() -> None:
    """A fetched node types every member three ways, so which one this is has to be checked.

    Suppressing the union instead would leave a destination that models `type`
    differently to fail inside the SDK, with nothing said about what it declared.
    """
    source = kit_source()

    assert "isinstance(attribute, Attribute)" in source


def test_the_planted_value_is_fresh_on_every_run() -> None:
    """A fixed value converges: the first apply writes it, and the next plan is empty again."""
    assert "uuid.uuid4" in attribute_calls(kit_source())


def test_the_planted_difference_is_read_back_from_the_destination() -> None:
    """A save that persisted nothing leaves an empty plan, and the row reports that instead."""
    source = kit_source()

    assert source.count("sdk().get(") == 2
    assert "did not keep the difference planted" in source


def branches_of(configuration: Path) -> dict[str, str]:
    """Return the branch each side of one declared configuration names."""
    declared = yaml.safe_load(configuration.read_text(encoding="utf-8"))["configuration"]
    return {side: declared[side]["settings"]["branch"] for side in ("source", "destination")}


@pytest.mark.parametrize("configuration", [BUNDLED_CONFIGURATION, UNKEYED_CONFIGURATION], ids=lambda path: path.stem)
def test_no_configuration_a_row_runs_reads_and_writes_one_branch(configuration: Path) -> None:
    """One branch on both sides reads identically, so the plan proposes nothing.

    The row then refuses for emptiness rather than for the property it exists to
    test -- which is how the keyed-write row could never have reached its own.
    """
    sides = branches_of(configuration)

    assert sides["source"] != sides["destination"], f"{configuration.name} reads and writes {sides['source']}"


def test_every_branch_a_configuration_names_is_one_the_seeding_forks() -> None:
    """A branch nobody creates fails inside the worker, not in the row that named it."""
    named = {
        branch
        for configuration in (BUNDLED_CONFIGURATION, UNKEYED_CONFIGURATION)
        for branch in branches_of(configuration).values()
    } - {"main"}
    forked = set(re.findall(r"ensure_branch\(planned_branch\(([A-Z_]*)\)\)", seeding()))

    assert len(named) == len(forked), f"{sorted(named)} are planned against and {sorted(forked)} are forked"


def test_the_seeding_waits_for_the_view_a_client_reads_before_it_writes() -> None:
    """A schema load returns before its kinds resolve, and a write in that window fails.

    It fails as a missing schema rather than as whatever the row is testing. The
    load settles the server; this settles the view a client actually reads, which
    is the view the deployment's own worker will read too.
    """
    source = seeding()
    steps = (SEEDING_WAIT, "site_id = seed_unkeyed_peer()")
    missing = [step for step in steps if step not in source]
    assert not missing, f"the seeding never performs {missing}"

    assert source.index(steps[0]) < source.index(steps[1]), "the seeding writes before the kinds resolve"
    # Without this the client answers from the cache the load never invalidated,
    # and the wait returns immediately having proved nothing.
    assert "refresh=True" in source


def test_the_unkeyed_rows_peer_is_seeded_before_its_fork_and_its_subject_after() -> None:
    """Its two kinds sit on either side of its own fork, and each side is load-bearing.

    The peer must exist before the fork so the branch inherits it and the
    reference resolves at the destination -- without a resolvable peer the run
    fails at peer resolution and reports the wrong refusal. The subject must be
    seeded after, on the source alone, so the plan proposes a create: an update
    would carry the destination node's own id and be keyed by it.
    """
    source = seeding()
    steps = (
        "site_id = seed_unkeyed_peer()",
        "ensure_branch(planned_branch(UNKEYED_CONFIGURATION))",
        "seed_unkeyed_subject(site_id)",
    )
    missing = [step for step in steps if step not in source]
    assert not missing, f"the seeding never performs {missing}"

    positions = [source.index(step) for step in steps]
    assert positions == sorted(positions), "the peer and the subject are not on either side of the fork"


def test_no_shipped_kit_file_describes_the_mechanism_it_no_longer_uses() -> None:
    """A kit document is read on a host with nothing else to check it against.

    The mechanism changed once already, and prose describing the old one is how
    the next person reconstructs a shape that does not work.
    """
    stale = sorted(
        path.name
        for path in [*CHECKS.glob("*.py"), *CHECKS.parent.glob("destination/*")]
        if "keyless" in path.read_text(encoding="utf-8").lower()
    )

    assert not stale, f"{stale} still describe the mechanism this row replaced"


def test_the_unkeyed_rows_kind_carries_an_identifier_that_crosses_a_relationship() -> None:
    """That is the whole mechanism: a component the payload cannot resolve.

    Read from the schema the kit ships, so a kind edited into an all-direct
    identifier fails here rather than on a host, with the gate passing for a
    reason that says nothing about this row.
    """
    declared = yaml.safe_load(UNKEYED_SCHEMA.read_text(encoding="utf-8"))["nodes"]
    by_kind = {f"{node['namespace']}{node['name']}": node for node in declared}
    subject = by_kind["CleanDevice"]

    crossing = [component for component in subject["human_friendly_id"] if component.count("__") > 1]
    assert crossing, f"{subject['human_friendly_id']} is all-direct, so the SDK renders a key for it"

    named = {relationship["name"] for relationship in subject["relationships"]}
    assert {component.split("__")[0] for component in crossing} <= named, "the identifier crosses no relationship"
    # The peer's own identifier is direct, so it renders a key and is writable:
    # the refusal under test is the crossing kind's alone.
    assert all(component.count("__") == 1 for component in by_kind["CleanSite"]["human_friendly_id"])


def test_the_unkeyed_rows_subject_declares_no_unique_attribute() -> None:
    """Uniqueness is not the key, and it is one more thing a destination can derive from.

    The previous mechanism died because a destination computed an identifier for a
    kind whose schema declared none.
    """
    declared = yaml.safe_load(UNKEYED_SCHEMA.read_text(encoding="utf-8"))["nodes"]
    subject = next(node for node in declared if f"{node['namespace']}{node['name']}" == "CleanDevice")

    assert not any(attribute.get("unique") for attribute in subject["attributes"])


def test_the_unkeyed_rows_configuration_reaches_its_kind_through_a_reference() -> None:
    """A reference is what makes this a deployed-path row rather than a direct adapter call.

    `plan/derive.py` renders a reference-bearing field into the relationship record
    the live integration module hand-builds, so the operation the gate refuses is
    one a declared configuration really produces.
    """
    declared = yaml.safe_load(UNKEYED_CONFIGURATION.read_text(encoding="utf-8"))["configuration"]
    mapped = {entry["name"]: entry for entry in declared["schema_mapping"]}

    assert set(mapped) == {"CleanSite", "CleanDevice"}, "the referenced peer kind is not mapped"
    referenced = {field["name"]: field.get("reference") for field in mapped["CleanDevice"]["fields"]}
    assert referenced.get("site") == "CleanSite"


def test_the_unkeyed_row_requires_the_operation_to_be_a_create() -> None:
    """An update carries the destination node's own id, so the gate would pass on the id."""
    source = code_of(CHECKS / "unkeyed_write_policy.py")

    assert "'create'" in source
    assert "rather than a create" in source


def test_the_unkeyed_write_row_counts_on_the_branch_its_run_writes_to() -> None:
    """A refused write leaves `main` unchanged whether it was refused or not.

    Counting there would confirm the property without ever having been able to
    contradict it.
    """
    source = code_of(CHECKS / "unkeyed_write_policy.py")

    # Rendered from the parsed module, so the quoting is the unparser's.
    assert "f'/graphql/{branch}'" in source
    assert "'/graphql'" not in source, "the count is taken on the branch `/graphql` addresses"
    assert "written_branch" in source


def test_the_unkeyed_write_row_registers_the_whole_package_it_declares() -> None:
    """The declared credentials are part of it, and a run without them resolves no token."""
    source = code_of(CHECKS / "unkeyed_write_policy.py")
    declared = yaml.safe_load(UNKEYED_CONFIGURATION.read_text(encoding="utf-8"))

    assert "credentials" in declared, "the keyed-write configuration declares no credential to resolve"
    assert "declared_package()" in source
    assert "format_version" not in source, "the package is rebuilt rather than read, so a section can be dropped"


# Every check that reads a plan and then asserts something about applying it. The
# list is written out, so a new one fails here until it checks its own plan.
ROWS_THAT_PLAN = ("managed_execution.py", "schema_change.py", "start_apply.py")


def test_every_row_that_plans_refuses_a_plan_with_nothing_in_it() -> None:
    """A plan proposing nothing satisfies every negative claim beneath it.

    Refused before any write, interrupted mid-write, wrote nothing it should not
    have -- an empty plan makes all three true and none of them meaningful. The
    plant is not trusted to have worked: each row reads its own plan and refuses.
    """
    for module in ROWS_THAT_PLAN:
        source = code_of(CHECKS / module)
        assert "require_planned_work" in source, f"{module} asserts about a plan it never checked"


def test_every_row_that_plans_plants_a_difference_of_its_own() -> None:
    """An apply converges the two sides, so the row before this one leaves nothing."""
    for module in ROWS_THAT_PLAN:
        assert "plant" in attribute_calls(code_of(CHECKS / module)), f"{module} plans against whatever it inherits"


def planted_purposes(source: str) -> list[str]:
    """Return the label each `plant(...)` call in a module is given."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "plant"):
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append(first.value)
    return found


def test_no_two_rows_plant_an_indistinguishable_difference() -> None:
    """A failure has to say which row's difference is being observed."""
    purposes = [
        purpose
        for module in (*ROWS_THAT_PLAN, "seed_destination.py")
        for purpose in planted_purposes(code_of(CHECKS / module))
    ]

    assert len(purposes) == len(set(purposes)), f"two rows plant the same difference: {sorted(purposes)}"
    assert len(purposes) == len(ROWS_THAT_PLAN) + 1


def test_the_separate_sync_has_to_report_having_written_something() -> None:
    """A sync with nothing to converge reaches a phase with no "failed" in it."""
    source = code_of(CHECKS / "managed_execution.py")

    assert "wrote(synced.run)" in source
    assert "converged nothing" in source


def test_the_secret_row_states_which_credential_it_does_not_sweep_for() -> None:
    """A published constant left out of a sweep reads as an omission until it is written down."""
    body = driver()

    assert "published development constant" in body
    assert "credentials this run generated" in body


def test_no_check_builds_a_run_request_of_its_own() -> None:
    """The client requires a confirmation paired with the operation, so one builder holds it.

    A second construction site is where the pairing drifts, and the row that
    finds out is whichever one executes first -- which for the sync route was
    none of them for four commits.
    """
    building = {
        module.name
        for module in sorted(CHECKS.glob("*.py"))
        if module.name != "kit.py" and "CreateRunRequest" in attribute_calls(code_of(module))
    }

    assert not building, f"{sorted(building)} build a run request instead of asking the kit for one"


def test_the_kit_derives_the_confirmation_from_the_operation() -> None:
    """Passed in, it is an argument a caller can get wrong; derived, there is no pair to mismatch."""
    source = kit_source()

    assert "confirm_writes=operation == 'sync'" in source
    assert source.count("confirm_writes") == 1


def test_the_unkeyed_row_settles_its_run_rather_than_awaiting_success() -> None:
    """A refused operation cannot produce a successful run, so awaiting one never passes.

    For this row the terminal failure is the expected outcome, and the check has
    to read the verdict instead of reporting it.
    """
    source = code_of(CHECKS / "unkeyed_write_policy.py")
    settled = [line for line in source.splitlines() if "client.sync(" in line]

    assert settled, "the row runs no sync"
    for line in settled:
        assert "settle(" in line, f"the row awaits success from a run that cannot succeed: {line.strip()}"


def test_the_unkeyed_row_reads_the_recorded_cause_and_not_the_wrapper() -> None:
    """Every designed apply failure is reported as one wrapper class.

    An assertion on the wrapper would be satisfied by an unresolvable peer, an
    unaccounted identity component, or the destination's own rejection.
    """
    source = code_of(CHECKS / "unkeyed_write_policy.py")

    assert "failure.get('cause_type') != UNKEYED_REFUSAL" in source
    assert "OperationApplyFailedError" not in source, "the row names the wrapper, which says only that something failed"


def test_the_recovery_row_cannot_pass_on_the_refusal_the_unkeyed_row_induces() -> None:
    """The two rows would otherwise assert the same state for opposite situations.

    `reconciliation_required` derives from a dispatch having been proven rather
    than from bytes having been sent, so a refusal that sent nothing raises it as
    well -- and row 8's property could never fail for the reason it is about.
    """
    source = code_of(CHECKS / "recovery.py")

    # Anchored to the comparison, not to the import that makes the name available.
    compared = "recorded_failure(client, run_id).get('cause_type') == UNKEYED_REFUSAL"
    assert compared in source, "the row does not separate an interruption from a refusal"
    assert source.index("reconciliation_required") < source.index(compared)


def test_no_check_reads_one_stages_failure_evidence_by_name() -> None:
    """A `sync` records `sync_failure` where an `apply` records `apply_failure`.

    A row reading one name observes nothing about a run that failed in the other,
    and reports that as the property having held.
    """
    naming = {
        module.name
        for module in sorted(CHECKS.glob("*.py"))
        if module.name != "kit.py" and any(f"'{stage}_failure'" in code_of(module) for stage in FAILURE_STAGES)
    }

    assert not naming, f"{sorted(naming)} read one stage's evidence by name instead of asking the kit"


def test_the_drift_row_reads_and_writes_the_branch_its_plan_is_computed_against() -> None:
    """A drift on another branch says nothing about the comparison the refusal makes.

    The apply compares a fingerprint taken from `effective_destination_branch(...)`,
    so verifying the change anywhere else would report a passing gate having
    drifted nothing that gate looks at.
    """
    source = code_of(CHECKS / "schema_change.py")

    assert "BRANCH = planned_branch()" in source
    for call in ("attribute_kind(BRANCH)", "schema.all(branch=branch, refresh=True)", "branch=branch"):
        assert call in source, f"the drift row does not reach its branch through {call}"
    assert "'/api/schema'" not in source, "the row reads the unbranched schema endpoint, which answers for main"


def drift_report() -> ast.Call:
    """Return the one `print` the drift row reports a settled apply's failure through."""
    tree = ast.parse((CHECKS / "schema_change.py").read_text(encoding="utf-8"))
    reporting = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
        and DRIFT_SENTENCE in ast.unparse(node)
    ]
    assert len(reporting) == 1, f"{len(reporting)} steps report what the settled apply recorded"
    return reporting[0]


def reported_failure_fields(report: ast.Call) -> set[str]:
    """Return every `failure` field that one report reads, wrapped or not."""
    return {
        argument.value
        for read in ast.walk(report)
        if isinstance(read, ast.Call)
        and isinstance(read.func, ast.Attribute)
        and read.func.attr == "get"
        and isinstance(read.func.value, ast.Name)
        and read.func.value.id == "failure"
        for argument in read.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
    }


def test_the_drift_row_prints_only_the_bounded_fields_of_a_recorded_failure() -> None:
    """The kit's boundary is stage, outcome and the error class, each bounded.

    Read off the report itself rather than the module, so the set is exact: a
    fourth field added to the line fails here. Interpolating the mapping would
    print whatever else that stage recorded, and a stage is free to record a
    field this row has never seen -- a destination's own text can carry anything,
    which is the reason only a class name is safe to print.
    """
    report = drift_report()
    rendered = ast.unparse(report)

    assert reported_failure_fields(report) == PERMITTED_FAILURE_FIELDS
    for whole in ("{failure}", "{failure or"):
        assert whole not in rendered, "the row prints the whole failure mapping"
    for field in sorted(PERMITTED_FAILURE_FIELDS):
        assert f"printable_type_name(failure.get('{field}'))" in rendered, f"{field} reaches the line unbounded"
    # Read for the assertion beside it, and deliberately never reported.
    assert "failure.get('may_have_partially_written')" in code_of(CHECKS / "schema_change.py")


def test_the_seed_waits_for_every_kind_it_then_writes() -> None:
    """A load is accepted before the kinds it declares resolve, so the write races it.

    The managed kind is created immediately after this wait. Waiting only for the
    unkeyed pair leaves that create failing as a missing schema rather than as
    whatever the row it seeds is testing.
    """
    source = code_of(CHECKS / "seed_destination.py")

    assert SEEDING_WAIT in source, "the seed writes a kind it never waited for"
    # Through the kit's constant, so the managed kind has one spelling.
    assert "SEEDED_KIND" in source.split("await_kinds")[0], "the managed kind is named again instead of imported"
    assert source.index(SEEDING_WAIT) < source.index("seed_object(CREATE_DEVICE")


def test_the_recovery_row_waits_for_the_interruption_to_be_recorded() -> None:
    """Nothing the killed worker does records it: reconciliation is what terminalises it.

    Reading immediately races a verdict the service has not reached, and the row
    would report a false negative about the product.
    """
    source = code_of(CHECKS / "recovery.py")

    assert "await_reconciliation(client, run_id)" in source
    assert "STALL_THRESHOLD_SECONDS" in source, "the bound is a number with no stated relationship to the product's"
    assert "RECONCILE_TIMEOUT_SECONDS = 8 * STALL_THRESHOLD_SECONDS" in source


def test_the_busy_worker_row_holds_the_guard_its_first_run_will_contend_on() -> None:
    """A run that finishes cannot be observed executing; one that blocks can.

    `busy` is a positive scheduled queue depth (`service.py` derives it as
    `"busy" if snapshot.queue_depth > 0`), and the deployment runs one worker
    that claims everything it sees, so nothing about this row is observable by
    catching an interval. The row instead holds the deployment's own
    configuration write guard, which `flow.py` enters for a `sync` *after*
    `_claim_current_execution`, so the first run is claimed and then blocks for
    exactly as long as the row keeps the key. The state is durable because the
    row owns it, not because the workload happens to be slow.

    The guard is taken through the product's own `hold_apply_guard`, so the row
    provably contends on the same advisory key the flow will, rather than on a
    reconstruction of it that could drift.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    # The import alone is not the claim: a row that imported the guard and never
    # entered it would read identically. What matters is a call used as a context.
    entered = [
        item.context_expr
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        for item in node.items
        if isinstance(item.context_expr, ast.Call) and getattr(item.context_expr.func, "id", None) == "hold_apply_guard"
    ]
    assert len(entered) == 1, "the row never enters the write guard its first run blocks on"

    # `isinstance` first, so the line number the ordering rests on is reachable:
    # `submitted_stage` narrows nothing for the caller, it only answers.
    submitted = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and submitted_stage(node) == "sync"]
    assert len(submitted) == 1, "the first run is not a write, so it never reaches the guard"
    assert entered[0].lineno < submitted[0].lineno, "the first run is submitted before the guard is held"


def test_the_busy_worker_row_asks_for_a_claimable_worker_before_it_waits_for_a_claim() -> None:
    """A deployment with no live worker is a setup finding, not a queue finding.

    `worker.py` skips submission entirely while it cannot resolve its exact pool
    identity, so a run can sit unclaimed for reasons this row is not about. Asked
    first and bounded separately, the row that runs out of claim budget is one a
    live worker declined to serve -- which is the only version of that refusal
    worth reading.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")

    assert "await_claimable_worker(client)" in source, "the row waits for a claim it never checked was possible"
    assert source.index("await_claimable_worker(client)") < source.index("await_execution(client, executing_run)")

    # Its own bound, read off the wait itself: a precondition that borrowed the
    # claim budget would still spend the row's whole claim allowance on a
    # deployment that had no worker, which is the failure this separates.
    waits = {
        node.name: ast.unparse(node)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name in {"await_claimable_worker", "await_execution"}
    }
    assert set(waits) == {"await_claimable_worker", "await_execution"}, waits
    assert "CLAIMABLE_TIMEOUT_SECONDS" in waits["await_claimable_worker"]
    assert "CLAIM_TIMEOUT_SECONDS" not in waits["await_claimable_worker"], "the precondition spends the claim budget"
    assert "CLAIM_TIMEOUT_SECONDS" in waits["await_execution"]
    assert "CLAIMABLE_TIMEOUT_SECONDS" not in waits["await_execution"], "the claim spends the precondition's budget"


def test_the_busy_worker_row_hands_the_driver_a_durable_executing_state() -> None:
    """The handshake carries states, not moments.

    Each side waits for a file that, once written, stays written, so neither is
    sampling for a transient. The check proves the first run claimed and
    unfinished before it says so, and does not submit the second until the
    driver has answered that the worker's parent is stopped.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")

    assert "attempt.claimed_at is not None and attempt.terminal_at is None" in source, (
        "the row does not define execution as a claim that has not ended"
    )
    proven = source.index("await_execution(client, executing_run)")
    signalled = source.index("signal_driver('executing')")
    awaited = source.index("await_driver('stopped'")
    submitted = source.index("queued_run = client.plan(")

    assert proven < signalled, "the row tells the driver it is executing before it has proven it"
    assert signalled < awaited < submitted, "the second run is submitted before the worker parent is stopped"


def test_the_busy_worker_row_proves_the_whole_queue_from_one_snapshot() -> None:
    """A queue depth read before the claim and a live worker read after it are two deployments."""
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    # Call sites may exceed two -- the recovery replays the second run's key on
    # the failure path -- but the distinct keys may not, because a run is one key.
    submitted = [node for node in ast.walk(tree) if submitted_stage(node) is not None]
    assert len(submitted) >= 2, "the row does not submit the two runs the evidence needs"
    keys = {ast.unparse(node.args[1]) for node in submitted if isinstance(node, ast.Call) and len(node.args) > 1}
    assert len(keys) == 2, f"the row does not accept exactly two distinct runs: {sorted(keys)}"
    assert source.count("accepted.append(") == 2, "the row records a number of handles other than the two it accepts"

    # Scoped to the wait that proves the property. The row also reads a status
    # before it submits anything, to establish that a worker able to claim
    # exists at all, and that reading is not part of the evidence: it is taken
    # before either run and cannot be mistaken for a queue this row created.
    proof = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "await_qualifying_snapshot"
    )
    snapshots = [
        node
        for node in ast.walk(proof)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get_status"
    ]
    assert len(snapshots) == 1, "the queue depth and the live worker are read from separate snapshots"

    asserted = source.index("worker.state not in LIVE")
    for proven in ("worker.queue_depth >= 1", "worker.live_workers >= 1"):
        assert proven in source, f"the precondition never proves {proven}"
        assert source.index(proven) < asserted, f"{proven} is read after the property it is a precondition for"

    for reproven in ("claimed_and_running(attempt_of(client, running))", "attempt_of(client, waiting).claimed_at"):
        assert reproven in source, f"the qualifying snapshot never re-proves `{reproven}`"


def test_the_busy_worker_row_releases_its_guard_and_settles_both_runs() -> None:
    """A held advisory key blocks every later write; an unsettled run holds READY open.

    The release is a `with`, so it happens on the exception path too, and the
    driver is told the check is finished from a `finally` -- otherwise a refusing
    check would leave the driver waiting out its whole bound before resuming a
    worker it stopped.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    assert "signal_driver('done')" in source, "a refusing check never tells the driver it has finished"
    finals = [node for node in ast.walk(tree) if isinstance(node, ast.Try) and node.finalbody]
    assert finals, "nothing in the row runs on the exception path"
    assert any("signal_driver('done')" in ast.unparse(node) for final in finals for node in final.finalbody), (
        "the driver is only told the check finished when it finishes successfully"
    )

    settled = [
        node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "follow"
    ]
    assert len(settled) == 2, "the row leaves an accepted run in flight, and the rows after it wait for READY"


def test_the_busy_worker_row_states_the_one_write_it_converges() -> None:
    """Row 5 plants a drift and reverts only the schema, so this row's sync writes.

    `schema_change.py` calls `plant("drift")` and restores the attribute kind,
    never the planted value, so the two sides still differ when this row runs. A
    real `sync` therefore converges exactly that one update. Asserting it is what
    separates an intended row transition from an incidental write.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")

    assert "EXPECTED_UPDATES = 1" in source, "the row does not state how much its sync is allowed to write"
    for forbidden in ("'create': 0", "'delete': 0"):
        assert forbidden in source, f"the row permits a write it never planted: {forbidden}"


def test_one_absolute_budget_covers_everything_after_the_first_run_is_claimed() -> None:
    """Three sequential twenty-second waits are a sixty-second row, not a twenty-second one.

    The product's clocks start at the claim and run once: the guard's
    `lock_timeout`, the liveness stall threshold and the live-worker freshness
    window, thirty seconds each. A row whose waits each restart a deadline can
    spend far longer than any of them while still obeying every individual
    bound, and the first clock to expire replaces the coordination failure with
    a stalled or contended run.

    So there is one budget, started the moment the claim is proven and covering
    every step to the last handshake. It is a real timer rather than a checked
    deadline because most of that time is spent inside blocking client calls,
    which a loop condition never gets to re-examine.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")

    budget = re.search(r"POST_CLAIM_BUDGET_SECONDS = ([0-9.]+)", source)
    assert budget, "the row names no single budget for everything after the claim"
    assert float(budget.group(1)) <= 15, "the budget leaves the product's thirty-second clocks no room"

    for mechanism in ("signal.setitimer", "signal.SIGALRM", "signal.ITIMER_REAL"):
        assert mechanism in source, f"the budget cannot interrupt a blocking client call without {mechanism}"

    assert "HELD_TIMEOUT_SECONDS" not in source, "a per-wait deadline survives and can still extend the total"

    # Anchored to where the budget is entered, not to where it is defined: the
    # definition necessarily precedes everything and would satisfy any ordering.
    tree = ast.parse(source)
    entered = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        for item in node.items
        if isinstance(item.context_expr, ast.Call)
        and getattr(item.context_expr.func, "id", None) == "post_claim_budget"
    ]
    assert len(entered) == 1, "the row does not enter one budget covering everything after the claim"
    proven = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node) == "await_execution(client, executing_run)"
    )
    guarded = ast.unparse(entered[0])
    assert proven.lineno < entered[0].lineno, "the budget starts before the claim it is meant to follow"
    for covered in ("signal_driver('executing')", "client.plan(", "await_qualifying_snapshot(", "worker.state not in"):
        assert covered in guarded, f"{covered} happens outside the one budget"

    driver_bound = re.search(r"ROW6_HELD_TIMEOUT=([0-9]+)", executable_lines())
    assert driver_bound, "the driver does not bound its wait while the worker parent is stopped"
    # Strictly shorter, so the driver gives up and resumes while the check still
    # has budget left to be told. Equal bounds would have both sides expire at
    # once and the row would report neither side's reason clearly.
    assert int(driver_bound.group(1)) < float(budget.group(1)), (
        "the shell bound leaves the check no budget to hear the driver give up"
    )


def test_the_budget_is_translated_at_the_block_it_bounds_not_only_in_its_waits() -> None:
    """The alarm can land anywhere inside, and most of the inside is a blocking call.

    `client.plan`, a reproof's `get_run`, a control write and the property
    evaluation are all reached with the timer armed and none of them is a loop
    whose condition could notice. Translated only inside the two wait helpers,
    the row would end in a raw traceback for every other step -- which is the
    one outcome a qualification gate must never produce.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    entered = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and getattr(item.context_expr.func, "id", None) == "post_claim_budget"
            for item in node.items
        )
    )
    guarding = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(
            handler.type is not None and "BudgetExpiredError" in ast.unparse(handler.type) for handler in node.handlers
        )
        and any(step is entered for step in node.body)
    ]
    assert guarding, "the budget's own expiry escapes every step that is not one of the two waits"
    assert "refuse(" in ast.unparse(guarding[0].handlers[0]), "the escape is caught but never reported as a refusal"


def test_an_interrupted_second_submission_cannot_leave_an_untracked_accepted_run() -> None:
    """The alarm can land inside `client.plan` after the service admitted the run.

    Between the service's acceptance and this row recording the handle there is a
    window in which a run exists that nothing will settle -- and the next row
    waits for a READY that run holds open. Replaying the same mutation key
    recovers it, because the service answers a known key with the run it already
    admitted rather than admitting another. One key, one run, never a third.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    assert "EXPECTED_ACCEPTED = 2" in source, "the row does not state how many runs it should be holding"
    assert "recover_queued(" in source, "an interrupted second submission is never recovered"
    assert "if attempted and len(handles) < EXPECTED_ACCEPTED:" in source, (
        "recovery does not distinguish an ambiguous submission from one that never began"
    )

    # A missing handle means two different things, and only one is ambiguous. A
    # row that failed before it ever reached the second submission must not have
    # one admitted for it during cleanup.
    assert "queued_attempted = False" in source, "the row cannot tell an ambiguous submission from an absent one"

    # Adjacent statements, read from the tree rather than from offsets: the mark
    # has to be the statement immediately before the submission, or the window it
    # describes is wider than the call it is about.
    adjacent = any(
        ast.unparse(body[index]) == "queued_attempted = True"
        and "client.plan(read, key(QUEUED_PURPOSE))" in ast.unparse(body[index + 1])
        for node in ast.walk(tree)
        for body in (getattr(node, "body", None), getattr(node, "orelse", None), getattr(node, "finalbody", None))
        if isinstance(body, list)
        for index in range(len(body) - 1)
    )
    assert adjacent, "the attempt is not recorded immediately before the submission it is about"

    # The replay must use the submission's own key, or it creates a third run
    # rather than returning the accepted second one.
    keys = {
        ast.unparse(node.args[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "key"
    }
    assert "QUEUED_PURPOSE" in keys, "the second run's key is not named, so the replay cannot reuse it"
    assert "'busy-queued'" not in keys, "the second run's key is written out twice and the two can drift apart"


def test_cleanup_cannot_speak_over_the_refusal_it_is_cleaning_up_after() -> None:
    """`settle_accepted` suppresses the client's taxonomy and nothing else.

    A settlement that failed some other way -- a transport error, the recovery
    replay refused -- would then leave this check reporting that instead of the
    property that actually failed. So while something is already propagating,
    every exception out of cleanup is swallowed, and the original is re-raised
    unchanged. On the success path the same cleanup speaks normally, because
    there is nothing for it to speak over.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    guarding = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(handler.type is not None and ast.unparse(handler.type) == "BaseException" for handler in node.handlers)
    ]
    assert guarding, "nothing preserves a primary failure across this row's cleanup"
    handler = next(one for one in guarding[0].handlers if ast.unparse(one.type or ast.Constant("")) == "BaseException")
    body = "\n".join(ast.unparse(step) for step in handler.body)

    assert "suppress(Exception)" in body, "a cleanup failure replaces the refusal being reported"
    assert "release_and_settle" in body, "the failure path never releases the driver or settles what it accepted"
    assert body.rstrip().endswith("raise"), "the primary failure is not re-raised unchanged"
    assert guarding[0].orelse, "the success path does not settle at all"
    assert "release_and_settle" in "\n".join(ast.unparse(step) for step in guarding[0].orelse), (
        "the success path does not release and settle through the same cleanup"
    )


def test_the_driver_is_released_before_the_failure_path_waits_on_anything() -> None:
    """After a refusal the parent is still stopped, so settlement cannot go first.

    Nothing this row accepted can reach a verdict while the worker's parent is
    stopped, and on the refusal path it still is: the driver is waiting to hear
    from this check before it resumes. Settling first would block until the
    driver's own bound expired. So the driver is released first -- the key is
    already gone by then, because the hold closed above it -- and settlement
    follows.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    cleanup = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "release_and_settle"
    )
    ordered = "\n".join(ast.unparse(step) for step in cleanup.body)

    assert "signal_driver('done')" in ordered, "cleanup settles without ever releasing the driver"
    assert "settle_accepted" in ordered, "cleanup never settles what the row accepted"
    assert ordered.index("signal_driver('done')") < ordered.index("recover_queued"), (
        "the recovery replay is attempted while the worker parent is still stopped"
    )
    assert ordered.index("recover_queued") < ordered.index("settle_accepted"), (
        "settlement runs before the interrupted acceptance is recovered, so it could never settle it"
    )


def test_the_rows_alarm_is_restored_so_nothing_after_it_inherits_one() -> None:
    """An interval timer and a handler are process state, not block state."""
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    finals = [node for node in ast.walk(tree) if isinstance(node, ast.Try) and node.finalbody]
    unwinds = ["\n".join(ast.unparse(step) for step in node.finalbody) for node in finals]
    restoring = [unwound for unwound in unwinds if "setitimer" in unwound]
    assert restoring, "the row arms an interval timer it never disarms"
    assert "signal.signal(" in restoring[0], "the row leaves its own SIGALRM handler installed"


def test_the_first_runs_settled_result_is_what_the_write_assertion_reads() -> None:
    """A write count read from the acceptance says nothing: acceptance writes nothing.

    `sync` returns on 202 with an empty summary, so an assertion fed the accepted
    resource passes over a run that never wrote and over one that wrote
    everything. What the row is about is the verdict, which is what `follow`
    returns.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    consuming = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "require_converged_drift"
    ]
    assert len(consuming) == 1, "the row does not assert its first run's write exactly once"
    assert "follow(client, executing_run)" in ast.unparse(consuming[0]), (
        "the write assertion reads something other than the settled first run"
    )


def test_every_accepted_run_is_settled_even_when_the_row_refuses() -> None:
    """A refusal after the second acceptance still leaves two runs in flight.

    The rows after this one wait for READY, which a run of this row's making
    holds open, so settlement cannot be something only the success path does.
    And it cannot speak over the refusal that is already being reported: a
    settlement that fails says nothing about the property.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    assert source.count("accepted.append(") == 2, "the row does not retain both accepted handles"

    guarding = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(handler.type is not None and ast.unparse(handler.type) == "BaseException" for handler in node.handlers)
    )
    failing = "\n".join(ast.unparse(step) for handler in guarding.handlers for step in handler.body)
    succeeding = "\n".join(ast.unparse(step) for step in guarding.orelse)
    assert "release_and_settle" in failing, "a refusing row leaves its accepted runs in flight"
    assert "release_and_settle" in succeeding, "a succeeding row leaves its accepted runs in flight"
    assert "suppress(SyncClientError)" in source, (
        "settlement either swallows everything or replaces the refusal being reported"
    )


def test_only_the_busy_worker_row_is_given_a_writable_control_mount() -> None:
    """The gate's one writable channel into a check belongs to the row that needs it.

    `check` mounts everything read-only. A control directory handed to every
    check would be a writable mount this gate could no longer argue about, and
    row 2 asserts that nothing is mounted from outside the bundle.
    """
    body = executable_lines()

    assert "coordinated_check()" in body, "there is no row-owned coordinated check"
    generic = body[body.index("check() {") : body.index("destination_check() {")]
    assert "/control" not in generic, "every check is given the row's writable control mount"


def test_the_driver_stops_only_the_worker_parent_and_only_once_it_is_executing() -> None:
    """`docker pause` freezes the child too, and a frozen child is not executing.

    The property is that a worker with work *in hand* stays live, so the run has
    to keep running while its parent cannot claim another. `docker kill --signal
    STOP` reaches PID 1 only, which is the worker parent; the flow runs as a
    separate child process (`ProcessWorker`: "Execute flow runs as subprocesses
    on a worker"), so it keeps running and keeps its claim.
    """
    body = function_body("row_status")

    assert "docker kill --signal STOP" in body, "the driver does not stop the worker parent"
    assert "docker pause" not in body.split("busy worker leaves the deployment READY")[0], (
        "the row freezes the child with the parent, so nothing is executing when the queue is read"
    )
    assert body.index("await_control executing") < body.index("docker kill --signal STOP"), (
        "the worker parent is stopped before the check has proven a run is executing"
    )
    assert body.index("docker kill --signal STOP") < body.index("stopped"), (
        "the driver answers the check before it has stopped anything"
    )


def test_the_backgrounded_row_six_check_is_tracked_by_its_exact_identity() -> None:
    """`owned_resources` cannot see it, so teardown would call a host clean that is not.

    This is the only check the gate backgrounds, so it is the only one that can
    outlive its row. A `docker run` check carries none of the deployment's
    instance labels, which is what every other removal is keyed on -- so this one
    is named, and the name is what teardown removes. Nothing broader: an
    unlabelled-container sweep would reach containers this gate does not own.
    """
    body = executable_lines()

    assert '--name "$ROW6_CHECK_CONTAINER"' in body, "the backgrounded check container has no identity to remove"
    assert "ROW6_CHECK_CONTAINER=clean-host-row6-$INSTANCE" in body, "its name is not tied to this deployment"

    stopper = function_body("stop_row6_check")
    # Found by that name and removed by what it turned out to hold: `--rm` frees
    # the name as the container exits, so a removal by name would reach whatever
    # took it. The label this run wrote is what makes the removal this run's.
    assert 'remove_owned_fixture_container "$ROW6_CHECK_CONTAINER"' in stopper, (
        "nothing removes the backgrounded container"
    )
    assert 'wait "$ROW6_CHECK_PID"' in stopper, "the backgrounded process is never reaped"
    assert "stop_row6_check" in function_body("cleanup"), "teardown does not stop a check its row may have left"


def test_the_busy_worker_rows_finally_cannot_replace_the_refusal_it_is_reporting() -> None:
    """A control directory that cannot be written is not this row's verdict to give.

    The refusal already raised is. An exception out of the last handshake would
    replace a sentence about the deployment with one about the harness, and the
    driver's own bound already covers the state never arriving.
    """
    source = code_of(CHECKS / "busy_worker_stays_ready.py")
    tree = ast.parse(source)

    final = next(node for node in ast.walk(tree) if isinstance(node, ast.Try) and node.finalbody)
    unparsed = "\n".join(ast.unparse(node) for node in final.finalbody)
    assert "suppress(OSError)" in unparsed, "a failed control write would replace the refusal being reported"
    assert "signal_driver('done')" in unparsed


def test_the_row_six_stop_is_required_rather_than_best_effort() -> None:
    """A signal that did not land leaves the parent claiming, and the row asserts anyway.

    The check submits its second run as soon as the driver says the parent is
    stopped. If that answer can be given after a failed signal, the property is
    asserted against a deployment nothing was holding open -- the exact vacuous
    pass this row exists to exclude. Only the resume in the trap may be
    best-effort, because there the alternative is a worker left stopped.
    """
    row = function_body("row_status").replace("\\\n", " ")
    stopping = next(line for line in row.splitlines() if "docker kill --signal STOP" in line)

    assert "|| true" not in stopping, "a failed stop is swallowed and the row asserts against a claiming parent"
    assert "|| fail" in stopping, "a failed stop has no named refusal"
    assert row.index("docker kill --signal STOP") < row.index("$ROW6_CONTROL/stopped"), (
        "the driver answers the check before it has stopped anything"
    )


def test_row_six_resumes_and_answers_the_check_whatever_the_observation_did() -> None:
    """A timed-out observation must not leave the check holding the key for its own bound.

    If the driver resumes but never answers, the check waits its whole bound
    while the guard is still held, and the product's thirty-second clocks reach
    its first run first -- so the run is reported stalled or contended and the
    coordination failure is never the diagnosis. The resume and the answer
    therefore sit outside the branch, and the timeout gets its own sentence.
    """
    row = function_body("row_status")
    conditional = row[row.index('if await_control observed "$ROW6_HELD_TIMEOUT"; then') :]
    branch = conditional[: conditional.index("fi")]

    assert "resume_worker" not in branch, "the worker is resumed only when the observation arrived"
    assert "resumed" not in branch, "the check is answered only when the observation arrived"
    assert branch.count("row6_observed=") == 2, "the branch does not record both outcomes"

    after = conditional[conditional.index("fi") :]
    assert after.index("resume_worker") < after.index("$ROW6_CONTROL/resumed"), (
        "the check is answered before the resume"
    )
    assert "row6_observed" in row[row.index("$ROW6_CONTROL/resumed") :], "a timed-out observation is never reported"
    assert "the check never reported reading the deployment's status" in row, "the driver timeout has no sentence"


def test_a_resume_that_did_not_land_is_reported_and_keeps_custody_of_the_parent() -> None:
    """Answering the check on a failed resume is worse than failing the row.

    The check is waiting on that answer to release its write guard. Told the
    parent resumed when it did not, it drops the key against a deployment that
    still cannot finish either run, and then hangs in settlement instead of
    reporting anything. And clearing the container out of `ROW6_WORKER` on a
    failed signal discards the only identity anything holds for it, so the trap
    could no longer try again.
    """
    resumer = function_body("resume_worker")

    assert "|| true" not in resumer, "the resume swallows its own failure"
    assert "return 1" in resumer, "the resume cannot report that the parent is still stopped"
    lines = [line.strip() for line in resumer.splitlines()]
    guarded = lines.index('if docker kill --signal CONT "$ROW6_WORKER" >/dev/null 2>&1; then')
    closed = lines.index("fi", guarded)
    cleared = [index for index, line in enumerate(lines) if line == "ROW6_WORKER="]
    assert cleared, "the resume never releases custody, so it can never succeed"
    # Inside the branch the signal succeeded in, not merely after it: a clear
    # anywhere below the `fi` runs on the failure path too.
    assert all(guarded < index < closed for index in cleared), "custody is dropped even when the parent stayed stopped"

    row = function_body("row_status").replace("\\\n", " ")
    answered = row.index("$ROW6_CONTROL/resumed")
    before = [line for line in row[:answered].splitlines() if "resume_worker" in line]
    assert before, "nothing resumes the parent before the check is told it resumed"
    assert "|| fail" in before[-1], "the check is told the parent resumed without proving that it did"

    assert "resume_worker || true" in function_body("cleanup"), (
        "a resume that cannot land stops teardown from reaching the deployment it must remove"
    )


def test_the_driver_resumes_the_worker_parent_on_every_path_out_of_row_six() -> None:
    """A worker left stopped makes every later row wait on a deployment that cannot answer.

    Including the trap: row 7 restarts this deployment and row 8 kills a worker
    mid-write, and both begin by expecting one that answers. A resume that only
    runs on the happy path would turn one row's failure into every later row's.
    """
    body = executable_lines()

    assert "resume_worker()" in body, "the driver has no way to resume the parent it stopped"
    assert "docker kill --signal CONT" in function_body("resume_worker"), "resume_worker does not resume anything"

    cleanup = function_body("cleanup")
    assert "resume_worker" in cleanup, "the teardown trap does not resume a worker the row may have stopped"

    row = function_body("row_status")
    assert row.count("resume_worker") >= 2, "row 6 resumes the parent on one path only"


@pytest.mark.parametrize("row", ["row_cold_start_and_idempotence", "row_restart", "row_alpha_replacement"])
def test_every_row_comparing_durable_state_first_proves_there_is_some(row: str) -> None:
    """The snapshot carries one line per table, so its non-emptiness proves nothing.

    Two empty deployments compare equal as happily as two identical full ones,
    and a restart that lost everything would satisfy an equality over nothing.
    """
    body = function_body(row)

    assert "require_snapshot_holds" in body, f"{row} compares a snapshot it never proved holds anything"


def test_the_durable_state_precondition_reads_a_count_and_not_a_string() -> None:
    """`[ -n "$snapshot" ]` is true for a snapshot of an entirely empty deployment."""
    helper = function_body("require_snapshot_holds")

    assert "snapshot_count" in helper
    assert "0) fail" in helper


def test_every_row_that_compares_snapshots_names_what_it_expects_to_survive() -> None:
    """Enumerated from the driver, not remembered: `grep durable_snapshot` is the list.

    A generic "some table is non-zero" drifts back to the same weakness the
    moment the schema gains a table populated for an unrelated reason, so each
    row names the thing whose survival it is about -- and row 2 runs before any
    run exists, so what it names is what bootstrap made rather than a run count.
    """
    body = executable_lines()
    comparing = {
        name
        for name in ("row_cold_start_and_idempotence", "row_restart", "row_alpha_replacement", "row_status")
        if "durable_snapshot" in function_body(name)
    }
    assert comparing == {"row_cold_start_and_idempotence", "row_restart", "row_alpha_replacement"}

    named = set(re.findall(r"require_snapshot_holds \"\$\w+\" (\w+)", body))
    assert named == {"configuration_versions", "product_runs"}, f"the rows expect {sorted(named)} to survive"


def test_the_durable_snapshot_narrows_named_columns_and_says_why_beside_each_one() -> None:
    """A table excluded stops comparing whole records; a column narrows one value.

    So every exclusion names the table it applies to and the columns within it,
    and carries its reason where the exclusion is declared -- an exclusion whose
    reason lives somewhere else is one nobody re-reads when the schema moves.
    What the snapshot then distinguishes is proved in
    `test_clean_host_integrity.py`, which runs it.
    """
    source = (CHECKS / "durable_state.py").read_text(encoding="utf-8")
    declared = re.search(r"VOLATILE_COLUMNS: dict\[str, tuple\[str, \.\.\.\]\] = \{(.*?)\n\}", source, re.DOTALL)
    assert declared is not None, "the snapshot declares no exclusion set this check can read"
    literal = declared.group(1)
    narrowed = re.findall(r'^ {4}"(\w+)": \((.*?)\),$', literal, re.MULTILINE)

    assert narrowed, "the snapshot excludes something this check cannot read as named columns of one table"
    for table, columns in narrowed:
        assert re.fullmatch(r'(?:"\w+", ?)+', columns.strip() + ", "), f"{table} is not narrowed to named columns"
        # The split lands mid-line, so the key's own indentation is dropped here.
        preceding = [line for line in literal.split(f'"{table}":')[0].splitlines() if line.strip()]
        assert preceding[-1].lstrip().startswith("#"), f"{table} is narrowed without its reason beside it"


def test_no_equality_in_the_driver_can_hold_because_neither_side_exists() -> None:
    """Enumerated from the driver: every `require` goes through one comparison.

    Two values that both failed to be produced compare equal, and the row then
    reports a property it never observed. Closed once, at the choke point, rather
    than at each of the sites `grep '    require '` returns.
    """
    helper = function_body("require")

    assert '[ -n "$2" ]' in helper, "an empty expectation is accepted as an expectation"
    assert "nothing was produced to compare it against" in helper


def test_the_record_reader_refuses_a_value_that_is_present_and_empty() -> None:
    """An absent key already exits non-zero; an empty one printed nothing and exited 0.

    That is the case that reached a comparison as a value equal to any other
    missing one.
    """
    helper = function_body("record")

    assert "str(held) == ''" in helper
    assert "holds an empty value where one is required" in helper


def test_the_managed_row_fetches_the_review_artifact_by_kind() -> None:
    """Whichever artifact came back first satisfies a check that only requires one."""
    source = code_of(CHECKS / "managed_execution.py")

    assert "entry.kind == REVIEW_ARTIFACT" in source
    assert "artifacts[0]" not in source


def test_every_row_that_plans_states_the_count_it_planted_for() -> None:
    """One planted difference is one proposed operation, and that is knowable.

    `>= 1` accepts a plan proposing operations nobody planted, which is what a
    qualification gate exists to catch.
    """
    for module in ROWS_THAT_PLAN:
        source = code_of(CHECKS / module)
        assert "expected=PLANTED_OPERATIONS" in source, f"{module} bounds its plan below instead of stating it"
    assert "if total != expected:" in kit_source()


def test_the_unkeyed_rows_count_survives_an_answer_it_cannot_read() -> None:
    """A GraphQL refusal is a 200 carrying `errors`, and indexing it raises."""
    source = code_of(CHECKS / "unkeyed_write_policy.py")

    assert "without a count" in source
    assert "answer.json()['data'][UNKEYED_KIND]['count']" not in source


def test_the_secret_row_sweeps_every_deployment_this_run_started() -> None:
    """Its claim covers what the gate leaves behind, and rows 9 and 10 destroy most of it.

    A tail is the lifecycle command's own bound and right for a diagnostic; for a
    sweep it makes the sentence wider than the evidence. And no amount of
    sweeping later recovers a container that no longer exists, so each
    deployment's whole log is taken before the thing that destroys it.
    """
    body = executable_lines()
    captured = [line for line in body.splitlines() if "capture_deployment_evidence" in line and "()" not in line]

    assert "INFRAHUB_SYNC_LOG_LINES=all" in body, "the sweep reads a bounded tail of what it claims to have read"
    assert len(captured) >= 3, "a deployment is destroyed with its log unread"
    for destroying in ("row_ownership_and_reset", "row_alpha_replacement"):
        row = function_body(destroying)
        assert row.index("capture_deployment_evidence") < row.index('compose_bundle reset "$INSTANCE"'), (
            f"{destroying} destroys its deployment before its log is taken"
        )
    # And the deployment that is still standing, which no row destroys.
    assert "capture_deployment_evidence final" in function_body("row_secrets")
    assert '"$LOG_DIR"/*.log' in function_body("row_secrets")


def test_no_docker_query_in_a_test_position_reads_a_refusal_as_an_answer() -> None:
    """A query this host declined answers nothing, and `-z` on it reads as "none left".

    Fixed once in `owned_resources` and missed in the reset row, because fixing an
    instance does not sweep for the class.
    """
    tested = [
        line.strip()
        for line in executable_lines().splitlines()
        if re.search(r'\[ +-[zn] +"\$\(docker ', line) or re.search(r'= +"\$\(docker ', line)
    ]

    assert tested == [], f"a refused query decides a test: {tested}"


def test_the_drift_row_proves_the_change_moved_what_the_apply_compares() -> None:
    """Landing on the branch and moving the fingerprint are two different claims.

    `_require_planned_schema` compares the manifest's recorded fingerprint against
    the live one, and a plan resource exposes that same field -- so a plan taken
    after the drift carries what `live` will be. Comparing the two is the
    comparison the apply is about to make, asserted before it is made.
    """
    source = code_of(CHECKS / "schema_change.py")

    assert "moved = client.get_plan(probe.run.run_id).schema_fingerprint" in source
    assert "if plan.schema_fingerprint == moved:" in source
    # Both values in the refusal, so a run that fails here says which did not move.
    assert "left the consumed-semantics fingerprint at {moved}" in source
    assert source.index("load_attribute_kind(REVERSIBLE_KINDS[original], BRANCH)") < source.index("moved = ")


# What submits a run. Each returns on acceptance, not on completion.
SUBMISSIONS = ("plan", "sync", "apply", "verify")


def submitted_stage(node: ast.AST) -> str | None:
    """Return the stage a `client.<stage>(...)` call submits, or `None`.

    Returning the name rather than a flag keeps the narrowing where the type
    checker can follow it: the caller needs `node.func.attr` and `node.lineno`,
    and neither is reachable from a bare `ast.AST`.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in SUBMISSIONS:
        return None
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "client":
        return None
    return node.func.attr


def unwaited_submissions(source: str) -> list[str]:
    """Return every run submission in a module that nothing waits for.

    A submission is waited when it is handed straight to `follow`/`settle`, or
    assigned to a name one of them is later given. Read from the parsed module,
    because "a `follow` appears somewhere in this file" is not the claim.
    """
    tree = ast.parse(source)
    waited: set[str] = set()
    settled_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"follow", "settle"}:
            for argument in node.args:
                if isinstance(argument, ast.Call):
                    waited.add(ast.dump(argument))
                elif isinstance(argument, ast.Name):
                    settled_names.add(argument.id)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if {target.id for target in node.targets if isinstance(target, ast.Name)} & settled_names:
            waited.add(ast.dump(node.value))

    found: set[str] = set()
    for node in ast.walk(tree):
        stage = submitted_stage(node)
        if stage is None or ast.dump(node) in waited:
            continue
        found.add(f"client.{stage} on line {getattr(node, 'lineno', 0)}")
    return sorted(found)


def test_no_check_reads_a_verdict_from_a_run_it_never_waited_for() -> None:
    """A submission returns on 202, and the verdict is the worker's, reached later.

    Reading the evidence straight after submitting reports an unfinished run as
    the product declining to do what the row is about -- which is how row 5
    reported `None` where the refusal belongs. Third occurrence of this class:
    row 4 needed `settle`, row 8 needed a bounded wait for reconciliation.

    One submission is deliberately unwaited and reads no verdict: the interrupt
    row hands its run to the driver to kill mid-write. The busy-worker row used
    to be the second, and settles both of its runs now, because the row after it
    waits for a READY a queue of its making would hold open.
    """
    offenders = {}
    for module in sorted(CHECKS.glob("*.py")):
        source = code_of(module)
        if "recorded_failure" not in source and "get_results" not in source:
            continue
        bare = unwaited_submissions(source)
        if bare:
            offenders[module.name] = bare

    assert offenders == {}, f"a verdict is read from a run nothing waited for: {offenders}"


def test_the_compatible_half_runs_against_a_schema_change_it_loaded() -> None:
    """A run against the destination as it stands is not a compatible change.

    The contract's compatible half is about a change an operator makes and a
    running deployment picks up. Planning against an unchanged schema exercises
    nothing of that: the row has to load an additive attribute, prove the
    destination converged on it, and only then plan.
    """
    source = code_of(CHECKS / "schema_change.py")

    assert "ADDITIVE_ATTRIBUTE" in source, "the row loads no additive change for its compatible half"
    assert "attribute_kind(branch, ADDITIVE_ATTRIBUTE) != ADDITIVE_KIND" in source, (
        "the row does not prove the additive change landed"
    )
    assert "key('compatible')" in source, "the row runs no compatible half at all"
    assert source.index("load_attribute_kind(original, BRANCH)") < source.index("key('compatible')"), (
        "the compatible half plans before the change it is supposed to run against"
    )


def test_the_schema_change_targets_only_the_node_both_halves_are_about() -> None:
    """A change written into every node the file declares changes nodes no half is about.

    Both halves are about one kind: the drifted attribute is the one the plan
    consumes on it, and the additive attribute is the compatible change to it.
    Applied across `schema["nodes"]`, the same edits would land on whatever else
    the seeded document grows -- and the row would be changing a schema it makes
    no claim about.
    """
    source = code_of(CHECKS / "schema_change.py")
    loader = source.index("def load_attribute_kind")
    loaded = source.index("sdk().schema.load", loader)
    body = source[loader:loaded]

    assert "smoke_node(schema)" in body, "the loader does not select the one node it changes"
    assert "for node in schema['nodes']" not in body, "the loader changes every node the file declares"


def test_the_targeted_node_is_matched_by_kind_and_refused_unless_there_is_exactly_one() -> None:
    """Infrahub composes a kind from a node's namespace and its name.

    Matched on that composition rather than on either half, so a namespace
    reused by a second node cannot be picked instead. Exactly one, so a seeded
    document that stops declaring this node refuses here rather than loading a
    change to nothing.
    """
    source = code_of(CHECKS / "schema_change.py")

    matcher = "f\"{node['namespace']}{node['name']}\" == SMOKE_KIND"

    assert matcher in source, "the node is not matched by the kind Infrahub composes"
    assert "if len(declared) != 1:" in source, "a document declaring none or several is accepted"


def test_the_additive_attribute_travels_with_every_load_of_the_seeded_schema() -> None:
    """A load states the node it declares, so one omitting it could take it back.

    The drift half and the revert after it load the same document. If either
    dropped the attribute the compatible half added, the halves after it would
    run against a schema this row had silently undone -- and the compatible
    claim would be about a change that no longer existed.
    """
    source = code_of(CHECKS / "schema_change.py")
    loader = source.index("def load_attribute_kind")
    loaded = source.index("sdk().schema.load", loader)

    assert "ADDITIVE_ATTRIBUTE" in source[loader:loaded], (
        "the additive attribute is not part of what the seeded schema is loaded as"
    )


def test_the_schema_row_proves_no_container_was_replaced_to_pick_the_change_up() -> None:
    """ "Needs no operator action" is a claim about containers, which only the host can answer.

    The check runs inside a throwaway container on the deployment's network and
    cannot see the engine, so it says so and leaves the claim to the driver. The
    bracket is the whole row rather than its compatible half: the driver cannot
    see inside a check, and no container replaced at any point in the row is the
    stronger claim that contains the one the compatible half needs.
    """
    body = function_body("row_schema_change")

    assert "before=$(deployment_container_identities)" in body, "the row never reads what it must not change"
    assert 'require "a schema change replaced a deployment container" "$before"' in body
    assert body.index("before=") < body.index("check schema_change")


def test_the_container_identities_the_schema_row_compares_are_this_deployments_own() -> None:
    """A container is identified by what a replacement changes, and by nothing else.

    Read through the instance label, so a fixture container this gate started is
    not mistaken for a service of the deployment, and sorted, so the order the
    engine happened to list them in cannot be read as a replacement.
    """
    assert "deployment_container_identities() {" in executable_lines(), (
        "the driver has no step reading the identities a replacement changes"
    )
    helper = function_body("deployment_container_identities")

    assert "label=io.infrahub-sync.instance=$INSTANCE" in helper
    assert "{{.ID}}" in helper, "the row compares nothing a replacement changes"
    assert "sort" in helper, "the engine's own ordering can be read as a replacement"
    assert "owns no containers" in helper, "an empty answer is compared as if it were an answer"


def test_the_drift_rows_apply_is_settled_before_its_evidence_is_read() -> None:
    """And before the revert, which would otherwise land while the run was queued.

    The revert is the *last* load of the original kind: the compatible half loads
    it too, before any apply exists, so the first occurrence is a different step
    with a different job.
    """
    source = code_of(CHECKS / "schema_change.py")

    assert source.index("settle(client, accepted)") < source.index("failure = recorded_failure(client, run_id)")
    assert source.index("settle(client, accepted)") < source.rindex("load_attribute_kind(original, BRANCH)")


def test_the_drift_row_waits_for_the_schema_it_loaded_to_converge() -> None:
    """A read that saw the change and a destination that finished applying it differ."""
    source = code_of(CHECKS / "schema_change.py")

    assert "wait_until_converged=True" in source
    assert "if attribute_kind(branch) != kind:" in source


def test_the_restart_row_reads_the_identity_a_restart_actually_replaces() -> None:
    """`restart` restarts the process inside the container it already has.

    `deploy/compose/infrahub-sync-compose`'s `command_restart` runs
    `docker compose restart sync-api sync-worker` and says so: "Containers and
    data survive; the processes inside them do not. The worker that comes back
    registers under a new Prefect identity." So a container identity that changed
    would mean the product had stopped doing what `restart` means, and a row
    asserting it can only pass against incorrect behaviour.
    """
    entry_point = (REPO_ROOT / "deploy" / "compose" / "infrahub-sync-compose").read_text(encoding="utf-8")
    opened = entry_point.index("command_restart() {")
    assert "compose restart" in entry_point[opened : entry_point.index("\n}", opened)]

    row = function_body("row_restart")
    assert "deployment_container" not in row, "the row compares a container identity a restart does not change"
    assert "check worker_identity" in row


def test_the_restart_row_proves_there_was_a_worker_to_replace() -> None:
    """An empty set before makes any name afterwards look like a replacement."""
    row = function_body("row_restart")

    assert '[ -s "$WORK/workers-before" ]' in row
    assert row.index("workers-before") < row.index("compose_bundle restart")


def test_the_replacement_wait_looks_for_a_name_it_has_not_seen() -> None:
    """The departing worker lingers ONLINE, so the count is not the signal.

    Bounded at the figure the Compose lifecycle gate proved, and expiring is a
    failure of the row rather than a verdict about the deployment.
    """
    helper = function_body("wait_for_replacement_worker")

    assert "grep -vxF -f" in helper, "the wait compares sets by size rather than by name"
    assert "WORKER_REPLACEMENT_SECONDS" in helper
    assert re.search(r"^WORKER_REPLACEMENT_SECONDS=180$", driver(), re.MULTILINE)


def test_the_worker_probe_asks_the_pool_the_deployment_uses() -> None:
    """The pool is a configurable setting, and a probe naming another reports nothing.

    An empty answer for a healthy deployment reads exactly like a deployment with
    no worker, which is the shape the row's own precondition exists to refuse.
    """
    source = code_of(CHECKS / "worker_identity.py")

    assert "os.environ['INFRAHUB_SYNC_WORK_POOL']" in source
    assert "infrahub-sync'" not in source, "the probe names a pool of its own"
    assert "INFRAHUB_SYNC_WORK_POOL=$(sed -n 's/^INFRAHUB_SYNC_WORK_POOL=//p'" in driver()


def test_the_worker_probe_refuses_an_answer_it_cannot_read() -> None:
    """A Prefect server that answered something else would raise where a sentence belongs."""
    source = code_of(CHECKS / "worker_identity.py")

    assert "if answer.status_code != 200:" in source
    assert "if not isinstance(reported, list):" in source


def mutation_purposes() -> list[tuple[str, str]]:
    """Return every `key(...)` purpose the checks name, with where it is named.

    Parsed rather than matched: a call spread over two lines would escape a
    pattern, and the same word inside a docstring would satisfy one.
    """
    found = []
    for module in sorted(CHECKS.glob("*.py")):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "key"):
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append((first.value, f"{module.name}:{node.lineno}"))
    return found


def test_every_mutation_key_the_checks_present_is_a_purpose_of_its_own() -> None:
    """One key presented twice with different bodies is a conflict the API refuses.

    `key()` promises a mutation key unique to one purpose in one run, and the run
    identifier is shared by the whole matrix -- so two checks naming one purpose
    render one key. The first consumes it and the second is refused, which
    surfaces as that row's precondition failing for a reason that is not its own.

    Only a live run can produce it: the call sites are in different files, and
    nothing reading either alone would see the collision.
    """
    named = mutation_purposes()
    assert named, "the checks present no mutation key at all"

    seen: dict[str, list[str]] = {}
    for purpose, where in named:
        seen.setdefault(purpose, []).append(where)
    shared = {purpose: places for purpose, places in seen.items() if len(places) > 1}

    assert shared == {}, f"one mutation key is presented for more than one purpose: {shared}"


def test_a_refused_request_says_what_it_was_refused_with() -> None:
    """`APIError`'s message is the taxonomy's constant sentence, so it names nothing.

    A 409 escaping as "the Sync API refused the request" is recoverable only from
    the container log; the status and the code are enumerated fields on the error
    and belong in the sentence a row reports. Translated at the one place every
    check reaches the deployment rather than at each call that could provoke one.
    """
    source = kit_source()

    assert "except APIError as error:" in source
    assert "error.status" in source
    assert "error.code" in source
    # Status and code only: a refusal's detail can quote declared configuration.
    for leaked in ("error.reason", "error.family", ".text", ".json()"):
        assert leaked not in source, f"a refusal's {leaked} reaches the sentence a driver shows"


# ---------------------------------------------------------------------------
# Row 8 — an actual held destination write, not a phase this driver watched go by
# ---------------------------------------------------------------------------
PROXY = CHECKS / "destination_proxy.py"


def proxy_constant(name: str) -> str:
    """Return one control-file name the proxy declares, so the driver cannot name its own."""
    found = re.search(rf"^{name} = \"([^\"]+)\"$", PROXY.read_text(encoding="utf-8"), re.MULTILINE)
    assert found is not None, f"the proxy declares no {name}"
    return found.group(1)


def test_the_deployments_destination_traffic_is_routed_through_the_fixture_proxy() -> None:
    """Nothing else can hold a write between the destination completing it and the worker.

    A response delay, a phase poll, or a log all depend on timing or on reading
    something that happens either side of the write. The only place a write is
    genuinely in flight is on the wire, so the gate puts its own proxy there and
    the deployment's registered configuration names it.
    """
    body = executable_lines()

    assert 'point_configuration_at_destination "$PROXY_URL"' in body, (
        "the registered configuration does not name the proxy, so no destination write goes through it"
    )
    assert "start_destination_proxy" in function_body("row_cold_start_and_idempotence"), (
        "the proxy is not started before the deployment is pointed at it"
    )
    row = function_body("row_cold_start_and_idempotence")
    assert row.index("start_destination_proxy") < row.index("configure_deployment"), (
        "the deployment is pointed at a proxy that does not exist yet"
    )
    assert "$PROXY_URL" in function_body("configure_deployment"), (
        "the deployment is configured without the proxy it routes through"
    )


def test_the_checks_own_view_of_the_destination_bypasses_the_proxy() -> None:
    """Row 8 proves the write landed by reading the destination itself.

    Read through the proxy, that proof would be a statement about the thing whose
    behaviour is being arranged. Every negative destination assertion in the
    matrix is read the same way and for the same reason.
    """
    configure = function_body("configure_deployment")

    assert "INFRAHUB_DESTINATION_URL=$DESTINATION_URL" in configure
    assert "INFRAHUB_ADDRESS=$DESTINATION_URL" in configure
    assert "INFRAHUB_DESTINATION_URL=$PROXY_URL" not in configure, (
        "the checks read the destination through the proxy whose behaviour row 8 arranges"
    )


def test_row_eight_no_longer_watches_a_phase_go_by() -> None:
    """A phase past planning is not a write in flight, and reading one is a sample.

    `accepted`/`planned` leaves the moment the run starts applying, which is
    before any byte reaches the destination and long before the destination has
    completed anything. The row that killed a worker on that reading interrupted
    whatever the run happened to be doing.
    """
    source = code_of(CHECKS / "start_apply.py")

    assert "phase" not in source, "the row still reads a run phase to decide it has reached its write"
    # Read from the kit's import list rather than as a substring: this row has
    # polling intervals of its own for the handshake, and what may not come back
    # is the deployment-polling the old precondition was built out of.
    imported = re.search(r"from kit import \(([^)]*)\)", (CHECKS / "start_apply.py").read_text(encoding="utf-8"))
    assert imported is not None, "the row no longer imports the kit"
    taken = {name.strip().rstrip(",") for name in imported.group(1).split()}
    assert "POLL_SECONDS" not in taken, "the row still polls the deployment for its own precondition"
    assert "RUN_TIMEOUT_SECONDS" not in taken, "the row still bounds a wait for a run it does not wait for"


def test_row_eight_arms_the_proxy_and_proves_the_write_upstream_before_it_names_the_run() -> None:
    """The run id is the driver's licence to kill a worker. It is issued last.

    Between arming and that licence: the proxy claimed a mutation, forwarded it,
    the destination completed it, and this check read the planted value out of
    the destination directly. A run named before any of that is a run the driver
    would interrupt for nothing.
    """
    source = code_of(CHECKS / "start_apply.py")
    tree = ast.parse(source)

    for step in ("signal_proxy(ARM)", "await_acceptance(", "await_upstream(", "require_planted_value_reached("):
        assert step in source, f"row 8 never reaches {step}"
    assert source.index("signal_proxy(ARM)") < source.index("client.sync("), (
        "the proxy is armed after the run that produces the mutation was submitted"
    )

    def calls(name: str) -> list[ast.Call]:
        return [
            node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", None) == name
        ]

    named = calls("print")
    proven = calls("require_planted_value_reached")
    assert len(named) == 1, "the row does not name the run exactly once"
    assert len(proven) == 1, "the row does not prove the held write landed exactly once"
    assert proven[0].lineno < named[0].lineno, "the run is named before the write it names was proven to have landed"


def test_row_eights_proof_reads_the_destination_itself_on_the_branch_the_plan_writes() -> None:
    """A value on another branch says nothing about the write this row held.

    The apply writes to the branch the declared configuration names, and that is
    the only side where the held mutation can have landed.
    """
    source = code_of(CHECKS / "start_apply.py")

    assert "planned_branch()" in source, "the proof does not read the branch the plan writes to"
    assert "sdk()" in source, "the proof does not read the destination itself"
    assert "planted_attribute(" in source, "the proof does not read the attribute the row planted"


def test_row_eight_gives_up_on_a_run_that_finished_without_reaching_the_proxy() -> None:
    """A sync that never issued a mutation leaves this check waiting out its whole bound.

    And the reason is worth saying: a run that finished without any write is a
    finding about the deployment, not a slow host.
    """
    source = code_of(CHECKS / "start_apply.py")

    assert "finished_at" in source, "a run that ended without writing is waited out rather than reported"


# ---------------------------------------------------------------------------
# B2 — one absolute budget from the accepted mutation to the acknowledgement
# ---------------------------------------------------------------------------
def test_one_absolute_budget_covers_everything_after_the_proxy_accepted_the_mutation() -> None:
    """Everything from the accept to the acknowledgement runs against one clock.

    The clock that matters is the worker's own: the SDK gives one destination
    call sixty seconds, and a hold that outlived it would be recorded as a
    transport timeout rather than as the interruption this row induces. A
    sequence of resettable waits obeys every individual bound and still outlives
    that one.

    The instant is recorded by the proxy, because the proxy is the only party
    that knows when it accepted the mutation. The check and this driver both read
    their remaining time from that one instant.
    """
    source = code_of(CHECKS / "start_apply.py")

    assert "PROXY_BUDGET_SECONDS" in source, "the row names no budget"
    assert not re.search(r"^PROXY_BUDGET_SECONDS = ", source, re.MULTILINE), (
        "the row declares a budget of its own, which can drift from the one the proxy holds"
    )
    assert "from destination_proxy import" in (CHECKS / "start_apply.py").read_text(encoding="utf-8"), (
        "the check does not take the budget from the proxy that holds it"
    )
    for mechanism in ("signal.SIGALRM", "signal.ITIMER_REAL"):
        assert mechanism in source, f"the budget cannot interrupt a blocking destination read without {mechanism}"
    # The arming call, not merely the name: the timer is put back in a `finally`,
    # and that restoration alone satisfies any claim about the name appearing.
    assert "signal.setitimer(signal.ITIMER_REAL, remaining)" in source, (
        "nothing arms an interval timer for what is left of the budget"
    )

    tree = ast.parse(source)
    entered = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        for item in node.items
        if isinstance(item.context_expr, ast.Call)
        and getattr(item.context_expr.func, "id", None) == "coordination_budget"
    ]
    assert len(entered) == 1, "the row does not enter one budget covering everything after the accept"
    guarded = ast.unparse(entered[0])
    for covered in ("await_upstream(", "require_planted_value_reached("):
        assert covered in guarded, f"{covered} happens outside the one budget"

    # Derived from the recorded instant rather than started where it is entered:
    # a budget armed on entry would begin after the forwarding it is meant to cover.
    assert "accepted_at" in ast.unparse(entered[0].items[0].context_expr), (
        "the budget does not begin at the instant the proxy recorded accepting the mutation"
    )


def test_the_forwarding_the_budget_covers_is_bounded_by_the_budget_and_not_by_a_clock_of_its_own() -> None:
    """A thirty-second forwarding inside a twenty-second budget is not inside it.

    The budget starts when the mutation is accepted and the forwarding is the
    first thing it covers. Given a timeout of its own that is longer, the
    forwarding alone can outlast the whole coordination -- and the row then
    reports a stalled destination instead of the interruption it arranged.
    """
    source = code_of(PROXY)
    tree = ast.parse(source)

    def body_of(name: str) -> str:
        return ast.unparse(
            next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
        )

    claimed = body_of("forward_within_budget")
    assert "remaining_budget(accepted_at)" in claimed, (
        "the claimed request is forwarded under a clock that is not the one budget"
    )
    assert "timeout=remaining" in claimed, "the forwarding ignores whatever the budget left it"
    assert "UNCLAIMED_TIMEOUT_SECONDS" not in claimed, "the claimed forwarding takes a timeout of its own"
    assert "forward_within_budget(" in body_of("_hold_claimed"), "the held write is not forwarded under the budget"
    # And the request nobody armed for is bounded, but by the ordinary bound: it
    # is not part of the coordination and must not consume the budget either.
    assert "timeout=UNCLAIMED_TIMEOUT_SECONDS" in body_of("_pass_through")
    assert "remaining_budget" not in body_of("_pass_through")

    # And nothing that is not the budget may be as long as the budget: a constant
    # at or above it is a second clock over the same interval whatever it is named.
    declared = PROXY.read_text(encoding="utf-8")
    stated = re.search(r"^PROXY_BUDGET_SECONDS = ([0-9.]+)$", declared, re.MULTILINE)
    assert stated is not None, "the proxy names no single coordination budget"
    budget = float(stated.group(1))
    for name, value in re.findall(r"^([A-Z_]+_SECONDS) = ([0-9.]+)$", declared, re.MULTILINE):
        if name in {"PROXY_BUDGET_SECONDS", "UNCLAIMED_TIMEOUT_SECONDS"}:
            continue
        assert float(value) < budget, f"{name} is another clock as long as the budget it sits inside"


def test_an_unclaimed_forwarding_stays_under_the_bound_its_own_caller_allows() -> None:
    """It is not part of row 8's budget, so what bounds it is the caller's SDK timeout.

    Longer than that and the deployment reports its own client timing out for a
    forwarding this proxy could still have finished, which would make every row
    that writes depend on this fixture's patience rather than on the product's.
    """
    source = PROXY.read_text(encoding="utf-8")
    declared = re.search(r"^UNCLAIMED_TIMEOUT_SECONDS = ([0-9.]+)$", source, re.MULTILINE)
    assert declared is not None, "an unclaimed request is forwarded under no bound at all"

    given = re.search(r'"timeout": (\d+)', (REPO_ROOT / "infrahub_sync" / "adapters" / "infrahub.py").read_text())
    assert given is not None
    assert float(declared.group(1)) < float(given.group(1)), (
        "an unclaimed forwarding may outlive the caller waiting on it"
    )


def test_the_budget_running_out_during_the_forwarding_is_recorded_as_the_budget_running_out() -> None:
    """A timeout mid-forward and an unreachable destination are different findings.

    Only one of them is the budget, and only the budget has a sentence both sides
    of the handshake share. Reported as the other, the driver would wait out its
    whole backstop for an acknowledgement that was never coming.
    """
    source = code_of(PROXY)
    tree = ast.parse(source)

    assert "def record_expiry" in source, "there is no single place the budget running out is recorded"
    expire = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "record_expiry")
    body = ast.unparse(expire)
    assert "signal_proxy(EXPIRED" in body, "an expiry is not recorded, so the driver cannot read it"
    assert "disarm()" in body, "an expiry leaves the proxy armed for whatever comes next"
    assert "ACKNOWLEDGED" not in body, "an expiry answers as though the coordination completed"
    assert "UPSTREAM_COMPLETED" not in body, "an expiry is recorded as a completed write"
    # The answer is the handler's, because recording an expiry and answering the
    # request are different concerns -- but it may never be a success.
    forwarding = ast.unparse(
        next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_hold_claimed")
    )
    for answered in re.findall(r"self\._answer\((\w+)", forwarding):
        assert answered != "HELD_ACKNOWLEDGED", "a held write is answered as though it completed"
    assert "self._answer(WITHHELD_STATUS" in forwarding, "an expiry leaves the held request without an answer"

    # Both places the budget can run out, and each anchored to where it runs out.
    # "the function mentions an expiry somewhere" is satisfied by the pre-check
    # alone, which is a different moment from a forwarding cut short by the clock.
    held = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "await_release")
    assert "record_expiry()" in ast.unparse(held), "a budget that ran out while holding is reported as something else"

    forwarding = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_hold_claimed"
    )
    handlers = [
        handler
        for node in ast.walk(forwarding)
        if isinstance(node, ast.Try)
        for handler in node.handlers
        if handler.type is not None and "HTTPError" in ast.unparse(handler.type)
    ]
    assert handlers, "nothing catches a forwarding that could not complete"
    assert "record_expiry()" in ast.unparse(handlers[0]), (
        "a forwarding the budget cut short is reported as a destination that could not be reached"
    )


def test_the_budget_expiry_is_reported_as_one_fixed_sentence_by_both_sides() -> None:
    """Two sides of one budget must not have two accounts of it running out.

    The check refuses with it; this driver fails with it when the proxy records
    that the same budget expired while the driver was waiting. One sentence,
    declared once, so neither can drift into claiming something else happened.
    """
    sentence = re.search(r'^BUDGET_SENTENCE = \(?\s*"([^"]+)"', PROXY.read_text(encoding="utf-8"), re.MULTILINE)
    assert sentence is not None, "the proxy declares no fixed sentence for its budget running out"

    assert "BUDGET_SENTENCE" in code_of(CHECKS / "start_apply.py"), "the check does not refuse with that sentence"
    assert sentence.group(1) in executable_lines(), "the driver does not fail with the sentence the budget declares"


def test_the_driver_waits_once_for_the_acknowledgement_and_never_resets_that_wait() -> None:
    """A loop that restarts its bound is how a twenty-second budget becomes a minute."""
    row = function_body("row_recovery").replace("\\\n", " ")

    waits = [line for line in row.splitlines() if "await_proxy" in line]
    assert len(waits) == 1, f"row 8 waits on the proxy {len(waits)} times, so its budget can be spent twice"
    assert "$ROW8_ACK_TIMEOUT" in waits[0], "the one wait is unbounded"

    backstop = re.search(r"^ROW8_ACK_TIMEOUT=([0-9]+)$", driver(), re.MULTILINE)
    assert backstop is not None, "the driver bounds nothing while the proxy holds a write"
    budget = re.search(r"^PROXY_BUDGET_SECONDS = ([0-9.]+)$", PROXY.read_text(encoding="utf-8"), re.MULTILINE)
    assert budget is not None
    # Longer than the budget, and only as a backstop for a proxy that died without
    # recording anything: the proxy's own expiry marker is what this row reports.
    assert int(backstop.group(1)) > float(budget.group(1)), (
        "the driver gives up before the proxy can record the budget expiring, so the row reports the wrong reason"
    )


def test_the_driver_reads_the_proxys_own_expiry_rather_than_timing_the_budget_again() -> None:
    """One budget means one holder of it. The driver reads the holder's answer."""
    helper = function_body("await_proxy")

    assert "$PROXY_EXPIRED" in helper, "the driver cannot tell an expired budget from a slow one"
    assert "PROXY_EXPIRED=" in driver()
    assert proxy_constant("EXPIRED") in driver(), "the driver names an expiry file the proxy does not write"


def test_the_driver_names_the_control_files_the_proxy_actually_writes() -> None:
    """Two names for one handshake is a handshake that never completes."""
    body = driver()

    for declared in ("READY", "ACCEPTED_AT", "RELEASE", "ACKNOWLEDGED", "EXPIRED"):
        assert proxy_constant(declared) in body, f"the driver does not name the proxy's {declared} file"


# ---------------------------------------------------------------------------
# B3 — row 8's own containers, its own writable directory, and nothing broader
# ---------------------------------------------------------------------------
def test_the_generic_check_is_still_given_nothing_writable() -> None:
    """`check` mounts everything read-only, and row 2 asserts the deployment does too."""
    body = executable_lines()
    generic = body[body.index("check() {") : body.index("destination_check() {")]

    assert "/control" not in generic, "every check is given a writable control mount"


@pytest.mark.parametrize("owner", ["coordinated_check", "row8_check"])
def test_only_a_row_that_owns_a_control_directory_receives_one(owner: str) -> None:
    """Two rows coordinate with this gate, and each gets the directory of its own row."""
    body = executable_lines()

    assert f"{owner}() {{" in body, f"there is no row-owned {owner}"
    assert "/control" in function_body(owner), f"{owner} coordinates through no writable channel"


def test_row_eights_check_and_proxy_are_named_exactly_and_recorded_before_creation() -> None:
    """A `docker run` container carries no instance label, so nothing else can find it.

    An interrupted creation is the case this is for: the identity has to be
    written down before the container can exist, or a container this run made is
    one no teardown will ever name.
    """
    body = executable_lines()

    assert "PROXY_CONTAINER=clean-host-proxy-$RUN_IDENTITY" in body, "the proxy has no exact name of its own"
    assert "ROW8_CHECK_CONTAINER=clean-host-row8-$RUN_IDENTITY" in body, "row 8's check has no exact name of its own"
    for creator, name in (("start_destination_proxy", "$PROXY_CONTAINER"), ("row8_check", "$ROW8_CHECK_CONTAINER")):
        created = function_body(creator)
        assert f'--name "{name}"' in created, f"{creator} creates a container with no identity to remove"
        assert created.index("$WORK/created") < created.index("docker run"), (
            f"{creator} creates before it records what it is about to create"
        )


@pytest.mark.parametrize(
    ("creator", "name"),
    [
        ("start_destination_proxy", "$PROXY_CONTAINER"),
        ("row8_check", "$ROW8_CHECK_CONTAINER"),
        # Row 6's too. It is a precondition path to rows 8 to 11 and its container
        # is part of the same global cleanup proof, so the same rule holds for it.
        ("coordinated_check", "$ROW6_CHECK_CONTAINER"),
    ],
)
def test_a_name_this_run_derived_is_proven_free_rather_than_cleared_by_force(creator: str, name: str) -> None:
    """`docker rm --force` on a derived name deletes a container this run did not create.

    The name is derived from an identity this run owns, so a collision is
    unlikely — and a gate whose every claim rests on reaching nothing it did not
    make cannot answer an unlikely collision by destroying the evidence of it.
    Proven free, and refused when it is not.

    Custody is given up again on that refusal, so the teardown does not go on to
    reach a container this run never created either.
    """
    created = function_body(creator)

    assert "docker rm --force" not in created, f"{creator} clears a name it does not own by force"
    assert f'container_name_taken "{name}"' in created, f"{creator} does not prove the name was free"
    assert f"{name.lstrip('$')}=\n" in created + "\n", f"{creator} keeps custody of a name it refused to take"
    assert created.index("container_name_taken") < created.index("docker run"), (
        f"{creator} creates the container before it establishes the name was free"
    )


def test_a_host_that_could_not_say_whether_a_name_is_free_ends_the_run() -> None:
    """A refused query and a free name read the same, and one of them destroys state."""
    helper = function_body("container_name_taken").replace("\\\n", " ")

    assert "|| fail" in helper, "a host that could not answer reads as a free name"
    assert "grep -qxF" in helper, "the name filter is a substring, so a partial match reads as this run's container"


@pytest.mark.parametrize(
    ("preparer", "directory"),
    [("create_proxy_control", "$PROXY_CONTROL"), ("coordinated_check", "$ROW6_CONTROL")],
)
def test_a_control_directory_is_writable_by_the_candidates_user_and_nothing_else_is(
    preparer: str, directory: str
) -> None:
    """The image runs as 10001, so a directory this host made is not writable by it.

    Both control directories, because a container running as that user writes each
    of them, and a check that cannot record a state the driver waits for takes the
    matrix down with a filesystem error about this harness rather than a finding.

    Made writable for that user explicitly, and for that one directory: a mode
    applied further up would widen what this gate hands a container.
    """
    prepared = function_body(preparer)
    narrowing = function_body("narrow_control_access")

    assert f'narrow_control_access "{directory}"' in prepared, (
        f"{preparer} leaves its control directory unwritable by the candidate's user"
    )
    assert "CANDIDATE_UID=10001" in driver(), "the driver does not name the identity those containers keep"
    # Shared through a group rather than handed over: `chown` to another user needs
    # privileges an ordinary account does not have, and the live matrix refused
    # exactly there.
    assert 'chgrp "$CONTROL_GROUP" "$1"' in narrowing
    assert "|| fail" in narrowing, "a control directory that could not be prepared is used anyway"
    # One directory per call, named by the caller: a mode applied to anything but
    # the argument would widen what this gate hands a container.
    for line in narrowing.splitlines():
        if "chmod" in line or "chgrp" in line:
            assert '"$1"' in line, f"a mode reaches beyond the directory it was given: {line.strip()}"


def test_the_proxy_is_published_on_a_port_the_engine_chose_and_exactly_one_mapping_is_taken() -> None:
    """A fixed port is a collision with whatever else this host publishes.

    And a mapping read without being counted is a host address assembled from
    whichever line came back first — the IPv4 and IPv6 publications of one
    container are two lines for one port, and two ports would be two answers.
    """
    started = function_body("start_destination_proxy")
    discovered = function_body("proxy_host_port")

    assert '--publish "$PROXY_CONTAINER_PORT"' in started, "the proxy is published on a port this gate chose"
    assert "docker port" in discovered, "the published port is assumed rather than discovered"
    assert "sort -u" in discovered, "one container's two publications are read as two ports"
    assert "-eq 1" in discovered, "the number of mappings is never checked"
    assert "|| fail" in discovered, "a host that could not answer reads as no mapping"


def test_the_proxy_records_nothing_about_the_requests_it_forwards() -> None:
    """Every request through it carries the destination token, and one carries a write."""
    source = code_of(PROXY)

    assert "def log_message" in source, "the request logger the base handler installs is left in place"
    assert "print(" not in source, "the proxy prints something, and everything through it is credentialed"


def test_row_eights_containers_and_control_state_are_removed_on_every_path() -> None:
    """Success, refusal, a signal, and the global teardown are four different paths out."""
    cleanup = function_body("cleanup")

    for step in ("release_proxy_hold", "stop_row8_containers", "discard_control_state"):
        assert step in cleanup, f"the teardown does not reach {step}"
    stopper = function_body("stop_row8_containers")
    assert 'remove_owned_fixture_container "$named"' in stopper, "nothing removes either container"
    for name in ("$ROW8_CHECK_CONTAINER", "$PROXY_CONTAINER"):
        assert name in stopper, f"nothing removes {name}"


# ---------------------------------------------------------------------------
# B4 — row 9's foreign volume, and the identity a reset replaces
# ---------------------------------------------------------------------------
def test_the_foreign_volume_is_named_for_an_identity_this_run_owns() -> None:
    """A fixed name is some other run's volume, or an operator's.

    Row 9's claim is that a reset leaves what it does not own alone. A volume
    that was already on this host proves that of a volume this run never made,
    and teardown would then remove foreign state.
    """
    body = executable_lines()

    assert not re.search(r"^FOREIGN_VOLUME=infrahub-sync-clean-host-foreign$", body, re.MULTILINE), (
        "the foreign volume carries a fixed name any other run would collide with"
    )
    assert "FOREIGN_VOLUME=infrahub-sync-clean-host-foreign-$INSTANCE" in body, (
        "the foreign volume is not named for an identity this run owns"
    )


def test_the_foreign_volumes_absence_is_proven_before_it_is_created() -> None:
    """A name already taken makes its survival a statement about someone else's volume."""
    created = function_body("create_foreign_volume")

    assert created.index("docker volume inspect") < created.index("docker volume create"), (
        "the row creates the volume before it has established that the name was free"
    )
    assert "|| fail" in created or "fail " in created


def test_the_foreign_volume_is_removed_only_when_this_run_created_it() -> None:
    """Teardown removing a volume it did not create is this gate destroying foreign state."""
    body = executable_lines()
    remover = function_body("remove_foreign_volume")

    # The guard, not the name: the removal also clears this variable, and that
    # clearing alone satisfies any claim about the name appearing in the body.
    assert '[ -n "$FOREIGN_VOLUME_INTENDED" ] || return 0' in remover, (
        "removal is not gated on this run having set out to create it"
    )
    assert "FOREIGN_VOLUME_INTENDED=yes" in function_body("create_foreign_volume"), (
        "the row never records that it set out to create anything"
    )
    assert "remove_foreign_volume" in teardown_body(), "the teardown does not remove the volume the row created"
    assert 'docker volume rm "$FOREIGN_VOLUME"' not in teardown_body(), (
        "the teardown removes the name directly, without knowing whether this run created it"
    )
    assert body.count("remove_foreign_volume") >= 3, "the row and the teardown do not share one gated removal"


def test_the_old_instance_is_proven_gone_before_a_new_identity_replaces_it() -> None:
    """After the identity is replaced nothing can ask the question again.

    Whatever the old identity still owned is then a resource no teardown will
    ever name, because every removal this gate makes is keyed on an identity it
    is holding.
    """
    reinitialise = function_body("reinitialise_deployment")

    assert reinitialise.index("require_instance_gone") < reinitialise.index("INSTANCE="), (
        "the identity is replaced before what it owned was proven gone"
    )
    proof = function_body("instance_resources").replace("\\\n", " ")
    queried = [line for line in proof.splitlines() if re.match(r"\s*docker\s", line)]
    assert len(queried) == 3, "containers, volumes and networks are not all asked about"
    for asked in ("docker ps", "docker volume ls", "docker network ls"):
        assert any(asked in line for line in queried), f"{asked} is never asked, so that kind is never checked"


def test_the_destination_fixture_project_is_named_for_an_identity_this_run_owns() -> None:
    """A fixed project name is a collision, and `down --volumes` on it is destructive."""
    body = executable_lines()

    assert not re.search(r"^FIXTURE_PROJECT=infrahub-sync-clean-host-destination$", body, re.MULTILINE), (
        "the fixture project carries a fixed name another run would tear down"
    )
    assert "FIXTURE_PROJECT=infrahub-sync-clean-host-destination-$RUN_IDENTITY" in body, (
        "the fixture project is not named for an identity this run owns"
    )
    assert "RUN_IDENTITY=$INSTANCE" in function_body("row_artifact_identity"), (
        "this run takes no stable identity of its own to name what it creates"
    )


# ---------------------------------------------------------------------------
# B5 — one teardown, and a signal that cannot become a pass
# ---------------------------------------------------------------------------
def test_a_signal_cannot_leave_this_gate_reporting_success() -> None:
    """`trap cleanup EXIT INT TERM` reads `$?` from whatever ran last.

    On an interrupt that is routinely zero, and the teardown then exits zero: the
    gate reports a pass for a matrix it did not finish.
    """
    body = executable_lines()

    assert "trap cleanup EXIT INT TERM" not in body, "a signal is handled by the same trap that reads the exit status"
    assert re.search(r"^\s*trap cleanup EXIT$", body, re.MULTILINE), "the exit trap is not separated from the signals"
    assert "on_signal" in body, "there is no signal handler"
    handler = function_body("on_signal")
    assert re.search(r"exit \d+", handler) or 'exit "$1"' in handler, "the signal handler leaves the status alone"
    assert "exit 0" not in handler


def test_the_teardown_runs_once_however_it_is_reached() -> None:
    """The signal handler exits, which runs the exit trap: cleanup can be re-entered."""
    cleanup = function_body("cleanup")

    assert "CLEANED" in cleanup, "cleanup can run twice and report the second run's verdict"


def test_the_teardown_releases_the_held_write_before_it_waits_on_the_deployment() -> None:
    """A worker blocked on a held response answers nothing, including a diagnostic."""
    cleanup = function_body("cleanup")

    assert cleanup.index("release_proxy_hold") < cleanup.index("capture_diagnostic"), (
        "the teardown reads a deployment whose write it is still holding"
    )


def test_the_destination_teardown_no_longer_swallows_its_own_failure() -> None:
    """A fixture left running is the next run's destination, seeded by someone else."""
    stopper = function_body("stop_destination")
    cleanup = function_body("cleanup")

    assert "|| true" not in stopper, "the destination teardown reports success whatever happened"
    assert "if ! stop_destination" in cleanup, "the teardown does not notice a destination it could not stop"
    # A cleanup defect may turn a passing run into a failure. It may never replace
    # the failure that caused the cleanup.
    assert cleanup.count('[ "$status" -ne 0 ] || status=1') >= 3


def test_the_teardown_verifies_the_absence_of_everything_this_run_tracked() -> None:
    """Containers and volumes were the list. A network, a port and a fixture project are not."""
    remaining = function_body("remaining_resources")

    assert "remaining_resources" in teardown_body(), "the teardown judges completeness by an older list"
    for tracked in ("publish=$PROXY_HOST_PORT", "com.docker.compose.project=$FIXTURE_PROJECT"):
        assert tracked in remaining, f"{tracked} is created by this run and never checked for absence"
    assert "network ls" in function_body("instance_resources"), "a network this run created is never checked"
    assert "PRIOR_INSTANCES" in remaining, "the identities earlier rows discarded are never checked again"

    # The record each creator wrote before it created anything, not the variables
    # holding those names: a removal that could not land clears its variable, and
    # the identity would then be checked for absence by nobody.
    assert '< "$WORK/created"' in remaining, "the unlabelled containers are checked through clearable variables"
    for creator in ("start_destination_proxy", "row8_check", "coordinated_check"):
        assert "$WORK/created" in function_body(creator), f"{creator} records no identity teardown can check"


def test_the_teardown_reaches_only_identities_this_run_holds() -> None:
    """Every filter names something this run generated, not a prefix of what it looks like."""
    owned = (
        "$1",
        "$INSTANCE",
        "$RUN_IDENTITY",
        "$FIXTURE_PROJECT",
        "$PROXY_CONTAINER",
        "$ROW8_CHECK_CONTAINER",
        "$ROW6_CHECK_CONTAINER",
        "$PROXY_HOST_PORT",
        "$FOREIGN_VOLUME",
        "$identity",
        "$named",
    )
    for helper in ("cleanup", "remaining_resources", "instance_resources", "stop_row8_containers"):
        for line in function_body(helper).replace("\\\n", " ").splitlines():
            if "--filter" in line or "--project-name" in line:
                assert any(name in line for name in owned), f"{helper} reaches beyond this run: {line.strip()}"


# ---------------------------------------------------------------------------
# B6 — what row 11 actually sweeps
# ---------------------------------------------------------------------------
def test_the_failure_evidence_row_eleven_sweeps_is_actually_collected() -> None:
    """Configurations and a status line are not failures, and row 11 claims failures.

    Its acceptance names the product's own failure evidence and what an
    orchestration console shows. A check that printed neither swept bytes that
    could not have carried a credential from either.
    """
    source = code_of(CHECKS / "reported_failures.py")

    assert "get_results(" in source, "no product failure evidence is read"
    assert "prefect_executions" in source, "nothing links a run to the orchestration that ran it"
    assert "flow_runs" in source, "no Prefect-visible state is read"
    assert "logs" in source, "no Prefect-visible log is read"
    assert "list_configs()" not in source or "get_results(" in source


def test_the_failure_evidence_collection_is_bounded() -> None:
    """A stream is not evidence a row can sweep before the deployment is destroyed."""
    source = code_of(CHECKS / "reported_failures.py")

    assert re.search(r"MAX_RUNS = \d+", source), "the run collection is unbounded"
    assert re.search(r"MAX_LOG_ENTRIES = \d+", source), "the Prefect log collection is unbounded"


def test_a_failure_evidence_collection_that_did_not_complete_is_not_a_shorter_sweep() -> None:
    """A refused collection and a deployment that reported nothing read the same otherwise."""
    body = executable_lines()

    assert "check reported_failures > " not in body or "|| true" not in body.split("reported_failures")[1][:80], (
        "the collection's own failure is swallowed and the sweep proceeds over what it happened to get"
    )
    capture = function_body("capture_deployment_evidence")
    assert "reported_failures" in capture, "the failure evidence is not collected beside the log"
    assert capture.count("|| fail") == 2, "a capture that could not be taken does not end the run"
    assert "|| true" not in capture, "a capture that could not be taken is read as nothing to sweep"


def test_the_raw_failure_evidence_is_swept_and_then_kept_by_nobody() -> None:
    """These bytes are the product's own failure documents and an orchestration log.

    They exist to be swept and for nothing else. Retained, they would be exactly
    the artifact row 11 exists to say the gate does not leave behind.
    """
    row = function_body("row_secrets")

    assert '"$EVIDENCE_DIR"/*.failures' in row, "the collected failure evidence is never swept"
    # Anchored to the row's own last two statements, not to an ordering: the row
    # discards on each refusing path too, so "some discard follows some sweep"
    # holds even when the passing path keeps the bytes. What has to be true is
    # that nothing survives a sweep that found nothing.
    statements = [line.strip() for line in row.replace("\\\n", " ").splitlines() if line.strip()]
    assert statements[-2].startswith("end_of_sweep"), (
        "the raw evidence outlives the sweep that was the only reason to have it"
    )
    assert statements[-1].startswith("report "), "the row reports before it has discarded what it swept"
    assert "discard_raw_evidence" in function_body("end_of_sweep")
    assert "discard_raw_evidence" in function_body("cleanup"), (
        "a row that failed before the sweep leaves the raw evidence on the host"
    )


def test_a_sweep_target_that_is_missing_ends_the_row_rather_than_narrowing_it() -> None:
    """An unmatched glob clears nothing and reads exactly like a clean file."""
    row = function_body("row_secrets")

    assert '[ ! -f "$target" ]' in row, "the sweep reads whatever targets happened to exist"
    assert "bytes nobody read" in row, "a missing target is skipped rather than reported"


def test_the_failure_evidence_never_reaches_the_terminal() -> None:
    """Raw product evidence is what the sweep is for; a terminal is not swept."""
    capture = function_body("capture_deployment_evidence")

    for line in capture.splitlines():
        if "reported_failures" in line:
            assert ">" in line, f"the collected evidence is printed rather than captured: {line.strip()}"
            assert "2>" in line, f"whatever the collection said reaches the terminal: {line.strip()}"


# ---------------------------------------------------------------------------
# B2 — the budget as an absolute deadline, not a scalar handed to a client
# ---------------------------------------------------------------------------
def test_the_upstream_exchange_is_bounded_as_a_whole_rather_than_per_phase() -> None:
    """`httpx` applies a scalar timeout to connect, read, write and pool separately.

    Four phases of twenty seconds each obey a twenty-second timeout and take
    eighty. The budget is one absolute deadline, so the exchange is bounded as a
    unit — and in a threaded handler that cannot be an interval timer, because
    Python delivers signals only to the main thread.
    """
    source = code_of(PROXY)
    tree = ast.parse(source)

    assert "def forward_within_budget" in source, "nothing bounds the exchange as a whole"
    bounded = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "forward_within_budget"
    )
    body = ast.unparse(bounded)
    assert "result(timeout=" in body, "the exchange is bounded by whatever the client does with a scalar"
    assert "remaining_budget(accepted_at)" in body, "the outer deadline is not the one budget"
    assert "signal.setitimer" not in body, "a handler thread cannot be reached by an interval timer"


def test_the_budget_is_rechecked_at_each_state_it_licenses() -> None:
    """A step that passed the check on entry may finish after the budget ended.

    Both durable states are licences: `proxy-upstream-completed` tells the check
    to go and prove a write, and `proxy-acknowledged` tells the driver the
    coordination completed. Either recorded late is a claim about a budget that
    no longer existed.
    """
    source = code_of(PROXY)
    tree = ast.parse(source)

    for recorder, licensed in (
        ("record_upstream_completed", "UPSTREAM_COMPLETED"),
        ("await_release", "ACKNOWLEDGED"),
    ):
        node = next(found for found in ast.walk(tree) if isinstance(found, ast.FunctionDef) and found.name == recorder)
        body = ast.unparse(node)
        assert "remaining_budget(accepted_at) <= 0" in body, f"{recorder} records {licensed} without rechecking"
        assert "record_expiry()" in body, f"{recorder} has no way to classify a late step as an expiry"
        # The recheck immediately before the state it licenses, with the answer or
        # the release already in hand. Anchored to the last one, because a wait
        # that polls the budget while it waits satisfies "somewhere before" on its
        # own -- and then the step it licenses is recorded without a recheck at all.
        assert body.rindex("remaining_budget(accepted_at) <= 0") < body.index(licensed), (
            f"{recorder} records {licensed} before its last look at the budget"
        )


def test_a_late_step_is_classified_only_as_an_expiry() -> None:
    """One late event, one name for it. Two names would be two findings for one fact."""
    source = code_of(PROXY)

    for outcome in ("HELD_EXPIRED", "HELD_COMPLETED", "HELD_ACKNOWLEDGED"):
        assert outcome in source, f"the hold has no {outcome} outcome to report"
    expiry = code_of(PROXY)[code_of(PROXY).index("def record_expiry") :]
    assert "signal_proxy(EXPIRED" in expiry.split("def ")[1] if "def " in expiry[4:] else True
    recorded = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "record_expiry"
    )
    body = ast.unparse(recorded)
    assert "signal_proxy(EXPIRED" in body
    assert "disarm()" in body, "an expiry leaves the proxy armed"
    assert "ACKNOWLEDGED" not in body, "an expiry is recorded as an acknowledgement"
    assert "UPSTREAM_COMPLETED" not in body, "an expiry is recorded as a completed write"


# ---------------------------------------------------------------------------
# B3 — narrow access, and control state removed with checked failures
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("preparer", ["create_proxy_control", "coordinated_check"])
def test_no_control_directory_falls_back_to_being_world_writable(preparer: str) -> None:
    """0777 on a host directory is every account on the host, which is not narrow.

    Two parties need it and only two: this driver, and the candidate image's
    user. A host that cannot grant exactly that is a host this gate refuses,
    because the alternative is handing a writable channel to everyone.
    """
    prepared = function_body(preparer) + function_body("narrow_control_access")

    assert "0777" not in prepared, "the control directory falls back to being writable by every account"
    assert '"$CONTROL_GROUP"' in prepared, "the group the candidate's containers reach it through is not established"
    assert "|| fail" in prepared, "a directory that could not be narrowed is used anyway"
    assert "2>/dev/null" not in function_body("narrow_control_access"), (
        "the grant's own failure is hidden, which is how a fallback creeps back in"
    )


def test_the_control_directories_are_removed_only_after_the_containers_writing_them_stop() -> None:
    """A removed directory under a running container is a mount that still exists.

    Both writers are `docker run` containers of this gate's own, so both are
    stopped first and the removal is then a removal rather than a race.
    """
    cleanup = function_body("cleanup")

    for stopper in ("stop_row6_check", "stop_row8_containers"):
        assert cleanup.index(stopper) < cleanup.index("discard_control_state"), (
            f"{stopper} runs after the directory it writes is removed"
        )


def test_both_control_directories_are_removed_and_their_removal_is_checked() -> None:
    """`rm -rf` reports success for a great many things it did not remove."""
    discard = function_body("discard_control_state")

    for directory in ("$PROXY_CONTROL", "$ROW6_CONTROL"):
        assert directory in discard, f"{directory} is left on the host"
    # The verdict comes from the directory being gone, not from `rm`'s status:
    # `rm -rf` exits zero for a great many things it did not remove.
    assert '[ -d "$control" ]' in discard, "a directory is removed without checking that it went"
    assert "return 1" in discard, "a removal that did not happen reports success"


def test_a_control_directory_left_behind_is_reported_as_a_resource_still_present() -> None:
    """It is private, writable, and this run made it. That is a resource."""
    remaining = function_body("remaining_resources")

    for directory in ("$PROXY_CONTROL", "$ROW6_CONTROL"):
        assert directory in remaining, f"{directory} is never checked for absence"


def test_a_control_state_removal_that_failed_can_only_turn_a_pass_into_a_failure() -> None:
    """A teardown defect must never replace the diagnosis that caused the teardown."""
    cleanup = function_body("cleanup")

    guarded = [line for line in cleanup.splitlines() if "discard_control_state" in line and "if !" in line]
    assert guarded, "the control-state removal's own failure is not noticed"
    following = cleanup[cleanup.index("if ! discard_control_state") :]
    assert '[ "$status" -ne 0 ] || status=1' in following.split("fi")[0], (
        "a failed removal replaces the original failure instead of only failing a passing run"
    )


# ---------------------------------------------------------------------------
# B4 — custody of the foreign volume across a signal
# ---------------------------------------------------------------------------
def test_custody_of_the_foreign_volume_is_intended_before_it_is_created() -> None:
    """A signal between the creation and a variable set after it loses the volume.

    `docker volume create` returns, the run is interrupted before the shell
    records that it succeeded, and the teardown then has no licence to remove a
    volume this run made. Intent is recorded first, so the window holds nothing.
    """
    created = function_body("create_foreign_volume")

    assert "FOREIGN_VOLUME_INTENDED=yes" in created, "the row records no intent to create anything"
    assert created.index("FOREIGN_VOLUME_INTENDED=yes") < created.index("docker volume create"), (
        "custody is taken after the creation, so a signal in between loses the volume"
    )


def test_the_removal_is_licensed_by_a_label_this_run_wrote_and_not_by_a_variable() -> None:
    """Intent is not ownership. What is on the volume decides whether it is ours.

    A name can be intended and belong to something else by the time teardown
    reads it; a label this run wrote and reads back cannot. So the removal asks
    the volume, and a volume that does not answer with this run's identity is
    reported rather than removed.
    """
    body = executable_lines()
    remover = function_body("remove_foreign_volume")

    assert "FOREIGN_VOLUME_LABEL=" in body, "the volume carries no identity this run can read back"
    assert '--label "$FOREIGN_VOLUME_LABEL=$RUN_IDENTITY"' in function_body("create_foreign_volume"), (
        "the volume is created without the identity that licenses removing it"
    )
    assert "foreign_volume_is_ours" in remover, "the removal is licensed by a shell variable"
    licensed = function_body("foreign_volume_is_ours")
    assert "docker volume inspect" in licensed, "ownership is asserted rather than read from the volume"
    assert "$FOREIGN_VOLUME_LABEL" in licensed
    # The comparison, not the name: the helper also refuses when this run has no
    # identity at all, and that guard alone satisfies any claim about the name
    # appearing. What has to hold is that the label *equals* this run's identity.
    assert '[ "$owner" = "$RUN_IDENTITY" ]' in licensed, (
        "any labelled volume would satisfy this, including another run's"
    )


def test_a_volume_that_is_not_this_runs_is_reported_and_never_removed() -> None:
    """This row exists to prove the gate leaves foreign state alone. Including here."""
    remover = function_body("remove_foreign_volume")

    intended = remover.index("FOREIGN_VOLUME_INTENDED")
    assert intended < remover.index("docker volume rm"), "the removal runs before custody is established"
    assert "return 1" in remover, "a volume this run could not account for reads as removed"


# ---------------------------------------------------------------------------
# B5 — a signal during the teardown
# ---------------------------------------------------------------------------
def test_a_signal_arriving_during_the_teardown_cannot_replace_the_original_status() -> None:
    """The exit trap has already read `$?`; a signal handler would then exit over it.

    `on_signal` ends the run with a status of its own, which is right before the
    teardown and wrong inside it: the diagnosis is already in hand and a late
    interrupt would discard it. So the handlers are dropped for the duration.
    """
    cleanup = function_body("cleanup")

    assert "trap '' INT TERM" in cleanup, "a signal during the teardown still reaches a handler that exits"
    assert cleanup.index("status=$?") < cleanup.index("trap '' INT TERM"), (
        "the handlers are dropped before the status they protect has been read"
    )
    ignored = cleanup.index("trap '' INT TERM")
    for destructive in ("capture_diagnostic", "stop_row8_containers", "stop_destination"):
        assert ignored < cleanup.index(destructive), f"{destructive} runs while a signal can still end the run"


# ---------------------------------------------------------------------------
# B6 — the plaintext list of credentials is evidence too
# ---------------------------------------------------------------------------
def test_the_credential_lists_are_named_once_so_both_sweeps_and_the_removal_agree() -> None:
    """Two spellings of one path is a file the removal never reaches."""
    body = executable_lines()

    assert re.search(r"^CANARIES=\S+$", body, re.MULTILINE), "the row's credential list has no name of its own"
    assert re.search(r"^DIAGNOSTIC_CANARIES=\S+$", body, re.MULTILINE), (
        "the diagnostic's credential list has no name of its own"
    )
    assert '"$WORK/canaries"' not in body, "the row's credential list is written out beside its own name"
    assert '"$WORK/diagnostic.canaries"' not in body, (
        "the diagnostic's credential list is written out beside its own name"
    )


def test_the_rows_credential_list_is_removed_on_every_path_out_of_the_sweep() -> None:
    """It holds every credential this run generated, in plaintext, and the run keeps it.

    The contract forbids a generated credential in retained evidence. The list of
    them is not an exception: it exists to be searched and for nothing else, and
    the kit's own working directory is exactly where the sweep does not look.
    """
    row = function_body("row_secrets")

    assert "discard_canaries" in function_body("end_of_sweep"), (
        "the end of the sweep keeps the list of credentials it swept for"
    )
    assert "end_of_sweep" in function_body("fail_after_sweep"), "a refusal after the list exists does not remove it"
    # Every way out from the moment the list exists, and there is no other kind:
    # each refusal goes through the one that removes it first, and the passing
    # path ends the sweep itself. A bare `fail` here would leave the list behind.
    bare = [
        line.strip()
        for line in row.replace("\\\n", " ").splitlines()
        # The passing path's own refusal is the exception, and the only one: the
        # sweep has already ended there, and what it reports is that ending it
        # could not remove the list.
        if re.search(r"(^|\|\| )fail \"", line.strip()) and not line.strip().startswith("end_of_sweep")
    ]
    assert bare == [], f"a refusal leaves the credential list on the host: {bare}"
    assert row.count("fail_after_sweep") >= 4, "the row has fewer refusing paths than it did"
    # And the list's last read is the kit sweep, so the removal follows it.
    assert row.rindex("end_of_sweep") > row.rindex('done < "$CANARIES"'), (
        "the list is removed before the sweep that reads it last"
    )


def test_the_diagnostics_credential_list_is_removed_on_both_of_its_paths() -> None:
    """Withheld or written, the account is finished with and the list is not evidence.

    Two paths, and the leaking one matters most: the diagnostic itself is deleted
    there, and leaving the list of what leaked beside its absence would be the
    same disclosure by a shorter route.
    """
    capture = function_body("capture_diagnostic")

    assert "discard_canaries" in capture, "the diagnostic keeps the list of credentials it swept for"
    withheld = [line for line in capture.splitlines() if "withheld" in line]
    assert len(withheld) == 2, "the diagnostic no longer has two withholding paths"
    assert capture.count("discard_canaries") >= 3, (
        "the diagnostic removes its credential list on some of its paths but not all three"
    )
    assert capture.index("carries_a_canary") < capture.rindex("discard_canaries"), (
        "the list is removed before the sweep that needed it"
    )


def test_neither_credential_list_survives_the_final_teardown() -> None:
    """The backstop for a run that failed before the row that removes its own list."""
    cleanup = function_body("cleanup")
    remaining = function_body("remaining_resources")

    assert "discard_canaries" in cleanup, "a run that failed before row 11 leaves its credential list on the host"
    for named in ("$CANARIES", "$DIAGNOSTIC_CANARIES"):
        assert named in remaining, f"{named} is never checked for absence"


# ---------------------------------------------------------------------------
# B7 — a name is not an identity once `--rm` has freed it
# ---------------------------------------------------------------------------
# The three containers this gate creates with `docker run` carry none of the
# deployment's instance labels, so a name was the only thing that identified
# them. Two of the three use `--rm`, which frees the name as the container exits;
# the third's name is free from the moment its `docker run` fails. Anything on
# the host may take it, and the old teardown removed it by name.
NAMED_FIXTURE_CREATORS = ("start_destination_proxy", "row8_check", "coordinated_check")


@pytest.mark.parametrize("creator", NAMED_FIXTURE_CREATORS)
def test_every_named_fixture_container_carries_this_runs_ownership_label(creator: str) -> None:
    """A label this run wrote is the only thing that outlives the name it was under.

    None of these three carries the deployment's instance labels -- they are not
    services of the deployment -- so this is the one identity a teardown can read
    back and the one thing that distinguishes them from whatever takes their name.
    """
    body = executable_lines()
    created = function_body(creator)

    assert re.search(r"^FIXTURE_LABEL=\S+$", body, re.MULTILINE), (
        "the containers this gate runs carry no ownership label of their own"
    )
    assert '--label "$FIXTURE_LABEL=$RUN_IDENTITY"' in created, (
        f"{creator} creates a container nothing can later prove is this run's"
    )


@pytest.mark.parametrize("stopper", ["stop_row8_containers", "stop_row6_check"])
def test_no_fixture_container_is_removed_by_a_name_that_may_have_moved(stopper: str) -> None:
    """`docker rm --force <name>` deletes whatever holds the name now.

    That is the whole defect: by teardown the name may belong to a container this
    run never made, and removing it would be this gate destroying foreign state
    while reporting that it left the host as it found it.
    """
    stopping = function_body(stopper)

    assert "docker rm" not in stopping, f"{stopper} removes a container by a name that can move"
    assert "remove_owned_fixture_container" in stopping, f"{stopper} does not establish ownership before removing"


def test_the_removal_proves_ownership_and_then_removes_what_it_proved() -> None:
    """Absence is clean, presence has to prove itself, and the proof names the container.

    Read in one question, because two would leave a window the name can move
    inside -- and the removal then names the identifier that question returned
    rather than the name it was asked about.
    """
    checking = function_body("owned_fixture_container").replace("\\\n", " ")
    removing = function_body("remove_owned_fixture_container")

    inspected = [line for line in checking.splitlines() if "docker inspect" in line]
    assert len(inspected) == 1, "ownership and identity are not read in one question"
    assert "{{.Id}}" in inspected[0], "the question does not ask which container the name holds"
    assert "$FIXTURE_LABEL" in inspected[0], "the question does not ask whose the container is"
    assert '"$RUN_IDENTITY"' in checking, "any labelled container would satisfy this, including another run's"

    assert 'docker rm --force "$identifier"' in removing, (
        "the removal names something other than the identifier ownership was proven for"
    )
    assert "$1" not in removing.split("docker rm")[1], "the removal falls back to the name it was given"


def test_an_unreadable_or_unowned_name_preserves_the_container_and_reports_it() -> None:
    """Three answers, and only one of them removes anything.

    A name that holds nothing is a clean teardown. A name holding something this
    run cannot account for is preserved and reported -- the gate cannot prove it
    left the host as it found it, and it must not resolve that by deleting the
    evidence.
    """
    checking = function_body("owned_fixture_container")
    removing = function_body("remove_owned_fixture_container")

    # Matched case-insensitively, because daemons differ on the capitalisation
    # and matching one exactly made a clean host report residue it did not have.
    assert 'grep -qiE "no such (object|container)"' in checking, (
        "an absent name and an unanswered question are not told apart"
    )
    assert checking.count("return 2") >= 2, "an unowned name and an unreadable one are not both preserved"
    assert "return 1" in checking
    # Absence returns clean from the removal; anything unaccounted for does not.
    assert re.search(r"1\)\s*return 0", removing), "a name that holds nothing is reported as a failed removal"
    assert re.search(r"2\)\s*return 1", removing), "a name this run cannot account for reads as removed"


@pytest.mark.parametrize("stopper", ["stop_row8_containers", "stop_row6_check"])
def test_a_preserved_container_turns_only_a_passing_run_into_a_failure(stopper: str) -> None:
    """A teardown defect may never replace the diagnosis that caused the teardown."""
    cleanup = function_body("cleanup")

    assert f"if ! {stopper}" in cleanup, f"{stopper} reports success whatever it could not remove"
    following = cleanup[cleanup.index(f"if ! {stopper}") :].split("fi")[0]
    assert '[ "$status" -ne 0 ] || status=1' in following, (
        f"a container {stopper} preserved replaces the original failure instead of only failing a passing run"
    )


@pytest.mark.parametrize("creator", NAMED_FIXTURE_CREATORS)
def test_a_name_already_taken_at_creation_is_recorded_for_nobody_to_remove(creator: str) -> None:
    """The other window: the precheck refuses, and the competitor must stay untouched.

    Custody is given up and nothing is written to the record the teardown reads,
    so the removal never even asks about a name this run did not take.
    """
    created = function_body(creator)

    assert created.index("container_name_taken") < created.index("$WORK/created"), (
        f"{creator} records a name it has not established it may use"
    )
    refusing = created[created.index("container_name_taken") : created.index("$WORK/created")]
    assert re.search(r"CONTAINER=\n", refusing + "\n"), f"{creator} keeps custody of a name it refused to take"


def test_the_residue_report_tells_this_runs_containers_from_a_recycled_name() -> None:
    """Reported either way, but not as the same thing.

    A container of this run's still running is residue. A name of this run's held
    by something else is a question this gate cannot answer, and the contract for
    this list is that an unanswered question reads as something still being there.
    """
    remaining = function_body("remaining_resources")

    assert "owned_fixture_container" in remaining, "the residue list identifies containers by a name that can move"
    assert "this run created is still present" in remaining
    assert "cannot account for" in remaining, "a recycled name is reported as this run's own container"


def test_row_six_weighs_its_acceptance_results_before_its_housekeeping() -> None:
    """Two things can be wrong at the same instant, and only one is this row's finding.

    `--rm` frees the check's name as the check exits, which is exactly when the
    row reads its verdict. A refusal about that name placed before the verdict
    replaces the diagnosis: the run then says nothing about whether a busy worker
    leaves the deployment READY.
    """
    row = function_body("row_status").replace("\\\n", " ")

    assert "row6_kept=0" in row, "the row acts on its housekeeping result instead of recording it"
    for line in row.splitlines():
        if "stop_row6_check" in line:
            assert "fail" not in line, f"the row reports a container name before its own verdict: {line.strip()}"

    verdict = row.index('[ "$row6_verdict" -eq 0 ]')
    observed = row.index('[ "$row6_observed" -eq 0 ]')
    kept = row.index('[ "$row6_kept" -eq 0 ]')
    assert verdict < kept, "the housekeeping is reported before the property that failed"
    assert observed < kept, "the housekeeping is reported before the queue this row never observed"


def test_row_six_keeps_the_container_and_its_name_for_the_global_teardown() -> None:
    """A foreign container is preserved, and the identity of the name it holds is kept.

    The row reports what it could not account for and stops; removing it is never
    an option, and discarding the name would leave the teardown with nothing to
    ask about.
    """
    row = function_body("row_status")
    stopper = function_body("stop_row6_check")

    assert "ROW6_CHECK_CONTAINER=" not in row, "the row discards the only identity anything holds for its container"
    assert '[ "$kept" -ne 0 ] || ROW6_CHECK_CONTAINER=' in stopper, (
        "custody is given up for a container that was never accounted for"
    )
    assert "stop_row6_check" in function_body("cleanup"), "the teardown does not ask again about what the row kept"


# ---------------------------------------------------------------------------
# B8 — sharing the private directories without privileges this gate may not have
# ---------------------------------------------------------------------------
# The live matrix ended in row 2: `chown 10001:10001` on row 8's control
# directory returned `Operation not permitted`, on a host doing exactly what a
# clean host does. A gate that requires a privileged runner is a gate that
# qualifies a deployment no clean host could reproduce.
CONTROL_OWNERS = (("create_proxy_control", "$PROXY_CONTROL"), ("coordinated_check", "$ROW6_CONTROL"))
# Every container this gate gives a control directory to, and therefore the only
# ones that may be given the group it is shared through.
CONTROL_READERS = ("start_destination_proxy", "row8_check", "coordinated_check")


def test_no_control_directory_asks_this_host_to_give_it_away() -> None:
    """`chown` to another user needs privileges an ordinary account does not have.

    The sharing goes the other way instead: the directory stays with whoever
    invoked this driver and is opened to that user's own group, and the
    containers that must reach it are given that group.
    """
    body = executable_lines()
    narrowing = function_body("narrow_control_access")

    assert "chown" not in body, "this gate still requires a runner that can hand a directory to another user"
    assert 'chgrp "$CONTROL_GROUP"' in narrowing, "the directory is not shared through a group"
    assert "chmod 0770" in narrowing, "the directory is not opened to that group"
    assert "0777" not in body, "a directory falls back to being writable by every account on the host"


def test_the_group_the_directories_are_shared_through_is_this_hosts_own() -> None:
    """Numeric, because the container has no idea what this host calls its groups.

    And established once rather than per directory, so both are shared through
    the same group and the containers need one supplementary group between them.
    """
    establishing = function_body("establish_control_group")

    assert "id -g" in establishing, "the group is not the invoking user's own"
    assert "*[!0-9]*" in establishing, "a group name that is not a number is used as though it were one"
    assert "|| fail" in establishing or "fail " in establishing, "a group this host could not name is used anyway"
    assert "numeric primary group" in establishing


@pytest.mark.parametrize(("owner", "directory"), CONTROL_OWNERS)
def test_each_control_directory_is_narrowed_through_the_one_helper(owner: str, directory: str) -> None:
    """Two directories, one rule. A second spelling of it is a second thing to get wrong."""
    prepared = function_body(owner)

    assert f'narrow_control_access "{directory}"' in prepared, f"{owner} does not narrow its control directory"


@pytest.mark.parametrize("creator", CONTROL_READERS)
def test_a_container_that_reaches_a_control_directory_is_given_that_group(creator: str) -> None:
    """A supplementary group, and exactly one: the directory's.

    Its primary identity stays as the image ships it -- this gate does not decide
    who the candidate runs as, and a run under another identity would be
    qualifying something else.
    """
    created = function_body(creator)

    assert '--group-add "$CONTROL_GROUP"' in created, f"{creator} cannot reach the directory it is given"
    assert created.count("--group-add") == 1, f"{creator} is given more than the one group it needs"
    assert '[ -n "$CONTROL_GROUP" ]' in created, f"{creator} runs before the group it needs was established"


@pytest.mark.parametrize("runner", ["check", "destination_check"])
def test_a_container_that_reaches_no_control_directory_is_given_no_group(runner: str) -> None:
    """These two mount nothing writable, so there is nothing for a group to open."""
    body = executable_lines()
    opened = body.index(f"\n{runner}() {{")
    generic = body[opened : body.index("\n}", opened)]

    assert "--group-add" not in generic, f"{runner} is given a group it has no directory to use it on"


def test_no_container_is_run_under_an_identity_the_image_did_not_ship() -> None:
    """The candidate decides who it runs as. This gate adds a group and nothing more."""
    body = executable_lines()

    assert "--user" not in body, "this gate overrides the identity the candidate image runs as"
    assert "CANDIDATE_UID=10001" in body, "the driver no longer records the identity those containers keep"


def test_the_absence_a_host_reports_is_recognised_however_it_is_spelled() -> None:
    """Docker Desktop writes `error: no such object`; other daemons capitalise it.

    The live matrix reported an incomplete teardown over a clean host because the
    match was exact. Widened to the case, and no further: every other thing a
    host can say is still a question it declined.
    """
    checking = function_body("owned_fixture_container")

    assert "grep -qi" in checking, "the absence a host reports is matched case-sensitively"
    assert "grep -q " not in checking, "one spelling of absence is still matched exactly"
    # The two things a daemon says when a *container* is not there, and nothing
    # wider. `no such host` is a name the daemon could not resolve: it says "no
    # such" too, and nothing at all about whether a container is there.
    assert 'grep -qiE "no such (object|container)"' in checking, (
        "the match reaches sentences that are not about a container being absent"
    )
    assert 'grep -qi "no such"' not in checking, "a DNS failure would read as a clean teardown"
    assert checking.count("return 2") >= 2, "widening the match turned another failure into a clean host"
