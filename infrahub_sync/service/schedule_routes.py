"""HTTP routes of the schedules of the Sync API (spec 021, contracts/http-api.md)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from .auth import Principal
from .config_routes import CONFIG_ID_PATTERN, parse_page_limit
from .models import (
    ChangeScheduleRequest,
    CreateScheduleRequest,
    ErrorEnvelope,
    ScheduleActionRequest,
    SchedulePage,
    ScheduleResource,
)
from .schedules import ScheduleService

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 404, 409, 422, 503)
}


def schedule_router(service: ScheduleService, authenticate: Any, idempotency_key: Any) -> APIRouter:
    """Create the authenticated schedule routes: create, list, view, change, pause, resume and delete.

    Every answer is built by the service and returned as it is, so a field that the API does
    not deliver yet is absent from the answer instead of null.
    """
    router = APIRouter()

    @router.post("/schedules", status_code=201, response_model=ScheduleResource, responses=_ERROR_RESPONSES)
    async def create_schedule(
        body: CreateScheduleRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        """Create a schedule that starts a ``sync`` run of one configuration version on a cron cadence."""
        status, content = await service.create(body, principal, key)
        return JSONResponse(status_code=status, content=content)

    @router.get("/schedules", response_model=SchedulePage, responses=_ERROR_RESPONSES)
    def list_schedules(
        _principal: Annotated[Principal, Depends(authenticate)],
        config_id: Annotated[str | None, Query(pattern=CONFIG_ID_PATTERN)] = None,
        state: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, Query()] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> JSONResponse:
        """Return one page of schedules, newest first; without ``state``, deleted schedules are left out."""
        return JSONResponse(
            content=service.list_schedules(
                config_id=config_id, state=state, cursor=cursor, limit=parse_page_limit(limit)
            )
        )

    @router.get("/schedules/{schedule_id}", response_model=ScheduleResource, responses=_ERROR_RESPONSES)
    def get_schedule(schedule_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> JSONResponse:
        """Return one schedule, also a deleted schedule."""
        return JSONResponse(content=service.get_schedule(schedule_id))

    @router.post("/schedules/{schedule_id}/change", response_model=ScheduleResource, responses=_ERROR_RESPONSES)
    async def change_schedule(
        schedule_id: str,
        body: ChangeScheduleRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        """Change one or more settings of a schedule."""
        status, content = await service.change(schedule_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    @router.post("/schedules/{schedule_id}/pause", response_model=ScheduleResource, responses=_ERROR_RESPONSES)
    async def pause_schedule(
        schedule_id: str,
        body: ScheduleActionRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        """Pause a schedule: it starts no run until it is resumed."""
        status, content = await service.pause(schedule_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    @router.post("/schedules/{schedule_id}/resume", response_model=ScheduleResource, responses=_ERROR_RESPONSES)
    async def resume_schedule(
        schedule_id: str,
        body: ScheduleActionRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        """Make a paused schedule active, or a stopped schedule whose reason no longer holds."""
        status, content = await service.resume(schedule_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    @router.post("/schedules/{schedule_id}/delete", response_model=ScheduleResource, responses=_ERROR_RESPONSES)
    async def delete_schedule(
        schedule_id: str,
        body: ScheduleActionRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        """Delete a schedule; its record, its runs and its open proposed change are kept."""
        status, content = await service.delete(schedule_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    return router
