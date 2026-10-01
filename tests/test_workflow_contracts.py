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

import os
import re
import shutil
import subprocess  # noqa: S404 — running the guard's own script is how its refusal is measured
import sys
import tempfile
from collections.abc import Callable
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
# `invoke` as the command being run, optionally through `uv run`, so that naming
# it as an argument — installing it, say — is not read as running a task.
INVOKE_TASK = re.compile(
    r"(?:^|&&|;|\|)\s*(?:uv\s+run\s+(?:--\S+\s+)*)?invoke\s+([A-Za-z][\w.-]*)",
    re.MULTILINE,
)

PUBLISH_WORKFLOW = WORKFLOWS / "workflow-publish.yml"
NIGHTLY_WORKFLOW = WORKFLOWS / "workflow-nightly-e2e.yml"
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

# `trigger-push-stable.yml` runs both, and the case below leaves it alone because it
# refuses every ref but the release branch before it types anything: it is the 2.x
# line's release automation, and the version it types is the one that line cuts. It
# is dispatched rather than pushed, and a dispatch carries no branch filter to read,
# so the guard is a step instead. What keeps the workflow out of scope is therefore
# that step rather than its name, and deleting it puts the workflow back in scope and
# fails, instead of quietly retyping this line's version and tagging a release it
# never qualified.
TWO_LINE_AUTOMATION = "trigger-push-stable.yml"
# The environment name that step reads the permitted ref out of, and the shell
# variable GitHub puts the dispatched ref in.
RELEASE_BRANCH_ENV = "RELEASE_BRANCH"
DISPATCHED_REF = "GITHUB_REF_NAME"
# The only ref that step may admit, and a bound on running its few lines of shell.
RELEASE_BRANCH = "main"
GUARD_TIMEOUT_SECONDS = 30

# The events a workflow answers without anyone choosing what it acts on. Only a
# dispatch, and a call from one, carry a person's decision about a candidate.
APPROVED_EVENTS = frozenset({"workflow_dispatch", "workflow_call"})

CHECKOUT_ACTION = "actions/checkout"

# The nightly route's commit input. The commit to build is an input rather than
# the ref's tip: `workflow_dispatch` runs against a ref, so a branch that moved
# between the merge and the dispatch would build different source.
SHA_INPUT = "sha"
HEAD_READBACK = "git rev-parse HEAD"
ANCESTRY_CHECK = "git merge-base --is-ancestor"
# What `actions/checkout` does with the run's token unless told otherwise. Left
# on, it writes the token into `.git/config` of the tree every later step runs
# third-party code against.
PERSISTED_CREDENTIALS = "persist-credentials"

# The pull-request caller.
DEVELOP_CALLER = WORKFLOWS / "trigger-pr-develop.yml"
# The branch this gate guards, and so the one tree a pull-request run never
# builds as itself: the merge commit. Qualifying it is a push route, separate
# from anything a pull request can ask for.
POST_MERGE_BRANCH = "feature/v3-develop"
# Compared as a whole set rather than by membership. The post-merge route is an
# addition to the one branch pattern that was already here, not a licence to
# start this gate on every push the repository receives.
PUSHED_BRANCHES = frozenset({"renovate/**", POST_MERGE_BRANCH})
# The job branch protection requires by name. A skipped job satisfies a required
# check, so the gate that has not run cannot be the thing required: this one
# always runs and reports on the image call whether it ran or not.
REQUIRED_JOB = "qualification-required"


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


def routed(path: Path, patterns: list[str]) -> bool:
    """Report whether one repository path is named by any of these filter patterns.

    `**` is compared with `fnmatch`, whose `*` already crosses a slash, so a tree
    pattern matches everything under it and a file pattern matches only itself.
    """
    relative = str(path.relative_to(REPO_ROOT))
    return any(fnmatch(relative, pattern) for pattern in patterns)


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


def _publish_guarded(job: dict, step: dict) -> bool:
    """Report whether a publishing step only runs when its workflow is asked to publish."""
    return PUBLISH_GUARD in str(job.get("if", "")) or PUBLISH_GUARD in str(step.get("if", ""))


def unguarded_publishers() -> set[Path]:
    """Return every workflow holding a publishing step that `inputs.publish` does not guard."""
    return {
        path
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job in load(path).get("jobs", {}).values()
        for step in job.get("steps") or []
        if _publishes(step) and not _publish_guarded(job, step)
    }


def test_nothing_a_pull_request_reaches_publishes_anything() -> None:
    """Validation is lint, unit, image build and smoke, and none of that publishes.

    The image gate is reusable and does publish when asked, so a workflow a pull
    request reaches may hold publishing steps only behind `inputs.publish`, and
    every call a pull-request workflow makes into one passes `publish: false`.

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
    assert not (reached & unguarded_publishers()), (
        f"{sorted(path.name for path in reached & unguarded_publishers())} can publish from a pull request"
    )
    for caller in sorted(reached):
        for name, job in jobs(caller).items():
            called = called_workflow(job)
            if called in publishing:
                assert (job.get("with") or {}).get("publish") is False, (
                    f"{caller.name} job {name} calls {called.name} from a pull request without publish: false"
                )


def test_the_legacy_release_route_is_what_the_case_above_would_otherwise_name() -> None:
    """Without this the case above would pass on a repository that publishes nowhere.

    The same shape the version-retyping pair already uses: the excluded route
    demonstrably does the thing, and demonstrably is not something a pull request
    can start.
    """
    publishing = {workflow for workflow, _job, _step in publishing_steps()}

    assert publishing & reachable_from_an_event(), "no trigger reaches a publication, so the exclusion proves nothing"
    assert not (unguarded_publishers() & pull_request_reachable())


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


def _names_a_ref_guard(step: dict) -> bool:
    """Report whether one step is shaped like the ref guard, before asking what it does."""
    run = str(step.get("run", ""))
    env = step.get("env") or {}
    return RELEASE_BRANCH_ENV in env and DISPATCHED_REF in run


def _guard_verdicts(step: dict) -> tuple[int, int]:
    """Run the guard's own script against the release ref and a foreign one.

    Reading the script cannot tell a refusal from a permission: inverting the one
    comparison in it leaves every word the reader matched on in place. So the script
    is executed, in a scratch directory with nothing in its environment but the two
    variables it reads, and judged by what it exits with.
    """
    script = str(step.get("run", ""))
    release_branch = str((step.get("env") or {})[RELEASE_BRANCH_ENV])
    bash = shutil.which("bash")
    assert bash, "a POSIX shell is needed to run the guard the way the runner does"

    def exit_code(ref: str) -> int:
        with tempfile.TemporaryDirectory() as scratch:
            return subprocess.run(  # noqa: S603
                [bash, "-c", script],
                env={RELEASE_BRANCH_ENV: release_branch, DISPATCHED_REF: ref},
                cwd=scratch,
                capture_output=True,
                check=False,
                timeout=GUARD_TIMEOUT_SECONDS,
            ).returncode

    return exit_code(release_branch), exit_code(V3_BRANCH)


def _refuses_a_foreign_ref(step: dict) -> bool:
    """Report whether one step really stops a run dispatched against a foreign ref."""
    if not _names_a_ref_guard(step):
        return False
    if str((step.get("env") or {})[RELEASE_BRANCH_ENV]) != RELEASE_BRANCH:
        return False
    admitted, refused = _guard_verdicts(step)
    return admitted == 0 and refused != 0


def _guards_its_ref_before_typing_anything(path: Path) -> bool:
    """Report whether a workflow refuses a foreign ref before it can retype an identity.

    A dispatch runs against whichever ref the dispatcher picked, so a workflow that
    prepares a release has to check that ref itself. Ordering is half of it: a guard
    that runs after the version is typed has already let the run type it. What the
    guard does when it runs is the other half, and is measured rather than read.
    """
    for definition in load(path).get("jobs", {}).items():
        steps = definition[1].get("steps") or []
        typed = next((index for index, step in enumerate(steps) if _retypes_identity(step)), None)
        if typed is None:
            continue
        named = next((index for index, step in enumerate(steps) if _names_a_ref_guard(step)), None)
        if named is None or named > typed or not _refuses_a_foreign_ref(steps[named]):
            return False
    return True


def test_nothing_the_v3_line_reaches_retypes_its_version_or_creates_its_tag() -> None:
    """One recorded identity survives only while nothing else can type a second one.

    A version retyped mid-run, or a tag computed from something other than the
    candidate, produces a release naming bytes nobody qualified under that name.

    A dispatched workflow answers every ref, so the release automation cannot be
    excluded by a branch filter the way a pushed one was. It is excluded by proving
    it refuses a foreign ref first; the case below holds that proof to its subject.
    """
    offending = sorted(
        step
        for path in v3_reachable()
        if not _guards_its_ref_before_typing_anything(path)
        for step in identity_rewriting_steps(path)
    )

    assert offending == []


def test_the_two_line_release_automation_is_what_the_case_above_would_otherwise_name() -> None:
    """Without this the case above would pass on a repository that types no version anywhere.

    It also pins what earns the exemption. The release automation is the only
    workflow allowed to type an identity, and only because it refuses every ref but
    its release branch before doing so.
    """
    excluded = WORKFLOWS / TWO_LINE_AUTOMATION
    exempted = {path for path in v3_reachable() if identity_rewriting_steps(path)}

    assert identity_rewriting_steps(excluded)
    assert _guards_its_ref_before_typing_anything(excluded)
    assert exempted == {excluded}, f"{sorted(path.name for path in exempted)} type an identity, not just the one"


def job_of(workflow: Path, name: str) -> dict:
    """Return one named job of one workflow, refusing a workflow that no longer defines it."""
    defined = jobs(workflow)
    assert name in defined, f"{workflow.name} defines no {name} job"
    return defined[name]


def test_nightly_route_is_dispatch_only_and_requires_an_exact_merged_commit() -> None:
    """A scheduled or pull-request run must not qualify a moving branch tip."""
    assert triggers(NIGHTLY_WORKFLOW) == {"workflow_dispatch"}
    assert NIGHTLY_WORKFLOW not in reachable_from_an_event()
    declared = triggers_of(NIGHTLY_WORKFLOW)["workflow_dispatch"]["inputs"][SHA_INPUT]
    assert declared["required"] is True
    assert declared["type"] == "string"
    steps = jobs(NIGHTLY_WORKFLOW)["end-to-end"]["steps"]
    checkout = next(step for step in steps if str(step.get("uses", "")).startswith(CHECKOUT_ACTION))
    assert checkout["with"] == {
        "ref": f"${{{{ inputs.{SHA_INPUT} }}}}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    guard_step = next(step for step in steps if step.get("id") == "sha_guard")
    guard = guard_step["run"]
    assert "${#expected} -ne 40" in guard
    assert HEAD_READBACK in guard
    assert ANCESTRY_CHECK in guard
    assert "git fetch --no-tags --quiet origin feature/v3-develop" in guard
    assert guard_step["env"]["NIGHTLY_SHA"] == f"${{{{ inputs.{SHA_INPUT} }}}}"


def test_nightly_suites_report_independently_and_clean_up() -> None:
    """Each selected suite retains evidence and later suites run after a failure."""
    steps = jobs(NIGHTLY_WORKFLOW)["end-to-end"]["steps"]
    by_id = {step["id"]: step for step in steps if "id" in step}
    image_check = next(
        index for index, step in enumerate(steps) if "nightly_e2e.py verify-images" in str(step.get("run", ""))
    )
    for name in ("integration", "preview", "saved-plan", "from-netbox"):
        step = by_id[name]
        assert image_check < steps.index(step)
        assert step["continue-on-error"] is True
        assert step["if"] == "always() && steps.images.outcome == 'success'"
        assert f"nightly_e2e.py suite {name}" in step["run"]
        assert any(
            other.get("if") == f"always() && steps.{name}.outcome == 'failure'"
            and other.get("continue-on-error") is True
            and f"nightly_e2e.py logs {name}" in str(other.get("run", ""))
            for other in steps
        )
    uploads = [step for step in steps if str(step.get("uses", "")).startswith(UPLOAD_ACTION)]
    assert {step["with"]["path"] for step in uploads} == {
        ".preview/nightly-e2e/*.xml",
        ".preview/nightly-e2e/*-containers.log",
    }
    assert all(step["if"] == "always()" for step in uploads)
    assert any(
        "nightly_e2e.py summary" in str(step.get("run", ""))
        and step.get("env", {}).get("NIGHTLY_SHA") == "${{ inputs.sha }}"
        for step in steps
    )
    cleanups = [step for step in steps if "down --volumes" in str(step.get("run", ""))]
    assert len(cleanups) == 2
    assert all(step["if"] == "always()" for step in cleanups)
    assert ".github/scripts/nightly_e2e.py" in filter_patterns("sync_all")


def test_nightly_runs_the_compose_suite_against_an_image_it_builds() -> None:
    """The opt-in Compose suite keeps running: nightly, on the exact merged commit.

    It builds the image locally and names it through the two settings the root
    `docker-compose.yml` reads, so the run proves the operator file against this
    commit's image without pulling anything.
    """
    steps = jobs(NIGHTLY_WORKFLOW)["compose-suite"]["steps"]
    checkout = next(step for step in steps if str(step.get("uses", "")).startswith(CHECKOUT_ACTION))
    assert checkout["with"] == {
        "ref": f"${{{{ inputs.{SHA_INPUT} }}}}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    guard = next(step for step in steps if step.get("id") == "sha_guard")["run"]
    assert ANCESTRY_CHECK in guard
    assert HEAD_READBACK in guard
    runs = [str(step.get("run", "")) for step in steps]
    build = next(index for index, run in enumerate(runs) if run == "docker build -t infrahub-sync:compose-test .")
    suite = next(
        index for index, run in enumerate(runs) if run == "uv run --no-sync pytest -m compose tests/compose -x"
    )
    assert build < suite
    assert steps[suite]["env"] == {"INFRAHUB_SYNC_DOCKER_IMAGE": "infrahub-sync", "VERSION": "compose-test"}


def develop_jobs() -> dict[str, dict]:
    """Return the job graph of the caller the two tiers exist for."""
    return jobs(DEVELOP_CALLER)


def test_a_merge_into_the_branch_this_gate_guards_re_qualifies_it() -> None:
    """A tier that would qualify a push is not the same thing as a push the gate ever sees.

    The decision above raises the tier for anything that is not a pull request,
    but GitHub only starts a workflow for the events `on:` names. Without this
    branch under `push`, the merged tree — the one head no pull-request run ever
    built as itself — is the only thing the full tier never covers.

    The set is compared whole, because the correction is the post-merge route
    and not a gate that runs on every push.
    """
    declared = triggers_of(DEVELOP_CALLER)

    assert POST_MERGE_BRANCH in declared["pull_request"]["branches"], (
        f"{DEVELOP_CALLER.name} no longer guards pull requests into {POST_MERGE_BRANCH}"
    )
    assert set(declared["push"]["branches"]) == PUSHED_BRANCHES, (
        f"{DEVELOP_CALLER.name} runs on pushes to {sorted(declared['push']['branches'])}; "
        f"a merge into {POST_MERGE_BRANCH} has to start the full tier, and nothing wider should"
    )


def test_the_fast_tier_starts_beside_the_lint_it_used_to_wait_for() -> None:
    """Ninety seconds on the critical path of every run, for an ordering nothing needs.

    Lint still blocks a merge as its own required check; what it no longer does
    is hold the tests and the image gate behind it.
    """
    graph = develop_jobs()
    linting = {name for name, job in graph.items() if "linter" in str(job.get("uses", ""))}
    needs = {name: _needs(job) for name, job in graph.items()}

    assert linting, f"{DEVELOP_CALLER.name} runs no linter"
    for name in ("tests", "image"):
        assert not linting & _ancestors(name, needs), f"{name} still waits for {sorted(linting)}"


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


def test_the_sync_filter_routes_the_examples_tree() -> None:
    """Several unit tests read fixtures under `examples/**` at runtime.

    A pull request that changes only a file there has to run the unit and
    base-install jobs, or a break in an example fixture merges undetected.
    """
    probe = REPO_ROOT / "examples" / "probe.yml"

    assert routed(probe, filter_patterns("sync_all")), (
        f"unit tests read fixtures under {probe.parent.relative_to(REPO_ROOT)}, which sync_all does not name"
    )


# The SDK update workflow runs unattended: it checks a branch out, moves the locked
# infrahub-sdk version and opens a pull request back to that same branch. All three
# parts read `matrix.branch-name`, so they have to keep agreeing, and the update has
# to stay lock-only or a bot rewrites the declared range in `pyproject.toml`.
SDK_UPDATE_WORKFLOW = WORKFLOWS / "update-infrahub-sdk.yml"
SDK_UPDATE_JOB = "update-dependencies"
SDK_LOCK_COMMAND = 'uv lock --upgrade-package "infrahub-sdk==${INFRAHUB_SDK_VERSION}"'
SDK_MATRIX_BRANCH = "${{ matrix.branch-name }}"


def sdk_update_steps() -> list[dict]:
    """The steps of the SDK update job."""
    return job_of(SDK_UPDATE_WORKFLOW, SDK_UPDATE_JOB)["steps"]


def _one_step(what: str, matches: Callable[[dict], bool]) -> dict:
    """The single step of the SDK update job that does something, located by what it does.

    Steps are found by the action they use or the command they run, not by their
    display name, so renaming a step does not change what these cases assert.
    """
    found = [step for step in sdk_update_steps() if matches(step)]
    assert len(found) == 1, f"{len(found)} steps of {SDK_UPDATE_JOB} {what}"
    return found[0]


def test_the_sdk_update_targets_main_only() -> None:
    """A second branch in the matrix would open a bot pull request against a line nobody asked it to."""
    matrix = job_of(SDK_UPDATE_WORKFLOW, SDK_UPDATE_JOB)["strategy"]["matrix"]

    assert matrix["branch-name"] == ["main"]


def test_the_sdk_update_checkout_takes_the_matrix_branch() -> None:
    """Without an explicit ref the run updates whatever the dispatch defaulted to."""
    declared = _one_step("check something out", lambda step: str(step.get("uses", "")).startswith(CHECKOUT_ACTION))[
        "with"
    ]

    assert declared["ref"] == SDK_MATRIX_BRANCH, f"the checkout takes {declared.get('ref')!r}"
    assert declared[PERSISTED_CREDENTIALS] is False


def test_the_sdk_update_only_moves_the_lockfile() -> None:
    """`uv add` pins the declared range; `uv lock` rewrites only `uv.lock`."""
    lock_step = _one_step("lock the SDK version", lambda step: "uv lock" in str(step.get("run", "")))

    assert SDK_LOCK_COMMAND in lock_step["run"]
    assert not [step for step in sdk_update_steps() if "uv add" in str(step.get("run", ""))], (
        f"a step of {SDK_UPDATE_JOB} still pins the declared range with `uv add`"
    )


def test_the_sdk_update_pull_request_targets_the_matrix_branch() -> None:
    """The pull request has to land on the branch the run checked out and locked."""
    create_pr = _one_step("open the pull request", lambda step: "gh pr create" in str(step.get("run", "")))

    assert create_pr["env"]["MATRIX_BRANCH"] == SDK_MATRIX_BRANCH
    assert '--base "${MATRIX_BRANCH}"' in create_pr["run"]


# --------------------------------------------------------------------------
# ci-docker-image
# --------------------------------------------------------------------------
# The reusable Harbor build-and-push workflow. Its inputs mirror infrahub-mcp's file
# of the same name, every platform is smoke-tested before anything is pushed, and
# every step that can reach the registry is behind `inputs.publish`.
DOCKER_IMAGE_WORKFLOW = WORKFLOWS / "ci-docker-image.yml"
PUBLISH_GUARD = "inputs.publish"
DOCKER_IMAGE_INPUTS = {
    "publish": {"type": "boolean", "required": False, "default": False},
    "version": {"type": "string", "required": False, "default": ""},
    "ref": {"type": "string", "required": True},
    "tags": {"type": "string", "required": True},
    "labels": {"type": "string", "required": True},
    "platforms": {"type": "string", "required": False, "default": "linux/amd64,linux/arm64"},
}
DOCKER_IMAGE_RUNNERS = {"linux/amd64": "ubuntu-24.04", "linux/arm64": "ubuntu-24.04-arm"}
PUBLISH_ONLY_JOBS = ("merge", "sign", "sbom")
SIGNING_JOBS = ("sign", "sbom")
SMOKE_COMMAND = "uv run pytest -m docker tests/image/test_image_artifact.py"
SMOKE_IMAGE = "infrahub-sync:smoke"
LOGIN_ACTION = "docker/login-action"
BUILD_PUSH_ACTION = "docker/build-push-action"
TAG_GUARD_MESSAGE = "publishing needs at least one tag"
# `uses: owner/repo[/path]@<40 hex> # vX.Y.Z` -- a SHA alone cannot be read, and a
# tag alone can be moved under the workflow.
PINNED_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*\S+@[0-9a-f]{40}\s+#\s*v\d[\w.\-]*\s*$")


def docker_image_job(name: str) -> dict:
    """Return one job of the reusable image workflow."""
    return job_of(DOCKER_IMAGE_WORKFLOW, name)


def docker_image_build_steps() -> list[dict]:
    """Return the steps of the per-platform build job, in order."""
    return docker_image_job("build")["steps"]


def _uses(step: dict, action: str) -> bool:
    return str(step.get("uses", "")).startswith(f"{action}@")


def _pushes(step: dict) -> bool:
    """Report whether one step logs in to or pushes to a registry."""
    if _uses(step, LOGIN_ACTION):
        return True
    declared = step.get("with") or {}
    return _uses(step, BUILD_PUSH_ACTION) and (
        "push=true" in str(declared.get("outputs", "")) or bool(declared.get("push"))
    )


@pytest.mark.parametrize("trigger", ["workflow_call", "workflow_dispatch"])
def test_the_image_workflow_takes_the_inputs_the_contract_names(trigger: str) -> None:
    """The same names and defaults as infrahub-mcp's, so callers read alike in both repositories."""
    declared = triggers_of(DOCKER_IMAGE_WORKFLOW)[trigger]["inputs"]

    assert set(declared) == set(DOCKER_IMAGE_INPUTS)
    for name, expected in DOCKER_IMAGE_INPUTS.items():
        assert {key: declared[name].get(key) for key in expected} == expected, name
        if "default" not in expected:
            assert "default" not in declared[name], f"{name} is required and must not carry a default"


def test_a_dispatch_takes_exactly_the_inputs_a_call_does() -> None:
    """A hand-run image is the same build a release calls for, so the two input sets cannot drift."""
    declared = triggers_of(DOCKER_IMAGE_WORKFLOW)

    assert declared["workflow_dispatch"]["inputs"] == declared["workflow_call"]["inputs"]


@pytest.mark.parametrize("trigger", ["workflow_call", "workflow_dispatch"])
def test_the_tags_input_says_it_takes_newline_separated_full_references(trigger: str) -> None:
    description = triggers_of(DOCKER_IMAGE_WORKFLOW)[trigger]["inputs"]["tags"]["description"].lower()

    assert "newline-separated" in description
    assert "full" in description


def test_a_run_names_the_ref_it_builds_and_whether_it_publishes() -> None:
    """The run list then tells two dispatches apart without opening either."""
    run_name = load(DOCKER_IMAGE_WORKFLOW).get("run-name")

    assert isinstance(run_name, str), "the image workflow declares no run-name"
    assert "${{ inputs.ref }}" in run_name
    assert "${{ inputs.publish }}" in run_name


def test_only_a_newer_run_for_the_same_ref_cancels_an_image_build() -> None:
    """The group is shared with the caller, so the ref keeps a run for one ref from cancelling another's."""
    declared = load(DOCKER_IMAGE_WORKFLOW)["concurrency"]

    assert concurrency_group(DOCKER_IMAGE_WORKFLOW) == "${{ github.workflow }}-${{ inputs.ref }}"
    assert declared.get("cancel-in-progress") is True


def test_the_image_workflow_requests_exactly_what_signing_and_pushing_need() -> None:
    """Scoped per job, so the build a pull request runs asks for nothing it can write with.

    Harbor is reached with its own credentials, so no job needs `packages`; only
    keyless signing and attestation need `id-token`.
    """
    assert permissions(DOCKER_IMAGE_WORKFLOW) == {"contents": "read"}
    for name in jobs(DOCKER_IMAGE_WORKFLOW):
        expected = {"contents": "read", "id-token": "write"} if name in SIGNING_JOBS else {"contents": "read"}
        assert job_permissions(DOCKER_IMAGE_WORKFLOW, name) == expected, f"{name} requests the wrong permissions"


def test_each_platform_builds_on_its_own_native_runner() -> None:
    matrix = docker_image_job("build")["strategy"]["matrix"]["include"]

    assert {entry["platform"]: entry["runner"] for entry in matrix} == DOCKER_IMAGE_RUNNERS
    assert docker_image_job("build")["runs-on"] == "${{ matrix.runner }}"


def test_the_build_loads_the_root_dockerfile_under_the_smoke_tag_with_the_callers_labels() -> None:
    builds = [step for step in docker_image_build_steps() if _uses(step, BUILD_PUSH_ACTION) and not _pushes(step)]

    assert len(builds) == 1, "exactly one local build feeds the smoke test"
    declared = builds[0]["with"]
    assert declared["load"] is True
    assert declared["tags"] == SMOKE_IMAGE
    assert declared["labels"] == "${{ inputs.labels }}"
    assert declared["file"] == "Dockerfile"


def test_the_smoke_test_runs_against_the_loaded_image() -> None:
    smoke = [step for step in docker_image_build_steps() if SMOKE_COMMAND in str(step.get("run", ""))]

    assert len(smoke) == 1
    assert smoke[0]["env"]["INFRAHUB_SYNC_IMAGE_REF"] == SMOKE_IMAGE


def test_the_smoke_test_comes_before_any_login_or_push() -> None:
    """A platform that fails its smoke test must leave nothing in the registry."""
    steps = docker_image_build_steps()
    smoke = next(index for index, step in enumerate(steps) if SMOKE_COMMAND in str(step.get("run", "")))
    pushing = [index for index, step in enumerate(steps) if _pushes(step)]

    assert pushing, "the build job never pushes"
    assert smoke < min(pushing), f"step {min(pushing)} reaches the registry before the smoke test at step {smoke}"


def test_the_push_is_by_digest_without_provenance() -> None:
    pushes = [step for step in docker_image_build_steps() if _uses(step, BUILD_PUSH_ACTION) and _pushes(step)]

    assert len(pushes) == 1
    declared = pushes[0]["with"]
    assert declared["provenance"] is False
    assert declared["outputs"] == (
        "type=image,name=${{ vars.HARBOR_HOST }}/${{ github.repository }},"
        "push-by-digest=true,name-canonical=true,push=true"
    )
    assert PUBLISH_GUARD in str(pushes[0].get("if", ""))


def test_every_step_that_reads_a_secret_is_behind_the_publish_guard() -> None:
    """A step-level guard, or a job that only runs when publishing."""
    exposed = [
        f"{name}: {_step_name(step)}"
        for name, job in jobs(DOCKER_IMAGE_WORKFLOW).items()
        for step in job.get("steps") or []
        if "secrets." in str(step)
        and PUBLISH_GUARD not in str(step.get("if", ""))
        and PUBLISH_GUARD not in str(job.get("if", ""))
    ]

    assert not exposed, f"these steps read a secret without the publish guard: {exposed}"


def test_no_step_echoes_a_secret() -> None:
    echoed = [
        _step_name(step)
        for job in jobs(DOCKER_IMAGE_WORKFLOW).values()
        for step in job.get("steps") or []
        if "secrets." in str(step.get("run", ""))
    ]

    assert not echoed, f"these run scripts expand a secret: {echoed}"


@pytest.mark.parametrize("job", PUBLISH_ONLY_JOBS)
def test_the_registry_jobs_only_run_when_publishing(job: str) -> None:
    assert docker_image_job(job)["if"] == PUBLISH_GUARD


def test_the_manifest_list_waits_for_every_platform() -> None:
    """No tag is created while any platform has failed its build or smoke test."""
    assert _needs(docker_image_job("merge")) == ("build",)


@pytest.mark.parametrize("job", ["sign", "sbom"])
def test_every_cosign_call_is_retried(job: str) -> None:
    """A transparency-log hiccup should not fail a release that already pushed."""
    scripts = [str(step.get("run", "")) for step in docker_image_job(job)["steps"]]
    calls_made = [
        line.strip()
        for script in scripts
        for line in script.splitlines()
        if "cosign " in line and not line.strip().startswith("#")
    ]

    assert calls_made, f"{job} calls no cosign"
    assert all(line.startswith("retry cosign ") for line in calls_made), calls_made


def test_every_action_is_pinned_to_a_full_commit_sha_with_its_version() -> None:
    lines = [line for line in DOCKER_IMAGE_WORKFLOW.read_text(encoding="utf-8").splitlines() if "uses:" in line]

    assert lines
    unpinned = [line.strip() for line in lines if not PINNED_USES.match(line)]
    assert not unpinned, f"these actions are not pinned to a commit: {unpinned}"


def _tag_guard_exit(publish: str, tags: str) -> tuple[int, str]:
    """Run the build job's first step the way the runner would, with the two inputs it reads."""
    step = docker_image_build_steps()[0]
    bash = shutil.which("bash")
    assert bash, "a POSIX shell is needed to run the guard the way the runner does"
    with tempfile.TemporaryDirectory() as scratch:
        result = subprocess.run(  # noqa: S603
            [bash, "-c", str(step["run"])],
            env={"PUBLISH": publish, "TAGS": tags},
            cwd=scratch,
            capture_output=True,
            text=True,
            check=False,
            timeout=GUARD_TIMEOUT_SECONDS,
        )
    return result.returncode, result.stdout + result.stderr


def test_the_tag_guard_reads_the_two_inputs_it_judges() -> None:
    step = docker_image_build_steps()[0]

    assert step["env"] == {"PUBLISH": "${{ inputs.publish }}", "TAGS": "${{ inputs.tags }}"}
    assert "if" not in step, "the guard must run for every platform, skipped or not"


@pytest.mark.parametrize("tags", ["", "\n", "  \n\t\n"], ids=["empty", "newline", "blank-lines"])
def test_publishing_without_a_tag_fails_before_building(tags: str) -> None:
    code, output = _tag_guard_exit("true", tags)

    assert code != 0
    assert TAG_GUARD_MESSAGE in output


@pytest.mark.parametrize(
    ("publish", "tags"),
    [("true", "registry.example/opsmill/infrahub-sync:1.0.0"), ("false", ""), ("false", "infrahub-sync:pr")],
)
def test_the_tag_guard_admits_a_tagged_publish_and_any_build_only_run(publish: str, tags: str) -> None:
    code, output = _tag_guard_exit(publish, tags)

    assert code == 0, output


# --------------------------------------------------------------------------
# release path
# --------------------------------------------------------------------------
# A release pull request merged to `main` tags the release and marks a pre-release
# as one (release-publish.yml). Publishing that release runs trigger-release.yml,
# which hands the flag to workflow-publish.yml, which ships PyPI and then the image.
# `latest` only moves for a stable release that GitHub also calls its latest.
RELEASE_PUBLISH_WORKFLOW = WORKFLOWS / "release-publish.yml"
TRIGGER_RELEASE_WORKFLOW = WORKFLOWS / "trigger-release.yml"
METADATA_ACTION = "docker/metadata-action"
PRERELEASE_COMMAND = "uv run --no-project --with packaging python -c"
PRERELEASE_PROGRAM = re.compile(re.escape(PRERELEASE_COMMAND) + r"\s+'(?P<program>[^']*)'")
CREATE_RELEASE_STEP = "Create the tag and the GitHub Release"
# A stub that records how it was called, standing in for `gh` so the scripts run offline.
ARGV_STUB = '#!/bin/sh\nprintf "%s\\n" "$@" > "$STUB_ARGV"\nprintf "%s\\n" "${STUB_STDOUT:-}"\n'


def release_publish_steps() -> list[dict]:
    """Return the steps of the job that tags and publishes a release, in order."""
    return job_of(RELEASE_PUBLISH_WORKFLOW, "publish")["steps"]


def _step_running(steps: list[dict], what: str, matches: Callable[[dict], bool]) -> tuple[int, dict]:
    """Return the single step that does something, with its position, located by what it does."""
    found = [(index, step) for index, step in enumerate(steps) if matches(step)]
    assert len(found) == 1, f"{len(found)} steps {what}"
    return found[0]


def prerelease_step() -> tuple[int, dict]:
    return _step_running(
        release_publish_steps(), "decide the pre-release flag", lambda step: PRERELEASE_COMMAND in str(step.get("run"))
    )


def create_release_step() -> tuple[int, dict]:
    return _step_running(
        release_publish_steps(), "create the release", lambda step: "gh release create" in str(step.get("run"))
    )


def _run_with_stub(script: str, env: dict[str, str], stdout: str = "") -> tuple[list[str], dict[str, str]]:
    """Run a step script under bash with `gh` stubbed; return the stub's argv and the step's outputs."""
    bash = shutil.which("bash")
    assert bash, "a POSIX shell is needed to run the step the way the runner does"
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        stub = root / "bin" / "gh"
        stub.parent.mkdir()
        stub.write_text(ARGV_STUB, encoding="utf-8")
        stub.chmod(0o755)
        output = root / "github-output"
        output.touch()
        result = subprocess.run(  # noqa: S603
            [bash, "-c", script],
            env={
                **env,
                "PATH": f"{stub.parent}:{os.environ['PATH']}",
                "STUB_ARGV": str(root / "argv"),
                "STUB_STDOUT": stdout,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY": "opsmill/infrahub-sync",
                "GITHUB_SHA": "0" * 40,
            },
            cwd=scratch,
            capture_output=True,
            text=True,
            check=False,
            timeout=GUARD_TIMEOUT_SECONDS,
        )
        argv_file = root / "argv"
        argv = argv_file.read_text(encoding="utf-8").splitlines() if argv_file.exists() else []
        outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert result.returncode == 0, result.stdout + result.stderr
    return argv, outputs


def test_the_prerelease_flag_is_decided_before_the_release_is_created() -> None:
    decide, step = prerelease_step()
    create, created = create_release_step()

    assert decide < create
    assert created["name"] == CREATE_RELEASE_STEP
    assert step["if"] == created["if"] == "steps.decide.outputs.publish == 'true'"
    assert step["env"]["VERSION"] == "${{ steps.decide.outputs.version }}"
    assert created["env"]["PRERELEASE"] == f"${{{{ steps.{step['id']}.outputs.prerelease }}}}"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("3.0.0", "false"),
        ("2.0.1", "false"),
        ("3.0.0.post1", "false"),
        ("3.0.0a1", "true"),
        ("3.0.0b2", "true"),
        ("3.0.0rc1", "true"),
        ("3.0.0.dev4", "true"),
    ],
)
def test_the_prerelease_flag_follows_packaging_version(version: str, expected: str) -> None:
    """Alpha, beta, release-candidate and dev versions are pre-releases; nothing else is."""
    _index, step = prerelease_step()
    match = PRERELEASE_PROGRAM.search(str(step["run"]))
    assert match, "the pre-release program is not a single-quoted `python -c` argument"
    program = match["program"]
    assert "packaging.version" in program
    assert "is_prerelease" in program
    assert "is_devrelease" in program

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        env={"VERSION": version, "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
        timeout=GUARD_TIMEOUT_SECONDS,
    )

    assert result.stdout.strip() == f"prerelease={expected}"


@pytest.mark.parametrize(
    ("prerelease", "flags"),
    [("true", ["--prerelease", "--latest=false"]), ("false", ["--latest"])],
)
def test_the_release_is_created_under_the_bare_version_with_its_prerelease_flags(
    prerelease: str, flags: list[str]
) -> None:
    _index, step = create_release_step()

    argv, _outputs = _run_with_stub(str(step["run"]), {"VERSION": "3.0.0", "PRERELEASE": prerelease})

    assert argv[:3] == ["release", "create", "3.0.0"], "the tag is the bare version, with no `v`"
    assert [arg for arg in argv if arg.startswith("--latest") or arg == "--prerelease"] == flags


def test_the_release_trigger_passes_the_prerelease_flag_through() -> None:
    job = job_of(TRIGGER_RELEASE_WORKFLOW, "publish")

    assert called_workflow(job) == PUBLISH_WORKFLOW
    assert job["secrets"] == "inherit"
    assert job["with"] == {
        "publish": True,
        "version": "${{ github.ref_name }}",
        "prerelease": "${{ github.event.release.prerelease }}",
    }


@pytest.mark.parametrize("trigger", ["workflow_call", "workflow_dispatch"])
def test_the_publish_workflow_takes_a_prerelease_input(trigger: str) -> None:
    declared = triggers_of(PUBLISH_WORKFLOW)[trigger]["inputs"]["prerelease"]

    assert {key: declared.get(key) for key in ("type", "required", "default")} == {
        "type": "boolean",
        "required": False,
        "default": False,
    }


def test_the_package_upload_honours_the_publish_input() -> None:
    uploads_ = [
        step
        for step in job_of(PUBLISH_WORKFLOW, "publish_to_pypi")["steps"]
        if any(command in str(step.get("run", "")) for command in PACKAGE_UPLOAD)
    ]

    assert len(uploads_) == 1
    assert str(uploads_[0].get("if", "")).strip("${} ") == PUBLISH_GUARD


def docker_meta_step(what: str, matches: Callable[[dict], bool]) -> dict:
    return _step_running(job_of(PUBLISH_WORKFLOW, "docker_meta")["steps"], what, matches)[1]


def test_the_image_metadata_waits_for_the_package_and_pins_the_release_commit() -> None:
    """`ref` and the revision label are one SHA, which the image smoke test checks."""
    job = job_of(PUBLISH_WORKFLOW, "docker_meta")
    ref = docker_meta_step("set the ref", lambda step: step.get("id") == "ref")

    assert _needs(job) == ("publish_to_pypi",)
    assert ref["run"].strip() == 'echo "ref=${{ github.sha }}" >> "$GITHUB_OUTPUT"'
    assert job["outputs"] == {
        "tags": "${{ steps.meta.outputs.tags }}",
        "labels": "${{ steps.meta.outputs.labels }}",
        "ref": "${{ steps.ref.outputs.ref }}",
    }


def test_the_image_is_tagged_with_its_version_and_latest_only_by_decision() -> None:
    meta = docker_meta_step("compute the metadata", lambda step: _uses(step, METADATA_ACTION))
    latest = docker_meta_step("decide latest", lambda step: "releases/latest" in str(step.get("run", "")))
    declared = meta["with"]

    assert meta["id"] == "meta"
    assert declared["images"].strip() == "${{ vars.HARBOR_HOST }}/${{ github.repository }}"
    assert declared["tags"].strip() == "type=raw,value=${{ inputs.version }}"
    assert declared["flavor"].strip() == f"latest=${{{{ steps.{latest['id']}.outputs.latest }}}}"
    assert {line.strip() for line in declared["labels"].splitlines() if line.strip()} == {
        "org.opencontainers.image.source=${{ github.server_url }}/${{ github.repository }}",
        "org.opencontainers.image.version=${{ inputs.version }}",
        "org.opencontainers.image.revision=${{ github.sha }}",
    }
    assert latest["env"]["VERSION"] == "${{ inputs.version }}"
    assert latest["env"]["PRERELEASE"] == "${{ inputs.prerelease }}"


@pytest.mark.parametrize(
    ("prerelease", "github_latest", "expected"),
    [
        ("false", "3.0.0", "true"),
        ("false", "3.1.0", "false"),
        ("false", "", "false"),
        ("true", "3.0.0", "false"),
    ],
    ids=["stable-and-latest", "stable-backport", "no-release-yet", "prerelease"],
)
def test_latest_moves_only_for_a_stable_release_github_calls_latest(
    prerelease: str, github_latest: str, expected: str
) -> None:
    step = docker_meta_step("decide latest", lambda step: "releases/latest" in str(step.get("run", "")))

    argv, outputs = _run_with_stub(
        str(step["run"]), {"VERSION": "3.0.0", "PRERELEASE": prerelease}, stdout=github_latest
    )

    assert outputs["latest"] == expected
    if prerelease == "false":
        assert argv[:2] == ["api", "repos/opsmill/infrahub-sync/releases/latest"]
        assert "tag_name" in " ".join(argv[2:])


def test_the_metadata_action_is_pinned_to_a_full_commit_sha_with_its_version() -> None:
    lines = [line for line in PUBLISH_WORKFLOW.read_text(encoding="utf-8").splitlines() if METADATA_ACTION in line]

    assert lines
    assert all(PINNED_USES.match(line) for line in lines), lines


def test_the_release_publishes_the_image_through_the_reusable_workflow() -> None:
    job = job_of(PUBLISH_WORKFLOW, "publish_docker_image")

    assert called_workflow(job) == DOCKER_IMAGE_WORKFLOW
    assert _needs(job) == ("docker_meta",)
    assert job["secrets"] == "inherit"
    assert job["with"] == {
        "publish": "${{ inputs.publish }}",
        "version": "${{ inputs.version }}",
        "ref": "${{ needs.docker_meta.outputs.ref }}",
        "tags": "${{ needs.docker_meta.outputs.tags }}",
        "labels": "${{ needs.docker_meta.outputs.labels }}",
    }


@pytest.mark.parametrize(
    ("caller", "job", "called"),
    [
        (PUBLISH_WORKFLOW, "publish_docker_image", DOCKER_IMAGE_WORKFLOW),
        (TRIGGER_RELEASE_WORKFLOW, "publish", PUBLISH_WORKFLOW),
    ],
    ids=["publish->image", "trigger->publish"],
)
def test_each_release_call_grants_what_the_called_workflow_requests(caller: Path, job: str, called: Path) -> None:
    """The generic case above only reads `trigger-*` callers; the release chain has one that is not."""
    granted = job_permissions(caller, job) or permissions(caller)

    assert granted, f"{caller.name} job {job} grants nothing explicitly, so signing gets no id-token"
    for asking in jobs(called):
        for scope, level in (job_permissions(called, asking) or permissions(called)).items():
            assert ACCESS[level] <= ACCESS[granted.get(scope, "none")], (
                f"{called.name} job {asking} requests {scope}: {level}, which {caller.name} job {job} does not grant"
            )


# The root `docker-compose.yml` names the release's image by version. The release
# pull request pins it (trigger-push-stable.yml), and the tag is refused for a file
# pinned to any other version (release-publish.yml).
PREPARE_RELEASE_WORKFLOW = WORKFLOWS / TWO_LINE_AUTOMATION
PIN_COMPOSE_COMMAND = 'uv run --no-sync invoke release.update-docker-compose --version "${VERSION}"'
VALIDATE_COMPOSE_COMMAND = 'uv run --no-sync invoke release.validate-docker-compose --version "${VERSION}"'
RELEASE_PR_GIT_ADD = re.compile(r"^\s*git add (?P<paths>.+)$", re.MULTILINE)


def prepare_release_steps() -> list[dict]:
    """Return the steps of the job that prepares the release pull request, in order."""
    return job_of(PREPARE_RELEASE_WORKFLOW, "prepare_release")["steps"]


def _syncs_dev_tools_before(steps: list[dict], index: int) -> bool:
    """Report whether the step at `index`, or one before it, installs the dev extra that carries `invoke`."""
    return any(
        "uv sync" in str(step.get("run")) and "--extra dev" in str(step.get("run")) for step in steps[: index + 1]
    )


def test_the_release_pull_request_pins_the_compose_image_after_the_lock() -> None:
    steps = prepare_release_steps()
    names = [step.get("name") for step in steps]
    pin, step = _step_running(steps, "pin the Compose image", lambda s: PIN_COMPOSE_COMMAND in str(s.get("run")))

    assert step["name"] == "Pin the Compose image to the release"
    assert names[pin - 1] == "Update lock file", "the pin runs right after the lock is refreshed"
    assert step["env"]["VERSION"] == "${{ steps.normalize.outputs.version }}"
    assert _syncs_dev_tools_before(steps, pin), "`uv run --no-sync invoke` needs the dev extra installed first"


def test_the_release_pull_request_commits_the_pinned_compose_file() -> None:
    _index, step = _step_running(
        prepare_release_steps(), "open the release pull request", lambda s: "git commit" in str(s.get("run"))
    )
    added = RELEASE_PR_GIT_ADD.findall(str(step["run"]))

    assert len(added) == 1
    assert "docker-compose.yml" in added[0].split()


def test_the_tag_is_refused_for_a_compose_file_pinned_to_another_version() -> None:
    steps = release_publish_steps()
    check, step = _step_running(
        steps, "validate the Compose pin", lambda s: VALIDATE_COMPOSE_COMMAND in str(s.get("run"))
    )
    create, _created = create_release_step()

    assert step["name"] == "Refuse a Compose file pinned to another version"
    assert check < create
    assert step["if"] == "steps.decide.outputs.publish == 'true'"
    assert step["env"]["VERSION"] == "${{ steps.decide.outputs.version }}"
    assert _syncs_dev_tools_before(steps, check), "`uv run --no-sync invoke` needs the dev extra installed first"


# --------------------------------------------------------------------------
# pull-request gate
# --------------------------------------------------------------------------
# A pull request that changes an image input builds and smoke-tests the image on
# both platforms through `ci-docker-image.yml`, publishing nothing and using no
# secret. The required check `Full qualification` always runs and passes when
# that build succeeded, or was skipped because no image input changed.
REQUIRED_JOB_NAME = "Full qualification"
IMAGE_CHANGES_JOB = "image-changes"
PR_IMAGE_JOB = "image"
IMAGE_INPUTS_FILTER = "image_inputs"
PATHS_FILTER_ACTION = "opsmill/paths-filter"
PR_IMAGE_REF = "${{ github.event.pull_request.head.sha || github.sha }}"
PR_IMAGE_TAG = "infrahub-sync:pr"
OCI_LABEL_KEYS = (
    "org.opencontainers.image.source",
    "org.opencontainers.image.version",
    "org.opencontainers.image.revision",
)
# Result pairs (image-changes, image) and whether the required check passes them.
REQUIRED_CHECK_VERDICTS = [
    ("success", "success", 0),
    ("success", "skipped", 0),
    ("success", "failure", 1),
    ("success", "cancelled", 1),
    ("failure", "skipped", 1),
    ("cancelled", "skipped", 1),
    ("skipped", "skipped", 1),
]


def pr_job(name: str) -> dict:
    """Return one job of the pull-request caller."""
    return job_of(DEVELOP_CALLER, name)


def required_check_script() -> tuple[dict, str]:
    """Return the required check's one step's environment and script."""
    steps = pr_job(REQUIRED_JOB)["steps"]
    assert len(steps) == 1, f"{REQUIRED_JOB} has {len(steps)} steps"
    return steps[0].get("env") or {}, str(steps[0]["run"])


def _required_check_exit(changes: str, image: str) -> tuple[int, str]:
    """Run the required check's script with each `needs.<job>.result` it reads set as given."""
    env, script = required_check_script()
    results = {
        f"${{{{ needs.{IMAGE_CHANGES_JOB}.result }}}}": changes,
        f"${{{{ needs.{PR_IMAGE_JOB}.result }}}}": image,
    }
    bash = shutil.which("bash")
    assert bash, "a POSIX shell is needed to run the check the way the runner does"
    with tempfile.TemporaryDirectory() as scratch:
        result = subprocess.run(  # noqa: S603
            [bash, "-c", script],
            env={key: results.get(str(value), str(value)) for key, value in env.items()},
            cwd=scratch,
            capture_output=True,
            text=True,
            check=False,
            timeout=GUARD_TIMEOUT_SECONDS,
        )
    return result.returncode, result.stdout + result.stderr


def test_the_required_check_keeps_its_id_and_name() -> None:
    """Branch protection requires it by this name, so renaming it would need a coordinated change."""
    assert pr_job(REQUIRED_JOB)["name"] == REQUIRED_JOB_NAME


def test_the_required_check_always_runs_after_the_filter_and_the_image_call() -> None:
    """A skipped job satisfies a required check, so this one must run whatever the image call did."""
    job = pr_job(REQUIRED_JOB)

    assert list(_needs(job)) == [IMAGE_CHANGES_JOB, PR_IMAGE_JOB]
    assert job.get("if") == "${{ always() }}" or job.get("if") == "always()"


def test_the_required_check_reads_both_results_it_judges() -> None:
    env, _script = required_check_script()

    assert set(env.values()) >= {
        f"${{{{ needs.{IMAGE_CHANGES_JOB}.result }}}}",
        f"${{{{ needs.{PR_IMAGE_JOB}.result }}}}",
    }


@pytest.mark.parametrize(("changes", "image", "expected"), REQUIRED_CHECK_VERDICTS)
def test_the_required_check_passes_only_a_built_or_skipped_image(changes: str, image: str, expected: int) -> None:
    """`skipped` passes only because the filter ran and found no image input."""
    code, _output = _required_check_exit(changes, image)

    assert code == expected, f"image-changes={changes}, image={image} exited {code}"


@pytest.mark.parametrize(
    ("changes", "image", "blamed"),
    [("failure", "skipped", IMAGE_CHANGES_JOB), ("success", "failure", PR_IMAGE_JOB)],
)
def test_each_refusal_names_the_job_that_failed(changes: str, image: str, blamed: str) -> None:
    code, output = _required_check_exit(changes, image)

    assert code == 1
    assert f"`{blamed}`" in output, f"the refusal does not name {blamed}: {output!r}"


def test_the_pull_request_image_call_builds_without_publishing_or_secrets() -> None:
    """Forks get the same check as internal branches, because nothing here needs a secret."""
    job = pr_job(PR_IMAGE_JOB)
    given = job.get("with") or {}

    assert called_workflow(job) == DOCKER_IMAGE_WORKFLOW
    assert given.get("publish") is False
    assert "secrets" not in job, f"{PR_IMAGE_JOB} passes secrets into a pull-request build"
    assert given.get("ref") == PR_IMAGE_REF
    assert given.get("tags") == PR_IMAGE_TAG


def test_the_pull_request_image_call_grants_no_write_beyond_the_signing_token() -> None:
    """`id-token` only because GitHub checks every called job, the skipped signing jobs included."""
    granted = job_permissions(DEVELOP_CALLER, PR_IMAGE_JOB)

    assert granted == {"contents": "read", "id-token": "write"}
    assert "actions" not in (granted or {})


def test_the_pull_request_image_labels_name_the_commit_it_builds() -> None:
    """The smoke test refuses a revision label naming any commit but the one checked out."""
    labels = str((pr_job(PR_IMAGE_JOB).get("with") or {}).get("labels", ""))
    pairs = dict(line.split("=", 1) for line in labels.strip().splitlines())

    assert tuple(pairs) == OCI_LABEL_KEYS
    assert pairs["org.opencontainers.image.source"] == "${{ github.server_url }}/${{ github.repository }}"
    assert pairs["org.opencontainers.image.revision"] == PR_IMAGE_REF
    assert f"needs.{IMAGE_CHANGES_JOB}.outputs.version" in pairs["org.opencontainers.image.version"]


def test_the_image_call_runs_only_when_an_image_input_changes() -> None:
    job = pr_job(PR_IMAGE_JOB)

    assert list(_needs(job)) == [IMAGE_CHANGES_JOB]
    assert str(job.get("if", "")).strip() == f"needs.{IMAGE_CHANGES_JOB}.outputs.{IMAGE_INPUTS_FILTER} == 'true'"
    assert filter_patterns(IMAGE_INPUTS_FILTER), f"{IMAGE_INPUTS_FILTER} matches nothing"


def test_the_filter_job_reads_the_image_inputs_filter_and_the_version() -> None:
    job = pr_job(IMAGE_CHANGES_JOB)
    filtering = [
        step for step in job.get("steps") or [] if str(step.get("uses", "")).startswith(f"{PATHS_FILTER_ACTION}@")
    ]

    assert len(filtering) == 1
    assert filtering[0]["with"]["filters"] == ".github/file-filters.yml"
    outputs = job.get("outputs") or {}
    assert outputs[IMAGE_INPUTS_FILTER] == f"${{{{ steps.{filtering[0]['id']}.outputs.{IMAGE_INPUTS_FILTER} }}}}"
    assert "version" in outputs


def test_the_tier_decision_and_its_label_triggers_are_gone() -> None:
    """Nothing heavy is left to opt into, so neither the decision job nor the label events remain."""
    declared = triggers_of(DEVELOP_CALLER)["pull_request"]

    assert "qualification" not in develop_jobs()
    assert not [name for name, job in develop_jobs().items() if "qualify" in (job.get("outputs") or {})]
    assert not {"labeled", "unlabeled"} & set(declared.get("types") or ())
