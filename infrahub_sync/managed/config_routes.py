"""HTTP-only adapter for the shared configuration application service."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from infrahub_sync.product_store import configs

from .auth import Principal
from .models import ConfigMutationRequest
from .service import ManagedAPIError


class ConfigurationRoutes:
    """Bind configuration operations to this server's durable cache location."""

    def __init__(self, product_cache_location: Path, *, secrets: tuple[str, ...] = ()) -> None:
        self._location = product_cache_location
        self._secrets = secrets

    def _call(self, operation: Any, **kwargs: Any) -> Any:
        try:
            return operation(product_cache_location=self._location, **kwargs)
        except configs.ConfigsRequestError:
            raise ManagedAPIError(400, "configs-request", "the configuration request is invalid") from None
        except configs.ConfigsValidationError:
            raise ManagedAPIError(422, "configs-validation", "the configuration is invalid") from None
        except configs.ConfigsNotFoundError as exc:
            reason = exc.reason
            raise ManagedAPIError(404, "configs-not-found", reason) from None
        except configs.ConfigsStorageError:
            raise ManagedAPIError(503, "configs-storage", "configuration storage is unavailable") from None
        except configs.ConfigsInternalError:
            raise ManagedAPIError(503, "configs-internal", "configuration service is unavailable") from None
        except configs.ConfigsError:
            raise ManagedAPIError(503, "configs-error", "configuration service is unavailable") from None

    def register(self, package: dict[str, Any]) -> Any:
        return self._call(configs.register, package=package)

    def create_version(self, config_id: str, package: dict[str, Any]) -> Any:
        return self._call(configs.create_version, config_id=config_id, package=package)

    def list_configs(self) -> Any:
        return self._call(configs.list_configs)

    def get_config(self, config_id: str) -> Any:
        return self._call(configs.get_config, config_id=config_id)

    def list_versions(self, config_id: str) -> Any:
        return self._call(configs.list_versions, config_id=config_id)

    def get_version(self, config_id: str, registry_version: int) -> Any:
        return self._call(configs.get_version, config_id=config_id, registry_version=registry_version)

    def validate(self, config_id: str, registry_version: int) -> Any:
        return self._call(configs.validate, config_id=config_id, registry_version=registry_version)


def configuration_router(routes: ConfigurationRoutes, authenticate: Any) -> APIRouter:
    """Create the seven authenticated configuration resources."""
    router = APIRouter()

    @router.post("/configs", status_code=201)
    def register(body: ConfigMutationRequest, principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        if not principal.administrator:
            raise ManagedAPIError(403, "forbidden", "administrator access is required")
        return routes.register(body.package)

    @router.post("/configs/{config_id}/versions", status_code=201)
    def create_version(
        config_id: str, body: ConfigMutationRequest, principal: Annotated[Principal, Depends(authenticate)]
    ) -> Any:
        if not principal.administrator:
            raise ManagedAPIError(403, "forbidden", "administrator access is required")
        return routes.create_version(config_id, body.package)

    @router.get("/configs")
    def list_configs(_principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        return routes.list_configs()

    @router.get("/configs/{config_id}")
    def get_config(config_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        return routes.get_config(config_id)

    @router.get("/configs/{config_id}/versions")
    def list_versions(config_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        return routes.list_versions(config_id)

    @router.get("/configs/{config_id}/versions/{registry_version}")
    def get_version(
        config_id: str, registry_version: int, _principal: Annotated[Principal, Depends(authenticate)]
    ) -> Any:
        return routes.get_version(config_id, registry_version)

    @router.post("/configs/{config_id}/versions/{registry_version}/validate")
    def validate(config_id: str, registry_version: int, _principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        return routes.validate(config_id, registry_version)

    return router
