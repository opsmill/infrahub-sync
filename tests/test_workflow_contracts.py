"""Workflow declarations whose consequences only appear once a run is under way.

Each invariant here covers a failure that costs a full run to discover and
leaves little to read: a permission the caller will not grant ends the run in
`startup_failure` with no jobs at all, a concurrency group shared with a
sibling cancels one of them with a one-line annotation, and an upload that
does not opt into hidden files finds nothing at the very end of the gate.

Only explicit declarations are compared. A workflow that states no
`permissions` or no `concurrency`, or uses one of the shorthand permission
strings, is left to GitHub.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CALLERS = tuple(sorted(path.name for path in WORKFLOWS.glob("trigger-*.yml")))

# GitHub's three access levels, ordered so "grants at least" is a comparison.
ACCESS = {"none": 0, "read": 1, "write": 2}

UPLOAD_ACTION = "actions/upload-artifact"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def jobs(caller: Path) -> dict[str, dict]:
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
