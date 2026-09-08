"""Workflow declarations whose consequences only appear once a run is under way.

Each invariant here covers a failure that costs a full run to discover and
leaves little to read: a permission the caller will not grant ends the run in
`startup_failure` with no jobs at all, a concurrency group shared with a
sibling cancels one of them with a one-line annotation, an upload that does not
opt into hidden files finds nothing at the very end of the gate, and a task a
workflow names but nothing registers is only reported by the runner.

Only explicit declarations are compared. A workflow that states no
`permissions` or no `concurrency`, or uses one of the shorthand permission
strings, is left to GitHub.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path

import pytest
import yaml

from tasks import ns, release

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
FILE_FILTERS = REPO_ROOT / ".github" / "file-filters.yml"
CALLERS = tuple(sorted(path.name for path in WORKFLOWS.glob("trigger-*.yml")))

# GitHub's three access levels, ordered so "grants at least" is a comparison.
ACCESS = {"none": 0, "read": 1, "write": 2}

UPLOAD_ACTION = "actions/upload-artifact"
# The artifacts an approval is later bound to, as opposed to evidence a run
# leaves for whoever reads it that day.
CANDIDATE_ARTIFACTS = ("infrahub-sync-candidate", "infrahub-sync-qualification")
# `invoke` as the command being run, optionally through `uv run`, so that naming
# it as an argument — installing it, say — is not read as running a task.
INVOKE_TASK = re.compile(
    r"(?:^|&&|;|\|)\s*(?:uv\s+run\s+(?:--\S+\s+)*)?invoke\s+([A-Za-z][\w.-]*)",
    re.MULTILINE,
)
# The tree every Invoke task is defined in, and so the tree any workflow that
# runs one depends on beyond the single module it names.
TASK_TREE = "tasks/**"
# What the image gate installs into the artifact it builds, and what the Compose
# phase of that same job then qualifies by running it.
QUALIFIED_TREES = ("infrahub_sync/**", "deploy/compose/**", "tests/compose/**")

PUBLISH_WORKFLOW = WORKFLOWS / "workflow-publish.yml"
IMAGE_WORKFLOW = WORKFLOWS / "workflow-image.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"

# What sends a built artifact somewhere this repository cannot take it back from:
# a package index, a registry, or a published release.
PUBLISHING_COMMANDS = (
    "uv publish",
    "twine upload",
    "docker push",
    "docker login",
    "docker buildx imagetools create",
    # Anything copied to a registry, whichever tool is holding the credential.
    "docker://",
    "gh release create",
    "gh release edit",
    "gh release upload",
    "gh release delete",
)
PUBLISHING_ACTIONS = (
    "pypa/gh-action-pypi-publish",
    "softprops/action-gh-release",
    "actions/create-release",
    "docker/login-action",
)
# The subset that puts a distribution on a package index. There is meant to be one
# route to it, so it is counted separately from the rest.
PACKAGE_UPLOAD = ("uv publish", "twine upload", "pypa/gh-action-pypi-publish")

# `git push` and `git tag` are not read as publication above. They are governed
# separately, by branch, because the repository runs two release lines at once and
# only one of them is bound to a candidate.

# The branch the V3 line is developed on. A workflow a run on that branch can
# start is one this line's single identity has to survive.
V3_BRANCH = "feature/v3-develop"

# Steps that give a release a second identity: one retypes the version the source
# declares, the other creates the tag that version is published under.
IDENTITY_REWRITING = ("uv version", "poetry version", "hatch version", "git tag ")
# Reading the tags back is not creating one.
TAG_READ = re.compile(r"git tag\s+(?:-l\b|--list\b)")

# `trigger-push-stable.yml` runs both, and the case below leaves it alone because
# its `on` selects `stable` and `main` and nothing else: it is the 2.x line's
# release automation, and the version it types is the one that line cuts. What
# keeps it out is that branch filter rather than its name, so adding the V3 branch
# to its triggers puts it back in scope and fails, instead of quietly retyping
# this line's version and tagging a release it never qualified.
TWO_LINE_AUTOMATION = "trigger-push-stable.yml"

# The events a workflow answers without anyone choosing what it acts on. Only a
# dispatch, and a call from one, carry a person's decision about a candidate.
APPROVED_EVENTS = frozenset({"workflow_dispatch", "workflow_call"})

# The job that qualifies the candidate on a host that has never seen this
# repository, and everything it is not allowed to have.
CLEAN_HOST_JOB = "clean-host"
CHECKOUT_ACTION = "actions/checkout"
INTERPRETER_ACTIONS = ("astral-sh/setup-uv", "actions/setup-python")
HOST_TOOLS = ("uv ", "uvx ", "pipx ", "poetry ", "pytest ")
DRIVER_ENTRYPOINT = "clean-host.sh"
DOWNLOAD_ACTION = "actions/download-artifact"

# The job that deletes the handoff inside the run that created it. A handoff is
# not retention: an artifact is the only transfer GitHub offers between two
# jobs, so the bytes exist for one download and the run ends holding none.
CLEANUP_JOB = "handoff-cleanup"
# The two windows the image workflow names, and what each upload has to
# reference. One day is the fail-safe for a run that lost its cleanup, never a
# window anything may rely on; seven is how long a failure is worked in.
WINDOWS = {"HANDOFF_RETENTION_DAYS": 1, "DIAGNOSTIC_RETENTION_DAYS": 7}
HANDOFF_WINDOW = "${{ env.HANDOFF_RETENTION_DAYS }}"
DIAGNOSTIC_WINDOW = "${{ env.DIAGNOSTIC_RETENTION_DAYS }}"
# Deleting one artifact, and deleting the run that is the evidence the gate ran.
ARTIFACT_ENDPOINT = "actions/artifacts"
RUN_ENDPOINT = "actions/runs"

# The one input that decides whether this run may hand bytes between two jobs at
# all, and the token every stage and job that does so has to name in its `if`.
# A fork's token is read-only whatever a workflow asks for, so a fork run
# produces no handoff rather than producing one it cannot delete.
HANDOFF_INPUT = "same-repository"
HANDOFF_GUARD = f"inputs.{HANDOFF_INPUT}"
# What marks a step as producing or describing the handoff: it stages the bytes,
# writes what the service is holding, writes the record read from them, or
# uploads one of the three artifacts.
HANDOFF_MARKERS = (".release/handoff", ".release/artifacts.json", "invoke release.qualify")
# The qualification a fork keeps. None of these may be behind the guard, or a
# fork pull request stops building, scanning, smoking and running the lifecycle.
UNGUARDED_TASKS = ("release.kit", "image.build", "image.scan", "image.smoke", "compose.lifecycle")
# How the caller tells this gate which route a run takes. Compared as text
# rather than evaluated: this suite reads declarations, and a workflow-expression
# evaluator is a second implementation of GitHub.
TRUST_COMPARISON = "github.event.pull_request.head.repo.full_name == github.repository"
# The artifact record `release.qualify` reads, and the one document the candidate
# it names may come from. `read_artifacts` refuses a record naming any other
# candidate, so a writer that retyped the identity would pass on the run that
# wrote it and refuse the next rebuild of the same version.
ARTIFACT_RECORD = ".release/artifacts.json"
RECORDED_IDENTITY = ".release/identity.json"


def load(path: Path) -> dict:
    """Return one parsed workflow document."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def jobs(caller: Path) -> dict[str, dict]:
    """Return a workflow's jobs, keyed by the name the workflow gives each one."""
    return load(caller).get("jobs", {})


def called_workflow(job: dict) -> Path | None:
    """Return the workflow a job calls, when it is one inside this repository."""
    uses = job.get("uses")
    return REPO_ROOT / uses.removeprefix("./") if isinstance(uses, str) and uses.startswith("./") else None


def permissions(path: Path) -> dict[str, str]:
    """Return one workflow's explicit top-level permission mapping, if it states one."""
    declared = load(path).get("permissions")
    return declared if isinstance(declared, dict) else {}


def concurrency_group(path: Path) -> str | None:
    """Return the literal group template a workflow declares, if it declares one."""
    declared = load(path).get("concurrency")
    group = declared.get("group") if isinstance(declared, dict) else None
    return group if isinstance(group, str) else None


def _needs(job: dict) -> tuple[str, ...]:
    declared = job.get("needs")
    if isinstance(declared, str):
        return (declared,)
    return tuple(declared) if isinstance(declared, list) else ()


def _ancestors(name: str, needs: dict[str, tuple[str, ...]]) -> set[str]:
    """Return every job that must finish before this one starts."""
    seen: set[str] = set()
    pending = list(needs.get(name, ()))
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(needs.get(current, ()))
    return seen


def calls() -> list[tuple[Path, str, Path]]:
    """Return every (caller, calling job, called) call inside this repository."""
    found = []
    for name in CALLERS:
        caller = WORKFLOWS / name
        for job, definition in jobs(caller).items():
            called = called_workflow(definition)
            if called is not None:
                found.append((caller, job, called))
    return found


def job_permissions(path: Path, job: str) -> dict[str, str] | None:
    """Return one job's own permission mapping, which replaces the workflow's rather than adding to it."""
    declared = jobs(path)[job].get("permissions")
    return declared if isinstance(declared, dict) else None


def concurrent_calls() -> list[tuple[Path, str, str]]:
    """Return every (caller, job, job) pair of calls that can be in flight together."""
    pairs = []
    for name in CALLERS:
        caller = WORKFLOWS / name
        calling = {job: called for job, definition in jobs(caller).items() if (called := called_workflow(definition))}
        needs = {job: _needs(definition) for job, definition in jobs(caller).items()}
        ordered = sorted(calling)
        pairs.extend(
            (caller, first, second)
            for index, first in enumerate(ordered)
            for second in ordered[index + 1 :]
            if first not in _ancestors(second, needs) and second not in _ancestors(first, needs)
        )
    return pairs


def uploads() -> list[tuple[Path, str, str, dict]]:
    """Return every artifact upload any workflow declares, with its job and step."""
    steps = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, job in load(path).get("jobs", {}).items():
            steps.extend(
                (path, name, str(step.get("name", step["uses"])), step.get("with") or {})
                for step in job.get("steps") or []
                if str(step.get("uses", "")).startswith(f"{UPLOAD_ACTION}@")
            )
    return steps


def candidates() -> list[tuple[Path, str, str, dict]]:
    """Return every upload of a handoff artifact, as opposed to a run's own evidence."""
    return [entry for entry in uploads() if str(entry[3].get("name", "")).startswith(CANDIDATE_ARTIFACTS)]


def _hidden(path: str) -> bool:
    """Report whether an upload path reaches into a directory or file Git-style hidden."""
    return any(part.startswith(".") for part in path.strip().split("/") if part not in {"", ".", ".."})


def invoked_tasks() -> list[tuple[Path, str]]:
    """Return every Invoke task any workflow names in a `run` step."""
    found = set()
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in load(path).get("jobs", {}).values():
            for step in job.get("steps") or []:
                found.update((path, name) for name in INVOKE_TASK.findall(str(step.get("run", ""))))
    return sorted(found, key=lambda entry: (entry[0].name, entry[1]))


def _identify(value: object) -> str:
    return value.name if isinstance(value, Path) else str(value)


@pytest.mark.parametrize(("caller", "job", "called"), calls(), ids=_identify)
def test_a_caller_grants_every_permission_the_workflow_it_calls_requests(caller: Path, job: str, called: Path) -> None:
    """A called workflow can keep or reduce the caller's token, never raise it.

    Read per job on both sides. A single job of a called workflow asking for one
    write is the whole point of scoping a permission, and a comparison that only
    looked at the two top-level mappings would pass while that job ends the run
    in `startup_failure` with no jobs at all.
    """
    granted = job_permissions(caller, job) or permissions(caller)
    if not granted:
        return

    for asking in jobs(called):
        for scope, level in (job_permissions(called, asking) or permissions(called)).items():
            assert ACCESS[level] <= ACCESS[granted.get(scope, "none")], (
                f"{called.name} job {asking} requests {scope}: {level}, which {caller.name} job {job} does not grant"
            )


@pytest.mark.parametrize(("caller", "first", "second"), concurrent_calls(), ids=_identify)
def test_calls_that_can_run_together_do_not_share_a_concurrency_group(caller: Path, first: str, second: str) -> None:
    """Siblings sharing a group cancel each other, and neither leaves a log behind.

    `github.workflow` resolves to the caller's name inside a reusable workflow, so
    two called workflows writing the same group template collide however different
    their own names are.
    """
    groups: dict[str, str | None] = {}
    for job in (first, second):
        called = called_workflow(jobs(caller)[job])
        assert called is not None, f"{caller.name} job {job} is not a call into this repository"
        groups[job] = concurrency_group(called)
    if None in groups.values():
        return

    assert groups[first] != groups[second], (
        f"{caller.name} can run {first} and {second} together, and both resolve the concurrency group "
        f"{groups[first]!r}, so whichever starts second cancels the first"
    )


@pytest.mark.parametrize(("workflow", "job", "step", "declared"), uploads(), ids=_identify)
def test_an_upload_of_evidence_from_a_hidden_directory_asks_for_hidden_files(
    workflow: Path, job: str, declared: dict, step: str
) -> None:
    """The upload action skips hidden paths unless told not to, and finds nothing.

    It reports that at the end of the gate, after everything it was collecting
    evidence about has already run.
    """
    del job
    hidden = [line for line in str(declared.get("path", "")).splitlines() if _hidden(line)]
    if not hidden:
        return

    assert declared.get("include-hidden-files") is True, (
        f"{workflow.name} step {step!r} uploads {hidden} from a hidden directory "
        f"without include-hidden-files, so the upload finds no files"
    )


@pytest.mark.parametrize(("workflow", "task"), invoked_tasks(), ids=_identify)
def test_every_invoke_task_a_workflow_runs_is_registered(workflow: Path, task: str) -> None:
    """A task name only resolves once `tasks/__init__.py` adds its module to the namespace.

    Nothing about the module itself says whether it was registered, so dropping a
    collection leaves the module importable and its own tests passing while the
    workflow that runs it fails on the runner.
    """
    assert task in ns.task_names, f"{workflow.name} runs `invoke {task}`, which the task namespace does not define"


def filter_patterns(name: str) -> list[str]:
    """Return every path pattern one named filter expands to."""
    declared = yaml.safe_load(FILE_FILTERS.read_text(encoding="utf-8"))[name]
    # Each entry is either a pattern or an expanded anchor holding several.
    return [pattern for entry in declared for pattern in (entry if isinstance(entry, list) else [entry])]


def image_filter_patterns() -> list[str]:
    """Return every path pattern the image gate's own filter expands to."""
    return filter_patterns("image_all")


def routed(path: Path, patterns: list[str]) -> bool:
    """Report whether one repository path is named by any of these filter patterns.

    `**` is compared with `fnmatch`, whose `*` already crosses a slash, so a tree
    pattern matches everything under it and a file pattern matches only itself.
    """
    relative = str(path.relative_to(REPO_ROOT))
    return any(fnmatch(relative, pattern) for pattern in patterns)


def build_context_inputs() -> set[str]:
    """Return every path the Dockerfile copies out of the build context.

    Read from the Dockerfile rather than listed here: a file joining the build
    context changes the wheel and the image, and a hand-written list is one edit
    behind the moment someone adds one. `--from=` copies are excluded because
    they come from another stage or another image, not from this tree.
    """
    found: set[str] = set()
    # A `COPY` may continue across physical lines. Joining them first is what
    # keeps every source after the first one from being read as a line that does
    # not begin with `COPY`, and so silently left out of the comparison below.
    joined = re.sub(r"\\[ \t]*\n", " ", DOCKERFILE.read_text(encoding="utf-8"))
    for line in joined.splitlines():
        parts = line.split()
        if not parts or parts[0].upper() != "COPY":
            continue
        arguments = [part for part in parts[1:] if not part.startswith("--")]
        if any(part.startswith("--from=") for part in parts[1:]) or len(arguments) < 2:
            continue
        # `COPY dir/ ./` and `COPY dir ./` copy the same tree, so both have to
        # produce the one name the filter's patterns are written against.
        found.update(argument.rstrip("/") for argument in arguments[:-1])
    return found


def test_the_image_filter_covers_every_input_the_dockerfile_copies() -> None:
    """A file the image is built from, that the filter does not name, skips the gate.

    `README.md` was exactly that: copied at `COPY pyproject.toml uv.lock
    README.md LICENSE.txt ./`, absent from the filter, so a pull request touching
    only it changed the wheel and the image and never re-ran the gate.
    """
    patterns = set(image_filter_patterns())
    inputs = build_context_inputs()

    assert inputs, "no COPY line in the Dockerfile reads from the build context"
    uncovered = sorted(name for name in inputs if name not in patterns and f"{name}/**" not in patterns)
    assert not uncovered, f"the image is built from {uncovered}, which image_all does not name"


@pytest.mark.parametrize("tree", QUALIFIED_TREES)
def test_the_image_filter_covers_every_tree_its_gate_qualifies(tree: str) -> None:
    """The gate builds an image and then runs it; both depend on more than the Dockerfile.

    A change to the application the image installs, or to the bundle that starts
    it, changes what the gate would find — and a filter that does not name it
    leaves that change qualified by the previous commit's run.
    """
    assert tree in image_filter_patterns(), (
        f"the image gate builds and runs {tree}, so image_all has to include it or the gate does not re-run"
    )


def test_the_image_filter_covers_the_whole_tree_its_gate_runs_from() -> None:
    """The gate runs Invoke, so any task module can change what it does.

    Naming only the one module the gate is about leaves the rest of the tree able
    to change the gate's behaviour without re-running it.
    """
    assert TASK_TREE in image_filter_patterns(), (
        f"a change under {TASK_TREE} can alter what `invoke image.*` does, "
        f"so image_all has to include it or the gate does not re-run"
    )


def _publishes(step: dict) -> bool:
    """Report whether one step sends an artifact somewhere the run cannot take it back from."""
    run = str(step.get("run", ""))
    uses = str(step.get("uses", ""))
    if any(uses == action or uses.startswith(f"{action}@") for action in PUBLISHING_ACTIONS):
        return True
    return any(command in run for command in PUBLISHING_COMMANDS)


def publishing_steps() -> list[tuple[Path, str, str]]:
    """Return every publishing step any workflow declares, with the job that holds it."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, job in load(path).get("jobs", {}).items():
            found.extend((path, name, _step_name(step)) for step in job.get("steps") or [] if _publishes(step))
    return found


def _step_name(step: dict) -> str:
    return str(step.get("name", step.get("uses", step.get("run"))))


def triggers(path: Path) -> set[str]:
    """Return the events a workflow answers.

    YAML reads a bare `on` as the boolean it also spells, which is why the key is
    looked up both ways rather than by name alone.
    """
    document = load(path)
    declared = document[True] if True in document else document.get("on")
    return {declared} if isinstance(declared, str) else set(declared or ())


def reachable(starts: Callable[[Path], bool]) -> set[Path]:
    """Return every workflow reachable from the ones `starts` answers for, calls included.

    The three cases below differ only in which workflows a run can begin at. What
    follows from there is the same question each time -- a workflow another one
    calls is a workflow whoever started the caller can run -- so it is asked in
    one place, and each case states its own starting point and nothing else.
    """
    called = {
        path: {target for job in load(path).get("jobs", {}).values() if (target := called_workflow(job)) is not None}
        for path in sorted(WORKFLOWS.glob("*.yml"))
    }
    pending = [path for path in called if starts(path)]
    reached: set[Path] = set()
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        pending.extend(called.get(current, ()))
    return reached


def reachable_from_an_event() -> set[Path]:
    """Return every workflow a run can reach without anyone choosing what it acts on."""
    return reachable(lambda path: bool(triggers(path) - APPROVED_EVENTS))


def pull_request_reachable() -> set[Path]:
    """Return every workflow a pull request can reach, through calls included."""
    return reachable(lambda path: "pull_request" in triggers(path))


def test_nothing_a_pull_request_reaches_publishes_anything() -> None:
    """Validation is lint, unit, image, smoke and Compose, and none of that publishes.

    Narrowed to a pull request rather than to every automatic trigger, because the
    legacy release route really does publish: a published release reaches
    `uv publish`, deliberately and unchanged. Narrowing it to the V3 *line* is not
    available -- `release: published` carries no branch filter, so the reachability
    helper admits that route for every branch, this one included. What is true,
    and what pull-request validation actually asks for, is that nothing a pull
    request can start publishes.

    Reachability is followed through calls, so moving a publication into a
    workflow that a pull request calls does not escape this.
    """
    publishing = {workflow for workflow, _job, _step in publishing_steps()}
    reached = pull_request_reachable()

    assert publishing, WORKFLOWS
    assert reached, WORKFLOWS
    assert not (reached & publishing), (
        f"{sorted(path.name for path in reached & publishing)} can publish from a pull request"
    )


def test_the_legacy_release_route_is_what_the_case_above_would_otherwise_name() -> None:
    """Without this the case above would pass on a repository that publishes nowhere.

    The same shape the version-retyping pair already uses: the excluded route
    demonstrably does the thing, and demonstrably is not something a pull request
    can start.
    """
    publishing = {workflow for workflow, _job, _step in publishing_steps()}

    assert publishing & reachable_from_an_event(), "no trigger reaches a publication, so the exclusion proves nothing"
    assert not (publishing & pull_request_reachable())


def test_one_workflow_is_the_only_route_to_a_package_index() -> None:
    """A second uploader is a second answer to what was published, and to from where."""
    uploaders = {
        workflow
        for workflow in sorted(WORKFLOWS.glob("*.yml"))
        for job in load(workflow).get("jobs", {}).values()
        for step in job.get("steps") or []
        if any(command in f"{step.get('run', '')}{step.get('uses', '')}" for command in PACKAGE_UPLOAD)
    }

    assert uploaders == {PUBLISH_WORKFLOW}


def triggers_of(path: Path) -> dict:
    """Return one workflow's trigger mapping, however YAML read its `on` key."""
    document = load(path)
    return document[True] if True in document else document["on"]


def _retypes_identity(step: dict) -> bool:
    """Report whether one step names a release something the source did not."""
    # The reads are dropped from the script rather than excusing it: a step that
    # lists the tags and then creates one still creates one.
    run = TAG_READ.sub("", str(step.get("run", "")))
    return any(command in run for command in IDENTITY_REWRITING)


def identity_rewriting_steps(path: Path) -> list[str]:
    """Return every step in one workflow that retypes a version or creates its tag."""
    return [
        f"{path.name} job {job} step {_step_name(step)!r}"
        for job, definition in load(path).get("jobs", {}).items()
        for step in definition.get("steps") or []
        if _retypes_identity(step)
    ]


def _selects_v3(path: Path) -> bool:
    """Report whether a run on the V3 branch can start this workflow directly.

    A workflow that filters no branch answers every branch. One reached only by a
    call answers none on its own, and is reached below through whoever calls it.
    """
    declared = triggers_of(path)
    if not isinstance(declared, dict):
        return True
    for event, definition in declared.items():
        if event == "workflow_call":
            continue
        filters = definition.get("branches") if isinstance(definition, dict) else None
        if filters is None or any(fnmatch(V3_BRANCH, pattern) for pattern in filters):
            return True
    return False


def v3_reachable() -> set[Path]:
    """Return every workflow a run on the V3 branch can reach, through calls included."""
    return reachable(_selects_v3)


def test_nothing_the_v3_line_reaches_retypes_its_version_or_creates_its_tag() -> None:
    """One recorded identity survives only while nothing else can type a second one.

    A version retyped mid-run, or a tag computed from something other than the
    candidate, produces a release naming bytes nobody qualified under that name.
    """
    offending = sorted(step for path in v3_reachable() for step in identity_rewriting_steps(path))

    assert offending == []


def test_the_two_line_release_automation_is_what_the_case_above_would_otherwise_name() -> None:
    """Without this the case above would pass on a repository that types no version anywhere."""
    excluded = WORKFLOWS / TWO_LINE_AUTOMATION
    drafter = {called for job in load(excluded)["jobs"].values() if (called := called_workflow(job))}

    assert identity_rewriting_steps(excluded)
    assert [step for path in drafter for step in identity_rewriting_steps(path)]
    assert excluded not in v3_reachable()
    assert not (drafter & v3_reachable())


def image_job(name: str) -> dict:
    """Return one job of the image workflow, refusing a workflow that no longer defines it."""
    defined = jobs(IMAGE_WORKFLOW)
    assert name in defined, f"{IMAGE_WORKFLOW.name} defines no {name} job"
    return defined[name]


def cleanup_script() -> str:
    """Return everything the cleanup job runs, as one body to read its claims out of."""
    return "\n".join(str(step.get("run", "")) for step in image_job(CLEANUP_JOB)["steps"])


def test_the_clean_host_gate_runs_the_shipped_driver_bounded_inside_its_job() -> None:
    """A gate wired to an event this line never raises, or running nothing, has never run.

    The eleven rows and their refusal to be skipped are the driver's own; what is
    checked here is that this line reaches a job that runs it and is bounded.
    """
    job = image_job(CLEAN_HOST_JOB)
    bounded = [step for step in job["steps"] if "timeout-minutes" in step]

    assert IMAGE_WORKFLOW in v3_reachable()
    assert job["needs"] == ["image"]
    assert [step for step in job["steps"] if DRIVER_ENTRYPOINT in str(step.get("run", ""))], (
        f"the {CLEAN_HOST_JOB} job runs no {DRIVER_ENTRYPOINT}"
    )
    assert bounded, f"no step of the {CLEAN_HOST_JOB} job states a timeout"
    for step in bounded:
        assert step["timeout-minutes"] < job["timeout-minutes"], (
            f"the step {step.get('name')!r} is not bounded inside its job"
        )


def test_the_clean_host_job_checks_nothing_out_and_installs_no_interpreter() -> None:
    """The subject is the released artifact, so the tree that produced it is not present.

    Read off the job, because one that happens to omit a checkout today is one
    edit from having one.
    """
    job = image_job(CLEAN_HOST_JOB)
    rendered = yaml.safe_dump(job)

    assert CHECKOUT_ACTION not in rendered
    for action in INTERPRETER_ACTIONS:
        assert action not in rendered, f"the {CLEAN_HOST_JOB} job sets up an interpreter with {action}"
    for step in job["steps"]:
        for tool in HOST_TOOLS:
            assert tool not in str(step.get("run", "")), f"the {CLEAN_HOST_JOB} job runs {tool.strip()} on the host"


def test_the_clean_host_job_takes_this_runs_artifacts_and_never_another_runs() -> None:
    """Naming a `run-id` is the one edit that would qualify a candidate some other commit built."""
    downloads = [
        step
        for step in image_job(CLEAN_HOST_JOB)["steps"]
        if str(step.get("uses", "")).startswith(f"{DOWNLOAD_ACTION}@")
    ]

    assert downloads, f"the {CLEAN_HOST_JOB} job downloads nothing, so it qualifies nothing this run produced"
    for step in downloads:
        assert "run-id" not in (step.get("with") or {}), f"{_step_name(step)!r} downloads from another run"


def test_the_clean_host_diagnostic_is_published_by_name_for_a_failure_only() -> None:
    """The working directory beside the swept file holds the list of this run's own credentials.

    Uploading the directory would publish both. A withheld diagnostic is an
    absent file, so the step tolerates finding nothing.
    """
    published = [
        step for step in image_job(CLEAN_HOST_JOB)["steps"] if str(step.get("uses", "")).startswith(f"{UPLOAD_ACTION}@")
    ]

    assert published, f"the {CLEAN_HOST_JOB} job publishes nothing a failed row leaves behind"
    for step in published:
        assert step.get("if") == "failure()"
        assert str(step["with"]["path"]).endswith("diagnostic.txt"), "a directory of the driver's working files"
        assert step["with"]["if-no-files-found"] == "ignore"


def test_every_upload_states_the_window_its_kind_of_artifact_is_kept_for() -> None:
    """A handoff and a diagnostic are different kinds of thing, kept for different reasons.

    Both windows are named once at the top of the workflow, so what is checked is
    that each upload references the one for what it is and that those two
    references resolve to the two numbers below.
    """
    handoffs = {str(declared["name"]) for _w, _j, _s, declared in candidates()}
    for workflow, _job, step, declared in uploads():
        if workflow != IMAGE_WORKFLOW:
            continue
        expected = HANDOFF_WINDOW if str(declared["name"]) in handoffs else DIAGNOSTIC_WINDOW
        assert declared.get("retention-days") == expected, f"{step!r} keeps {declared['name']} for the wrong window"

    assert load(IMAGE_WORKFLOW)["env"] == WINDOWS


def test_the_cleanup_job_follows_every_job_that_uploads_or_reads_a_handoff() -> None:
    """A cleanup that can start early deletes bytes the gate has not read yet.

    `always()` carries it past a dependency that failed, was skipped or was
    cancelled, which leave the same bytes behind as a passing one.
    """
    job = image_job(CLEANUP_JOB)
    uploading = {name for workflow, name, _step, _declared in candidates() if workflow == IMAGE_WORKFLOW}

    assert uploading, f"{IMAGE_WORKFLOW.name} uploads no handoff, so this proves nothing"
    assert uploading | {CLEAN_HOST_JOB} <= set(_needs(job)), f"{CLEANUP_JOB} waits for {sorted(_needs(job))}"
    assert "always()" in str(job.get("if", ""))


def test_the_cleanup_job_deletes_each_named_artifact_and_never_the_run() -> None:
    """Deletion is by the exact names this run uploaded, never by a pattern.

    A glob deletes whatever else matches and stops matching a renamed artifact.
    The run is not this job's to delete: it is the evidence the gate ran.
    """
    script = cleanup_script()

    assert f"{ARTIFACT_ENDPOINT}/" in script, f"{CLEANUP_JOB} deletes no artifact"
    for _workflow, _job, _step, declared in candidates():
        assert str(declared["name"]) in script, f"{CLEANUP_JOB} never names {declared['name']}"
    assert not re.search(rf"--method\s+DELETE\s+\S*{re.escape(RUN_ENDPOINT)}/\$?\{{?[A-Za-z_]", script), (
        f"{CLEANUP_JOB} deletes a workflow run"
    )


def test_only_the_cleanup_job_can_delete_anything() -> None:
    """`actions: write` is repository-wide, so exactly one job may hold it.

    At the workflow level every step of the build, of the gate and of their
    third-party actions would carry it.
    """
    assert image_job(CLEANUP_JOB)
    assert "actions" not in permissions(IMAGE_WORKFLOW)
    for job in jobs(IMAGE_WORKFLOW):
        declared = job_permissions(IMAGE_WORKFLOW, job) or {}
        if job == CLEANUP_JOB:
            assert declared.get("actions") == "write", f"{CLEANUP_JOB} cannot delete what it is there to delete"
        else:
            assert ACCESS[declared.get("actions", "none")] < ACCESS["write"], f"{job} can delete an artifact"


def test_no_candidate_artifact_outlives_a_completed_run_on_the_v3_line() -> None:
    """A pull-request run describes bytes nobody will ship, so it ends holding none of them.

    What such a run produces is a handoff: one job builds the bytes, a host that
    has never seen this repository qualifies them, and the run deletes them
    before it finishes. Keeping them put gigabytes of pre-release bytes behind
    public download links; the candidate an approval is bound to is built by a
    manual run against an exact merged commit.

    Two claims, because issuing deletions and holding nothing are different: the
    job reads the run back and fails on anything remaining, and every candidate
    upload a pull request can reach is covered by a cleanup that waits for the
    job holding it. Failure-only diagnostics carry neither prefix and are absent
    on success, so they are deliberately not caught here.
    """
    script = cleanup_script()
    assert "expired" in script, f"{CLEANUP_JOB} does not read back what the run still holds"
    assert re.search(r"exit\s+1", script), f"{CLEANUP_JOB} cannot fail a run that still holds a handoff"

    reachable = v3_reachable()
    uncovered = [
        f"{workflow.name}: {job}: {step}"
        for workflow, job, step, _declared in candidates()
        if workflow in reachable
        and not [
            name
            for name, definition in jobs(workflow).items()
            if job in _needs(definition) and "always()" in str(definition.get("if", ""))
        ]
    ]

    assert candidates(), WORKFLOWS
    assert uncovered == [], f"{len(uncovered)} candidate uploads outlive their run: {uncovered}"


def handoff_steps(job: str) -> list[dict]:
    """Return every step of one job that produces or describes the handoff."""
    return [
        step
        for step in image_job(job)["steps"]
        if any(marker in str(step.get("run", "")) for marker in HANDOFF_MARKERS)
        or str((step.get("with") or {}).get("name", "")).startswith(CANDIDATE_ARTIFACTS)
    ]


def test_the_gate_takes_one_input_and_assumes_the_untrusted_route_without_it() -> None:
    """A caller that says nothing gets the route that produces no handoff.

    The default is what a new caller inherits, so it is the fork route: a run
    that builds, scans, smokes and qualifies the lifecycle, and hands nothing to
    a second job it could not then delete.
    """
    declared = triggers_of(IMAGE_WORKFLOW)["workflow_call"]["inputs"][HANDOFF_INPUT]

    assert declared["type"] == "boolean"
    assert declared["default"] is False


def test_every_stage_that_produces_the_handoff_is_behind_the_trust_guard() -> None:
    """A fork's token is read-only, so a fork that uploads a handoff cannot delete it."""
    producers = handoff_steps("image")

    assert len(producers) == len(HANDOFF_MARKERS) + len(CANDIDATE_ARTIFACTS) + 1, producers
    for step in producers:
        assert HANDOFF_GUARD in str(step.get("if", "")), f"{_step_name(step)!r} runs on a fork"


@pytest.mark.parametrize("job", [CLEAN_HOST_JOB, CLEANUP_JOB])
def test_both_jobs_that_consume_or_delete_the_handoff_are_behind_the_guard(job: str) -> None:
    """Neither has anything to do on a fork, and the second would fail on a 403."""
    assert HANDOFF_GUARD in str(image_job(job).get("if", "")), f"{job} runs on a fork"


@pytest.mark.parametrize("task", UNGUARDED_TASKS)
def test_the_qualification_a_fork_still_runs_is_not_behind_the_guard(task: str) -> None:
    """Routing the handoff by trust is not permission to stop qualifying a fork.

    Without this the guard could be moved up the job and satisfy every case
    above while a fork pull request built nothing and ran no lifecycle.
    """
    running = [step for step in image_job("image")["steps"] if task in str(step.get("run", ""))]

    assert running, f"no step of the image job runs {task}"
    for step in running:
        assert HANDOFF_GUARD not in str(step.get("if", "")), f"a fork no longer runs {task}"


def test_the_caller_derives_the_route_from_the_head_repository() -> None:
    """The input is only as good as what the caller puts in it.

    Read as text, deliberately: evaluating the expression would mean
    reimplementing GitHub's own context resolution inside this suite.
    """
    calling = [
        job for caller in CALLERS for job in jobs(WORKFLOWS / caller).values() if called_workflow(job) == IMAGE_WORKFLOW
    ]

    assert calling, f"no caller reaches {IMAGE_WORKFLOW.name}"
    for job in calling:
        passed = str((job.get("with") or {}).get(HANDOFF_INPUT, ""))
        assert TRUST_COMPARISON in passed, f"the image call derives {HANDOFF_INPUT} from {passed!r}"


def test_the_artifact_record_names_the_candidate_from_the_one_document_that_holds_it() -> None:
    """`release.qualify` refuses a record describing another candidate's uploads.

    So the writer copies the identity out of the document `release.identity`
    wrote rather than retyping it. A retyped version would pass on the run that
    wrote it and refuse a rebuild of that version at a new revision.
    """
    writers = [step for step in image_job("image")["steps"] if ARTIFACT_RECORD in str(step.get("run", ""))]

    assert len(writers) == 1, f"{len(writers)} steps write {ARTIFACT_RECORD}"
    script = str(writers[0]["run"])
    assert RECORDED_IDENTITY in script, f"the writer does not read the candidate from {RECORDED_IDENTITY}"
    assert "identity:" in script, f"the writer records no identity in {ARTIFACT_RECORD}"


def kit_inputs() -> list[Path]:
    """Return every repository file `build_qualification_kit()` copies into the kit.

    Read off that function's own declarations rather than listed here, so a
    renamed or repointed source follows automatically. What it cannot see is a
    brand-new source constant; the case below is the reason to add one here too.
    """
    return [
        release.QUALIFICATION_SOURCE / "clean-host.sh",
        *sorted((release.QUALIFICATION_SOURCE / "checks").glob("*.py")),
        *(release.DESTINATION_SOURCE / name for name in release.DESTINATION_FILES),
        release.EXAMPLE_SCHEMA,
    ]


@pytest.mark.parametrize("source", kit_inputs(), ids=lambda path: path.name)
def test_the_image_filter_covers_every_input_the_qualification_kit_carries(source: Path) -> None:
    """The clean-host gate runs the kit, so a change to what goes in it re-runs the gate.

    An input the filter does not name leaves the clean-host matrix qualifying
    the previous commit's fixture — the row passes, and it passed against the
    wrong bytes.
    """
    assert routed(source, image_filter_patterns()), (
        f"the qualification kit carries {source.relative_to(REPO_ROOT)}, which image_all does not name"
    )


@pytest.mark.parametrize("declaration", [WORKFLOWS, FILE_FILTERS, DOCKERFILE], ids=lambda path: path.name)
def test_the_filter_runs_this_suite_when_a_declaration_it_reads_changes(declaration: Path) -> None:
    """Every case here reads one of these, so a change to one changes what this suite asserts.

    Taken from this module's own constants, so a case that starts reading
    something new is covered by naming it there. `sync_all` is the filter that
    routes the unit suites, and this file is one of them.
    """
    probe = declaration / "probe.yml" if declaration.is_dir() else declaration

    assert routed(probe, filter_patterns("sync_all")), (
        f"this suite reads {declaration.relative_to(REPO_ROOT)}, which sync_all does not name"
    )
