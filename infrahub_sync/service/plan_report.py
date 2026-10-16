"""The plan report of a saved plan, as the Sync API publishes and pages it (spec 009).

The plan stage's worker publishes the report beside the review document as one public JSON
artifact, `plan-report`, so the service reads it without the plan directory. The service
pages it in the stable report order through `infrahub_sync.plan.reader.report_page`
(contracts/sync-api.md "Problem list, left-out list and kept list").

A run whose review document was published by an earlier release has no `plan-report`
artifact. Its plan was saved in format 2 or 3, whose report is empty, so the routes return
empty lists for it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError

from infrahub_sync.plan.models import PlanReport, ProblemType, ReportEntryName
from infrahub_sync.plan.reader import report_page

from .models import (
    EmittedPlanKeptPageResource,
    EmittedPlanLeftOutPageResource,
    EmittedPlanProblemsPageResource,
    PlanKeptPageResource,
    PlanLeftOutPageResource,
    PlanProblemsPageResource,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.product_store import ProductProjection

    from .service import ServiceAPIError

PLAN_REPORT_ARTIFACT_ID = "plan-report"
PLAN_REPORT_ARTIFACT_KIND = "saved-plan-report"

ReportRoute = Literal["problems", "left-out", "kept"]
ReportPageResource = PlanProblemsPageResource | PlanLeftOutPageResource | PlanKeptPageResource

_ROUTE_ENTRIES: dict[ReportRoute, ReportEntryName] = {
    "problems": "problem",
    "left-out": "left_out",
    "kept": "kept",
}
_ROUTE_MODELS: dict[ReportRoute, type[ReportPageResource]] = {
    "problems": EmittedPlanProblemsPageResource,
    "left-out": EmittedPlanLeftOutPageResource,
    "kept": EmittedPlanKeptPageResource,
}


def plan_report_document(run_id: str, plan_checksum: str, report: PlanReport) -> bytes:
    """Return the `plan-report` artifact's bytes: the report lines in the stable order."""
    return json.dumps(
        {"run_id": run_id, "plan_checksum": plan_checksum, "entries": report.entries()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _stored_report(
    projection: ProductProjection, run_id: str, error: Callable[..., ServiceAPIError]
) -> tuple[PlanReport, str | None]:
    """Return the published report and the plan checksum it records (`None` when absent)."""
    result = projection.lookup_artifact(run_id, PLAN_REPORT_ARTIFACT_ID)
    if result.value is None:
        if result.reason == "artifact-reference-not-found":
            # Published by an earlier release, for a plan whose report is empty.
            return PlanReport(), None
        if result.reason == "artifact-expired":
            raise error(410, "artifact-expired", "the retained plan report has expired", run_id=run_id)
        raise error(503, "plan-unavailable", "the retained plan report cannot be retrieved", run_id=run_id)
    try:
        document: Any = json.loads(result.value)
        report = PlanReport.from_entries(document["entries"])
        checksum = document["plan_checksum"]
    except (ValidationError, ValueError, KeyError, TypeError):
        raise error(503, "plan-unavailable", "the retained plan report is invalid", run_id=run_id) from None
    if not isinstance(checksum, str):
        raise error(503, "plan-unavailable", "the retained plan report is invalid", run_id=run_id)
    return report, checksum


def plan_report_page(
    projection: ProductProjection,
    error: Callable[..., ServiceAPIError],
    run_id: str,
    plan_checksum: str,
    route: ReportRoute,
    *,
    offset: int,
    limit: int,
    problem_type: ProblemType | None = None,
    kind: str | None = None,
    expected_checksum: str | None = None,
) -> ReportPageResource:
    """Return one page of a saved plan's report for one report route.

    `plan_checksum` is the checksum of the retained review document, which the caller has
    already read: it names the plan the page comes from.

    Raises:
        ServiceAPIError: 409 `plan-changed` when `expected_checksum` names another plan;
            410 `artifact-expired` or 503 `plan-unavailable` when the report cannot be read.
    """
    if expected_checksum is not None and expected_checksum != plan_checksum:
        raise error(409, "plan-changed", "the saved plan of this run is not the expected plan", run_id=run_id)
    report, report_checksum = _stored_report(projection, run_id, error)
    if report_checksum is not None and report_checksum != plan_checksum:
        raise error(503, "plan-unavailable", "the retained plan report belongs to another plan", run_id=run_id)
    page = report_page(
        report, entry=_ROUTE_ENTRIES[route], offset=offset, limit=limit, problem_type=problem_type, kind=kind
    )
    return _ROUTE_MODELS[route].model_validate(
        {
            "run_id": run_id,
            "plan_checksum": plan_checksum,
            "entries": [{key: value for key, value in line.items() if key != "entry"} for line in page.entries],
            "offset": page.offset,
            "limit": page.limit,
            "total": page.total,
            "next_offset": page.next_offset,
        }
    )


__all__ = [
    "PLAN_REPORT_ARTIFACT_ID",
    "PLAN_REPORT_ARTIFACT_KIND",
    "ReportPageResource",
    "ReportRoute",
    "plan_report_document",
    "plan_report_page",
]
