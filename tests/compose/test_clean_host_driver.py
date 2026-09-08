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


def function_body(name: str) -> str:
    """Return one shell function's body, comments already removed."""
    body = executable_lines()
    opened = body.index(f"{name}() {{")
    return body[opened : body.index("\n}", opened)]


def test_a_query_this_host_refused_to_answer_is_not_read_as_an_empty_answer() -> None:
    """Teardown's own verdict rests on this list, so a failed query cannot read as none.

    Every removal is judged complete by this list being empty. A host that
    refused the question would produce the same emptiness as a host with nothing
    left on it, and the run would report a teardown it never measured.
    """
    helper = function_body("owned_resources").replace("\\\n", " ")

    queried = [line for line in helper.splitlines() if re.match(r"\s*docker\s", line)]
    assert len(queried) == 2, "the owned-resource list is built from two queries"
    for line in queried:
        assert "|| echo" in line, f"a refused query reads as an empty answer: {line.strip()}"


# What F16 settled may never reach a retained artifact, in the forms this driver
# could produce them.
FORBIDDEN_IN_A_DIAGNOSTIC = ("docker inspect", "compose config", "operator.env", "secrets/", "printenv")


def test_the_diagnostic_is_taken_before_anything_is_removed() -> None:
    """After teardown there is no state left to describe, which is the whole point."""
    body = teardown_body()
    captured = body.index("capture_diagnostic")

    for removal in ('compose_bundle reset "$INSTANCE"', "stop_destination", 'docker volume rm "$FOREIGN_VOLUME"'):
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
    steps = ("await_kinds(UNKEYED_KINDS)", "site_id = seed_unkeyed_peer()")
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

    snapshots = [
        node
        for node in ast.walk(tree)
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
    assert 'docker rm --force "$ROW6_CHECK_CONTAINER"' in stopper, "nothing removes the backgrounded container"
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
    captured = [line for line in body.splitlines() if "capture_deployment_log" in line and "()" not in line]

    assert "INFRAHUB_SYNC_LOG_LINES=all" in body, "the sweep reads a bounded tail of what it claims to have read"
    assert len(captured) >= 3, "a deployment is destroyed with its log unread"
    for destroying in ("row_ownership_and_reset", "row_alpha_replacement"):
        row = function_body(destroying)
        assert row.index("capture_deployment_log") < row.index('compose_bundle reset "$INSTANCE"'), (
            f"{destroying} destroys its deployment before its log is taken"
        )
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


def test_the_drift_rows_apply_is_settled_before_its_evidence_is_read() -> None:
    """And before the revert, which would otherwise land while the run was queued."""
    source = code_of(CHECKS / "schema_change.py")

    assert source.index("settle(client, accepted)") < source.index("failure = recorded_failure(client, run_id)")
    assert source.index("settle(client, accepted)") < source.index("load_attribute_kind(original, BRANCH)")


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
