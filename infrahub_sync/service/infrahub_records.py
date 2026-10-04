"""Sync's records in Infrahub, as the API and the worker build them from their environment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from infrahub_sync.platform.client import (
    PlatformSettings,
    PlatformSettingsError,
    default_branch_sync,
    service_client_sync,
)
from infrahub_sync.platform.records import InfrahubConfigurations, RunMirror

from .models import EmittedPlanResource
from .service import PLAN_ARTIFACT_ID
from .storage import service_version_lock

if TYPE_CHECKING:
    from infrahub_sync.product_store import ProductProjection


def infrahub_records() -> tuple[InfrahubConfigurations, RunMirror]:
    """Configurations and run mirrors in Infrahub, written by the service account.

    Raises `PlatformSettingsError` when the environment names no Infrahub.
    """
    client = service_client_sync(PlatformSettings.from_environment())

    def branch() -> str:
        return default_branch_sync(client)

    return (
        InfrahubConfigurations(client=client, branch=branch, version_lock=service_version_lock),
        RunMirror(client=client, branch=branch),
    )


def optional_infrahub_records() -> tuple[InfrahubConfigurations | None, RunMirror | None]:
    """Infrahub's records when the environment names an Infrahub; otherwise none.

    Without the Infrahub settings, configurations are read from the product store, as in
    local and test use, and runs are mirrored nowhere.
    """
    try:
        return infrahub_records()
    except PlatformSettingsError:
        return None, None


def stored_plan_checksum(projection: ProductProjection, run_id: str) -> str | None:
    """The checksum of the run's saved plan, once the plan stage has published it."""
    published = projection.lookup_artifact(run_id, PLAN_ARTIFACT_ID).value
    if published is None:
        return None
    try:
        return EmittedPlanResource.model_validate_json(published).checksum
    except (ValidationError, ValueError):
        return None


def mirror_run(projection: ProductProjection, mirror: RunMirror, run_id: str) -> None:
    """Copy a run's current state into Infrahub, best effort; Sync's database holds it."""
    stored = projection.lookup_run(run_id).value
    if stored is None:
        return
    checksum = stored_plan_checksum(projection, run_id)
    extra = {"plan_checksum": checksum} if checksum is not None else None
    mirror.mirror(stored, extra, current=lambda: projection.lookup_run(run_id).value)


def mirror_finished(projection: ProductProjection, run_id: str) -> None:
    """Copy a finished run's state into Infrahub when the environment names an Infrahub."""
    _configurations, mirror = optional_infrahub_records()
    if mirror is not None:
        mirror_run(projection, mirror, run_id)
