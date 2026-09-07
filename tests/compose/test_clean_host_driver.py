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
    source = seeding()

    # Rendered from the parsed module, so the quoting is the unparser's.
    assert "declared['destination']['settings']['branch']" in source
    for branch in set(configured_branches().values()) - {"main"}:
        assert branch not in source, f"{branch} is written into the kit as well as into the configuration"


def test_the_seeding_check_is_given_the_configuration_whose_branch_it_creates() -> None:
    """The document lives in the extracted bundle, so the check has to be handed it."""
    helper = function_body("destination_check")

    assert '"$BUNDLE/configuration:/configuration:ro"' in helper
    assert "/configuration/qualification.yaml" in seeding()


def test_the_seeding_refuses_a_configuration_that_reads_and_writes_one_branch() -> None:
    """Two sides on one branch read identically, so every plan against them is empty.

    A row asserting its plan proposed something would then refuse for emptiness
    rather than for the property it exists to test, which is the failure this
    sweep found in the first place.
    """
    source = seeding()

    assert "if branch == source:" in source
    assert "no plan against it can propose anything" in source


def test_the_destination_is_prepared_in_the_order_a_branch_inherits_from() -> None:
    """Schema, then the fork, then the object -- and each position means something.

    A branch forked before the schema load carries no schema, and a row planning
    against it is refused for the schema rather than judged. An object seeded
    before the fork reaches both sides, and the first plan then has nothing to
    propose.
    """
    source = seeding()
    loaded = source.index("for schema in SCHEMAS:")
    forked = source.index("ensure_branch(planned_branch())")
    seeded = source.index("CREATE_DEVICE.replace")

    assert loaded < forked < seeded
