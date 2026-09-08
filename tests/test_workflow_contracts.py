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
from fnmatch import fnmatch
from pathlib import Path

import pytest
import yaml

from tasks import ns

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


def calls() -> list[tuple[Path, Path]]:
    """Return every (caller, called) pair of workflows inside this repository."""
    pairs = []
    for name in CALLERS:
        caller = WORKFLOWS / name
        for job in jobs(caller).values():
            called = called_workflow(job)
            if called is not None:
                pairs.append((caller, called))
    return pairs


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


def uploads() -> list[tuple[Path, str, dict]]:
    """Return every artifact upload any workflow declares, with its step name."""
    steps = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in load(path).get("jobs", {}).values():
            steps.extend(
                (path, str(step.get("name", step["uses"])), step.get("with") or {})
                for step in job.get("steps") or []
                if str(step.get("uses", "")).startswith(f"{UPLOAD_ACTION}@")
            )
    return steps


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


@pytest.mark.parametrize(("caller", "called"), calls(), ids=_identify)
def test_a_caller_grants_every_permission_the_workflow_it_calls_requests(caller: Path, called: Path) -> None:
    """A called workflow can keep or reduce the caller's token, never raise it."""
    granted = permissions(caller)
    if not granted:
        return

    for scope, level in permissions(called).items():
        assert ACCESS[level] <= ACCESS[granted.get(scope, "none")], (
            f"{called.name} requests {scope}: {level}, which {caller.name} does not grant"
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


@pytest.mark.parametrize(("workflow", "step", "declared"), uploads(), ids=_identify)
def test_an_upload_of_evidence_from_a_hidden_directory_asks_for_hidden_files(
    workflow: Path, step: str, declared: dict
) -> None:
    """The upload action skips hidden paths unless told not to, and finds nothing.

    It reports that at the end of the gate, after everything it was collecting
    evidence about has already run.
    """
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


def image_filter_patterns() -> list[str]:
    """Return every path pattern the image gate's own filter expands to."""
    declared = yaml.safe_load(FILE_FILTERS.read_text(encoding="utf-8"))["image_all"]
    # Each entry is either a pattern or an expanded anchor holding several.
    return [pattern for entry in declared for pattern in (entry if isinstance(entry, list) else [entry])]


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


def reachable_from_an_event() -> set[Path]:
    """Return every workflow a run can reach without anyone choosing what it acts on."""
    called = {
        path: {target for job in load(path).get("jobs", {}).values() if (target := called_workflow(job)) is not None}
        for path in sorted(WORKFLOWS.glob("*.yml"))
    }
    pending = [path for path in called if triggers(path) - APPROVED_EVENTS]
    reached: set[Path] = set()
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        pending.extend(called.get(current, ()))
    return reached


def pull_request_reachable() -> set[Path]:
    """Return every workflow a pull request can reach, through calls included."""
    called = {
        path: {target for job in load(path).get("jobs", {}).values() if (target := called_workflow(job)) is not None}
        for path in sorted(WORKFLOWS.glob("*.yml"))
    }
    pending = [path for path in called if "pull_request" in triggers(path)]
    reached: set[Path] = set()
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        pending.extend(called.get(current, ()))
    return reached


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
    called = {
        path: {target for job in load(path).get("jobs", {}).values() if (target := called_workflow(job)) is not None}
        for path in sorted(WORKFLOWS.glob("*.yml"))
    }
    pending = [path for path in called if _selects_v3(path)]
    reached: set[Path] = set()
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        pending.extend(called.get(current, ()))
    return reached


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


def test_nothing_the_v3_line_reaches_retains_a_candidate_artifact() -> None:
    """A pull-request run describes bytes nobody will ship, so it keeps none of them.

    Validation on a pull request is lint, unit, image, smoke and Compose. The
    candidate that gets qualified and approved is built by a manual run against an
    exact merged commit, and only that run retains anything -- a PR run's image,
    layout, distributions and bundle describe a merge result that will never be
    published, and retaining them put gigabytes of pre-release bytes behind
    public download links.

    The workflow declares no upload at all today, so this holds by there being
    nothing to retain. It stays an equality over every reachable upload, which is
    what makes the first one added later fail here.
    """
    reachable = v3_reachable()
    retained = sorted(
        f"{path.name}: {step}"
        for path, step, declared in uploads()
        if path in reachable and str(declared.get("name", "")).startswith(CANDIDATE_ARTIFACTS)
    )

    assert retained == [], f"a pull-request run retains {len(retained)} candidate artifacts: {retained}"
