"""The Prefect markdown artifact that links one service flow run to its Sync run.

Every service stage that runs inside a Prefect flow run ends by publishing one markdown
artifact on that flow run: the Sync run ID, the stage, the outcome, the configuration and
destination branch, the run's operation counts, and the CLI commands that show the run. An
operator who opens the flow run in the Prefect UI reads where the run is and what to do next
without searching the logs.

The text is built from named fields of the stored run record and fixed templates only, plus
the failure's next action, which the failure boundary builds from fixed templates
(docs/docs/development/guidelines/secret-redaction.md).
It never reads a failure's `cause`, so it never holds the text of an exception, a provider's
error message, configuration content or an address. The finished text is redacted with the
stage's secrets. A failure to publish is logged and never fails the run.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from infrahub_sync.execution import ACTION_KEYS, redact

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from infrahub_sync.orchestration.flow import RunLogger
    from infrahub_sync.product_store import ProductProjection, ProductRun

# The artifact kind `_publish_plan` in `service/flow.py` records for a saved plan review.
PLAN_REVIEW_ARTIFACT_KIND = "saved-plan-review"
_KEY_UNSAFE = re.compile(r"[^a-z0-9-]+")
_KEY_DASHES = re.compile(r"-{2,}")


def artifact_key(run_id: str, stage: str) -> str:
    """The Prefect artifact key of one stage of one run: lowercase letters, digits and dashes.

    Stable for the same run and stage, so a later flow run of the same stage adds a version
    to the same key.
    """
    text = _KEY_UNSAFE.sub("-", f"sync-run-{run_id}-{stage}".lower())
    return _KEY_DASHES.sub("-", text).strip("-")


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _counts(values: Mapping[Any, Any]) -> dict[str, int]:
    return {str(key): count for key, value in values.items() if (count := _count(value)) is not None}


def _operations_by_action(summary: Mapping[str, Any]) -> dict[str, int]:
    """A plan's `by_action`, else an apply's count per action key."""
    by_action = summary.get("by_action")
    if isinstance(by_action, Mapping):
        return _counts(by_action)
    return {key: count for key in ACTION_KEYS if (count := _count(summary.get(key))) is not None}


def _operations_by_kind(summary: Mapping[str, Any]) -> dict[str, int]:
    """A plan's `by_kind`, sorted by kind name."""
    by_kind = summary.get("by_kind")
    if not isinstance(by_kind, Mapping):
        return {}
    return dict(sorted(_counts(by_kind).items()))


def _joined(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{key} {value}" for key, value in counts.items())


def _left_out_detail(summary: Mapping[str, Any]) -> str:
    """How many left-out records have a problem and how many depend on one, when recorded."""
    with_problem = _count(summary.get("left_out_with_problem"))
    dependent = _count(summary.get("left_out_dependent"))
    if with_problem is None or dependent is None:
        return ""
    return f" (with a problem: {with_problem}, depending on one: {dependent})"


def run_markdown(run: ProductRun, stage: str, outcome: str, next_action: str | None) -> str:
    """The artifact's markdown, built from the run record's named fields only.

    `outcome` and `next_action` are the values the stage's last log line used. A plan or
    `sync` stage whose summary records them adds the count of records left out and the count
    of problems (spec 019 FR-028). The next steps are `runs show`, then `runs plan --detail`
    when the run holds a saved plan review, then `runs plan --problems` and `--left-out` when
    the plan leaves out records, then `next_action` when there is one.
    """
    run_id = run.run_id
    lines = [f"# Sync run {run_id}", "", f"- Stage: {stage}", f"- Outcome: {outcome}"]
    if run.config_id is not None and run.registry_version is not None:
        lines.append(f"- Configuration: {run.config_id}@{run.registry_version}")
    if run.destination_branch:
        lines.append(f"- Destination branch: {run.destination_branch}")
    by_action = _operations_by_action(run.summary)
    if by_action:
        lines.append(f"- Operations by action: {_joined(by_action)}")
    by_kind = _operations_by_kind(run.summary)
    if by_kind:
        lines.append(f"- Operations by kind: {_joined(by_kind)}")
    left_out = _count(run.summary.get("left_out"))
    if left_out is not None:
        lines.append(f"- Records left out: {left_out}{_left_out_detail(run.summary)}")
    problems = _count(run.summary.get("problems"))
    if problems is not None:
        lines.append(f"- Problems: {problems}")
    lines.extend(("", "## Next steps", "", f"- `infrahub-sync runs show {run_id}`"))
    if any(reference.kind == PLAN_REVIEW_ARTIFACT_KIND for reference in run.artifact_refs):
        lines.append(f"- `infrahub-sync runs plan {run_id} --detail`")
    if left_out:
        lines.extend(
            (
                f"- `infrahub-sync runs plan {run_id} --problems`",
                f"- `infrahub-sync runs plan {run_id} --left-out`",
            )
        )
    if next_action:
        lines.append(f"- {next_action}")
    return "\n".join(lines) + "\n"


def publish_run_artifact(  # pylint: disable=too-many-arguments
    projection: ProductProjection,
    run_id: str,
    stage: str,
    *,
    outcome: str,
    next_action: str | None,
    secrets: Sequence[str],
    run_logger: RunLogger,
    create: Callable[..., object],
) -> None:
    """Publish the run's redacted markdown artifact; log a warning and go on if that fails.

    Reads the run record after the stage stored its outcome. A run whose record cannot be
    read gets no artifact: the artifact repeats the record, and nothing else is safe to print.
    """
    try:
        run = projection.lookup_run(run_id).value
        if run is None:
            run_logger.log(logging.WARNING, "run summary artifact skipped: run %s is unavailable", run_id)
            return
        create(
            markdown=redact(run_markdown(run, stage, outcome, next_action), secrets),
            key=artifact_key(run_id, stage),
            description=f"Sync run {run_id}, stage {stage}",
        )
    except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        run_logger.log(
            logging.WARNING,
            "run summary artifact for run %s stage %s could not be published (%s)",
            run_id,
            stage,
            type(error).__name__,
        )
