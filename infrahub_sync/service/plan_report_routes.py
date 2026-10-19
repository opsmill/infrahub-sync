"""The plan report routes of the Sync API (spec 009, contracts/sync-api.md).

`GET /runs/{run_id}/plan/problems`, `/plan/left-out` and `/plan/kept` return one page of a
saved plan's report, with the bearer-token authentication of the other run routes.
"""

# No `from __future__ import annotations`: FastAPI evaluates each route's annotations, and the
# `Depends(authenticate)` in them names a variable of `plan_report_router`, not a module global.
from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import APIRouter, Depends, Query

from infrahub_sync.client.models import REPORT_PAGE_LIMIT
from infrahub_sync.plan.models import ProblemType

from .auth import Principal
from .models import (
    PlanKeptPageResource,
    PlanLeftOutPageResource,
    PlanProblemsPageResource,
)

if TYPE_CHECKING:
    from .service import RunService

_CHECKSUM_PATTERN = r"^[0-9a-f]{64}$"


def plan_report_router(
    service: "RunService", authenticate: Any, responses: dict[int | str, dict[str, Any]]
) -> APIRouter:
    """Create the three authenticated plan report routes."""
    router = APIRouter(tags=["Plan reports"])

    @router.get("/runs/{run_id}/plan/problems", responses=responses)
    def get_plan_problems(
        run_id: str,
        *,
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=REPORT_PAGE_LIMIT)] = REPORT_PAGE_LIMIT,
        problem_type: ProblemType | None = None,
        kind: str | None = None,
        expected_checksum: Annotated[str | None, Query(pattern=_CHECKSUM_PATTERN)] = None,
    ) -> PlanProblemsPageResource:
        return cast(
            "PlanProblemsPageResource",
            service.get_plan_report(
                run_id,
                "problems",
                offset=offset,
                limit=limit,
                problem_type=problem_type,
                kind=kind,
                expected_checksum=expected_checksum,
            ),
        )

    @router.get("/runs/{run_id}/plan/left-out", responses=responses)
    def get_plan_left_out(
        run_id: str,
        *,
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=REPORT_PAGE_LIMIT)] = REPORT_PAGE_LIMIT,
        kind: str | None = None,
        expected_checksum: Annotated[str | None, Query(pattern=_CHECKSUM_PATTERN)] = None,
    ) -> PlanLeftOutPageResource:
        return cast(
            "PlanLeftOutPageResource",
            service.get_plan_report(
                run_id, "left-out", offset=offset, limit=limit, kind=kind, expected_checksum=expected_checksum
            ),
        )

    @router.get("/runs/{run_id}/plan/kept", responses=responses)
    def get_plan_kept(
        run_id: str,
        *,
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=REPORT_PAGE_LIMIT)] = REPORT_PAGE_LIMIT,
        kind: str | None = None,
        expected_checksum: Annotated[str | None, Query(pattern=_CHECKSUM_PATTERN)] = None,
    ) -> PlanKeptPageResource:
        return cast(
            "PlanKeptPageResource",
            service.get_plan_report(
                run_id, "kept", offset=offset, limit=limit, kind=kind, expected_checksum=expected_checksum
            ),
        )

    return router
