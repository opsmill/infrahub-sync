"""Converge the durable objects one deployment needs, and change nothing else.

This is the one-off job a Compose deployment runs before its API and its worker
start: the artifact bucket, the Prefect process work pool, the installed
deployment, and the bundled declared configuration. Everything here is safe to
repeat — a second run observes what the first created and writes nothing.

Two boundaries are deliberate. Nothing in the long-running services does any of
this: an API or a worker that converged bootstrap state on startup would make
every restart a write, and would give two processes racing claims on the same
objects. And every failure leaves through one fixed family name, because this
job's inputs are credentials and endpoints and its provider errors carry them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.exceptions import ObjectNotFound
from prefect.workers.process import ProcessWorker

from infrahub_sync.configuration.models import ConfigurationPackageParseError, parse_configuration_package
from infrahub_sync.product_store import AuditEvent, ProductStoreProviderError, configs

from .deploy import WORK_POOL_ENV
from .deploy import main as apply_deployment
from .storage import (
    S3_BUCKET_ENV,
    S3_ENDPOINT_ENV,
    S3_REGION_ENV,
    service_product_projection,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sync.configuration import ConfigurationPackage
    from infrahub_sync.product_store import ProductProjection

logger = logging.getLogger(__name__)

CONFIGURATION_PATH_ENV = "INFRAHUB_SYNC_BOOTSTRAP_CONFIGURATION"

# The audit actor and reason a first registration records. Fixed values: this
# job has no operator to attribute a decision to, and the reason must not
# describe an input.
BOOTSTRAP_ACTOR = "compose-bootstrap"
BOOTSTRAP_REASON = "compose bootstrap: register the bundled configuration"
BOOTSTRAP_OUTCOME = "accepted"

# The pool type this deployment's worker joins. Prefect's own process worker
# supplies the template, so a pool created here is the one `prefect work-pool
# create --type process` would have made.
PROCESS_POOL_TYPE = "process"

# The exact object-store responses that mean the bucket is already there. Any
# other client error is a real failure, and is reported as one.
_BUCKET_PRESENT = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})


# Every way this job can fail, named. The family is the whole public message:
# each input it reads is an endpoint or a credential, and the providers
# underneath render both into their own exception text.
OBJECT_STORE_UNAVAILABLE = "object-store-unavailable"
WORK_POOL_UNAVAILABLE = "work-pool-unavailable"
WORK_POOL_CONFLICT = "work-pool-conflict"
DEPLOYMENT_FAILED = "deployment-failed"
CONFIGURATION_INVALID = "configuration-invalid"
CONFIGURATION_CONFLICT = "configuration-conflict"
PRODUCT_STORE_UNAVAILABLE = "product-store-unavailable"
SETTING_MISSING = "required-setting-missing"


class BootstrapError(RuntimeError):
    """One fixed, secret-safe bootstrap failure family.

    The message is the family name and nothing else. Every input this job reads
    is an endpoint or a credential, and the providers underneath render both into
    their exception text, so no provider detail crosses this boundary.
    """

    def __init__(self, family: str) -> None:
        super().__init__(family)
        self.family = family


def converge_bucket(client: Any, bucket: str) -> bool:
    """Create the artifact bucket when it is absent. Return whether this call made it."""
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in _BUCKET_PRESENT:
            return False
        raise BootstrapError(OBJECT_STORE_UNAVAILABLE) from None
    except BotoCoreError:
        raise BootstrapError(OBJECT_STORE_UNAVAILABLE) from None
    return True


async def converge_work_pool(client: Any, name: str) -> bool:
    """Create the process work pool when it is absent, and refuse another type.

    Read first. Creating unconditionally and treating a conflict as success would
    accept a pool of some other type that happens to carry this name, and the
    worker would then fail to join it long after bootstrap reported success.
    """
    try:
        existing = await client.read_work_pool(name)
    except ObjectNotFound:
        existing = None
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise BootstrapError(WORK_POOL_UNAVAILABLE) from None
    if existing is not None:
        if existing.type != PROCESS_POOL_TYPE:
            raise BootstrapError(WORK_POOL_CONFLICT)
        return False
    try:
        await client.create_work_pool(
            WorkPoolCreate(
                name=name,
                type=PROCESS_POOL_TYPE,
                base_job_template=ProcessWorker.get_default_base_job_template(),
            )
        )
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise BootstrapError(WORK_POOL_UNAVAILABLE) from None
    return True


def _declared_name(content: Mapping[str, Any]) -> str | None:
    """Return the configuration name declared content carries, or None when it carries none."""
    try:
        return parse_configuration_package(dict(content)).configuration.name
    except ConfigurationPackageParseError:
        return None


def converge_configuration(projection: ProductProjection, package: ConfigurationPackage) -> tuple[str, int, bool]:
    """Register the bundled configuration once, and afterwards find what it registered.

    Identity is the declared name plus the package checksum, because that is what
    a repeated bootstrap has to recognise: the registry allocates the identifier,
    so nothing in the bundle can name the configuration it created last time.

    Two states are refused rather than resolved. A registration under this name
    whose content differs is a configuration an operator changed without changing
    its name, and registering a second one would leave two live candidates. More
    than one registration under this name is a registry this job did not create
    and must not add to.
    """
    checksum = package.checksum()
    matches: list[tuple[str, int, bool]] = []
    for summary in projection.list_configurations():
        versions = projection.list_configuration_versions(summary.config_id)
        named = [
            version for version in versions if _declared_name(version.declared_content) == package.configuration.name
        ]
        if not named:
            continue
        exact = [version for version in named if version.package_checksum == checksum]
        if not exact:
            raise BootstrapError(CONFIGURATION_CONFLICT)
        matches.append((summary.config_id, exact[-1].registry_version, False))
    if len(matches) > 1:
        raise BootstrapError(CONFIGURATION_CONFLICT)
    if matches:
        return matches[0]
    try:
        registered = configs.register(package=package.declared_content(), projection=projection)
    except configs.ConfigsError:
        raise BootstrapError(CONFIGURATION_INVALID) from None
    _record_registration(projection)
    return registered.version.config_id, registered.version.registry_version, True


def _record_registration(projection: ProductProjection) -> None:
    """Write the one audit event a first registration leaves behind."""
    projection.record_audit(
        AuditEvent(
            event_id=f"a-{uuid4().hex}",
            run_id=None,
            actor=BOOTSTRAP_ACTOR,
            operation="configs.register",
            reason=BOOTSTRAP_REASON,
            outcome=BOOTSTRAP_OUTCOME,
            created_at=datetime.now(timezone.utc),
        ),
        secrets=(),
    )


def _required(name: str) -> str:
    """Return one required setting's value, refusing absence without echoing it."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        logger.error("bootstrap setting %s is missing", name)
        raise BootstrapError(SETTING_MISSING)
    return value


def _bundled_package() -> ConfigurationPackage:
    """Read the one declared package this deployment registers."""
    path = _required(CONFIGURATION_PATH_ENV)
    try:
        return parse_configuration_package(configs.load_package_content(path))
    except (configs.ConfigsError, ConfigurationPackageParseError):
        raise BootstrapError(CONFIGURATION_INVALID) from None


async def _converge_pool(name: str) -> bool:
    """Own one Prefect client for the one step that needs one."""
    async with get_client() as client:
        return await converge_work_pool(client, name)


def _converge() -> None:
    """Run every convergence step in the order a deployment depends on.

    Synchronous on purpose. Two of these steps own an event loop of their own --
    the pool read-then-create, and the installed deployment's own entry point --
    and a loop already running here would leave the second unable to start one.
    """
    package = _bundled_package()
    try:
        client = boto3.client(
            "s3",
            endpoint_url=os.environ.get(S3_ENDPOINT_ENV) or None,
            region_name=os.environ.get(S3_REGION_ENV) or None,
        )
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise BootstrapError(OBJECT_STORE_UNAVAILABLE) from None
    created_bucket = converge_bucket(client, _required(S3_BUCKET_ENV))

    created_pool = asyncio.run(_converge_pool(_required(WORK_POOL_ENV)))

    if apply_deployment() != 0:
        raise BootstrapError(DEPLOYMENT_FAILED)

    try:
        projection = service_product_projection()
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise BootstrapError(PRODUCT_STORE_UNAVAILABLE) from None
    try:
        config_id, registry_version, created_configuration = converge_configuration(projection, package)
    except ProductStoreProviderError:
        raise BootstrapError(PRODUCT_STORE_UNAVAILABLE) from None

    logger.info("bucket %s", "created" if created_bucket else "present")
    logger.info("work pool %s", "created" if created_pool else "present")
    logger.info("deployment applied")
    logger.info(
        "configuration %s version %s %s",
        config_id,
        registry_version,
        "registered" if created_configuration else "present",
    )


def main() -> int:
    """Converge this deployment's durable objects, reporting only a family on failure."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | infrahub-sync bootstrap | %(message)s")
    try:
        _converge()
    except BootstrapError as error:
        logger.error("refused: %s", error.family)  # noqa: TRY400 - the family is the whole report.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
