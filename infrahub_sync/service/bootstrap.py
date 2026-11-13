"""Converge the durable objects one deployment needs, and change nothing else.

This is the one-off job a Compose deployment runs before its API and its worker
start: the product schema, the artifact bucket, the Prefect process work pool,
and the installed deployment. Everything here is safe to repeat — a second run
observes what the first created and writes nothing.

The configuration registry is not one of those objects. A deployment converges
infrastructure and starts empty; which package it runs is a decision an operator
makes through the API, and one nothing here may make for them.

Two further boundaries are deliberate. Nothing in the long-running services does
any of this: an API or a worker that converged bootstrap state on startup would
make every restart a write, and would give two processes racing claims on the
same objects. And every failure leaves through one fixed family name, because
this job's inputs are credentials and endpoints and its provider errors carry
them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import boto3
from botocore.exceptions import BotoCoreError, ClientError, InvalidRegionError
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.exceptions import ObjectNotFound
from prefect.workers.process import ProcessWorker

from infrahub_sync.product_store import DuplicateArtifactError

from .deploy import WORK_POOL_ENV
from .deploy import main as apply_deployment
from .storage import (
    S3_BUCKET_ENV,
    S3_ENDPOINT_ENV,
    S3_REGION_ENV,
    Boto3S3Client,
    EndpointSettingError,
    s3_client_arguments,
    s3_key_prefix,
    service_product_projection,
)
from .trust_store import TrustStoreError, install_custom_ca_bundle

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# The pool type this deployment's worker joins. Prefect's own process worker
# supplies the template, so a pool created here is the one `prefect work-pool
# create --type process` would have made.
PROCESS_POOL_TYPE = "process"

# The exact object-store responses that mean the bucket is already there. Any
# other client error is a real failure, and is reported as one.
_BUCKET_PRESENT = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})
# The exact answers to `HeadBucket` that mean the bucket is absent.
_BUCKET_ABSENT_CODES = frozenset({"NoSuchBucket", "NotFound"})
_HTTP_NOT_FOUND = 404
_HTTP_FORBIDDEN = 403
_HTTP_NOT_IMPLEMENTED = 501
# The region in which the S3 API takes no location constraint.
_DEFAULT_REGION = "us-east-1"
# The bundled store's service name in the Compose file.
BUNDLED_STORE_HOST = "object-store"
# Where the create-only check writes, under the deployment's key prefix.
CREATE_ONLY_CHECK_PATH = ".infrahub-sync/bootstrap-check"


# Every way this job can fail, named. The family is the whole public message:
# each input it reads is an endpoint or a credential, and the providers
# underneath render both into their own exception text.
OBJECT_STORE_UNAVAILABLE = "object-store-unavailable"
WORK_POOL_UNAVAILABLE = "work-pool-unavailable"
WORK_POOL_CONFLICT = "work-pool-conflict"
DEPLOYMENT_FAILED = "deployment-failed"
PRODUCT_STORE_UNAVAILABLE = "product-store-unavailable"
SETTING_MISSING = "required-setting-missing"
OBJECT_STORE_ENDPOINT_INVALID = "object-store-endpoint-invalid"
BUNDLED_OBJECT_STORE_NOT_LEFT_OUT = "bundled-object-store-not-left-out"
OBJECT_STORE_ENDPOINT_NOT_SET = "object-store-endpoint-not-set"
OBJECT_STORE_ACCESS_REFUSED = "object-store-access-refused"
OBJECT_STORE_UNSUPPORTED = "object-store-unsupported"
OBJECT_STORE_SETTING_INVALID = "object-store-setting-invalid"


class BootstrapError(RuntimeError):
    """One fixed, secret-safe bootstrap failure family.

    The message is the family name and nothing else. Every input this job reads
    is an endpoint or a credential, and the providers underneath render both into
    their exception text, so no provider detail crosses this boundary.
    """

    def __init__(self, family: str) -> None:
        super().__init__(family)
        self.family = family


def _resolves(host: str) -> bool:
    """Report whether a host name resolves on this container's networks.

    The name is looked up as an absolute name, with a trailing dot, so the resolver does not
    try the search domains the container inherits from the Docker host: a site record such as
    `object-store.<search domain>` must not read as the bundled store running.
    """
    try:
        socket.getaddrinfo(f"{host.rstrip('.')}.", None)
    except OSError:
        return False
    return True


def check_store_settings(endpoint: str | None, *, resolves: Callable[[str], bool] = _resolves) -> None:
    """Refuse an object store endpoint that does not agree with the bundled store setting.

    The bundled store runs exactly when its service name resolves. It takes the deployment's
    S3 keys as its own root credential, so an external endpoint while it runs means it holds
    the external store's credential. A left-out bundled store with the endpoint still naming
    it means no store answers. Both are refused before any S3 request, by name only.
    """
    host = urlsplit(endpoint).hostname if endpoint else None
    bundled_runs = resolves(BUNDLED_STORE_HOST)
    if host == BUNDLED_STORE_HOST and not bundled_runs:
        raise BootstrapError(OBJECT_STORE_ENDPOINT_NOT_SET)
    if host != BUNDLED_STORE_HOST and bundled_runs:
        raise BootstrapError(BUNDLED_OBJECT_STORE_NOT_LEFT_OUT)


def _status(error: ClientError) -> int | None:
    status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status if isinstance(status, int) else None


def _refusal(error: ClientError) -> BootstrapError:
    """Name the failure family of one store answer: a refused credential, a missing feature, or neither."""
    status = _status(error)
    if status == _HTTP_FORBIDDEN:
        return BootstrapError(OBJECT_STORE_ACCESS_REFUSED)
    if status == _HTTP_NOT_IMPLEMENTED or error.response.get("Error", {}).get("Code") == "NotImplemented":
        return BootstrapError(OBJECT_STORE_UNSUPPORTED)
    return BootstrapError(OBJECT_STORE_UNAVAILABLE)


def converge_bucket(client: Any, bucket: str, region: str | None = None) -> bool:
    """Create the artifact bucket only when the store reports it absent. Return whether this call made it.

    Checking first lets a credential that may read and write objects but not create buckets
    complete against a bucket the team already created.
    """
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if _status(error) != _HTTP_NOT_FOUND and code not in _BUCKET_ABSENT_CODES:
            raise _refusal(error) from None
    except BotoCoreError:
        raise BootstrapError(OBJECT_STORE_UNAVAILABLE) from None
    else:
        return False
    settings: dict[str, Any] = {}
    if region and region != _DEFAULT_REGION:
        settings["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        client.create_bucket(Bucket=bucket, **settings)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in _BUCKET_PRESENT:
            return False
        raise _refusal(error) from None
    except BotoCoreError:
        raise BootstrapError(OBJECT_STORE_UNAVAILABLE) from None
    return True


def _write_if_absent(objects: Any, bucket: str, key: str) -> bool:
    """Write one empty object create-only. Return False when the store refuses it as present."""
    try:
        objects.put(bucket=bucket, key=key, data=b"", if_absent=True)
    except DuplicateArtifactError:
        return False
    except ClientError as error:
        raise _refusal(error) from None
    except BotoCoreError:
        raise BootstrapError(OBJECT_STORE_UNAVAILABLE) from None
    return True


def check_create_only(objects: Any, bucket: str, prefix: str) -> None:
    """Check that the store accepts a first create-only write of one key, refuses a second, and leave no object.

    Sync's artifact store writes some objects only when the key is absent, and relies on the
    store refusing the second write. A store that accepts it would let a write replace a
    stored artifact. The key is new, so a store that refuses the first write refuses every
    create-only write, and no artifact could be stored.

    Sync also deletes objects it no longer needs, so a store that refuses to delete the check
    key stops bootstrap too, unless an earlier refusal already did.
    """
    path = f"{CREATE_ONLY_CHECK_PATH}/{uuid.uuid4()}"
    key = f"{prefix}/{path}" if prefix else path
    try:
        unsupported = not _write_if_absent(objects, bucket, key) or _write_if_absent(objects, bucket, key)
    except BootstrapError:
        _delete_check_key(objects, bucket, key)
        raise
    refusal = _delete_check_key(objects, bucket, key)
    if unsupported:
        raise BootstrapError(OBJECT_STORE_UNSUPPORTED)
    if refusal is not None:
        raise refusal


def _delete_check_key(objects: Any, bucket: str, key: str) -> BootstrapError | None:
    """Delete the check key, and return the refusal when the store does not delete it."""
    try:
        objects.delete(bucket=bucket, key=key)
    except ClientError as error:
        logger.error("the create-only check could not delete its key under %s", CREATE_ONLY_CHECK_PATH)  # noqa: TRY400 - a fixed message; the family follows.
        return _refusal(error)
    except BotoCoreError:
        logger.error("the create-only check could not delete its key under %s", CREATE_ONLY_CHECK_PATH)  # noqa: TRY400 - a fixed message; the family follows.
        return BootstrapError(OBJECT_STORE_UNAVAILABLE)
    return None


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


def _required(name: str) -> str:
    """Return one required setting's value, refusing absence without echoing it."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        logger.error("bootstrap setting %s is missing", name)
        raise BootstrapError(SETTING_MISSING)
    return value


async def _converge_pool(name: str) -> bool:
    """Own one Prefect client for the one step that needs one."""
    async with get_client() as client:
        return await converge_work_pool(client, name)


def _s3_client(arguments: dict[str, Any]) -> Any:
    """Build the S3 client from checked arguments, naming only the setting botocore refuses.

    botocore's own refusals quote the value they refuse ("Invalid endpoint: <url>"), so
    none of their text is logged: only a fixed line and the family.
    """
    try:
        return boto3.client("s3", **arguments)
    except InvalidRegionError:
        logger.error("the S3 client refused %s", S3_REGION_ENV)  # noqa: TRY400 - botocore's text quotes the value.
        raise BootstrapError(OBJECT_STORE_SETTING_INVALID) from None
    except BotoCoreError as error:
        # Another client setting, such as a partial key pair or an absent AWS_PROFILE: the
        # class names the cause, and its text, which may quote a value, is not logged.
        logger.error("the S3 client refused its settings (%s)", type(error).__name__)  # noqa: TRY400 - see above.
        raise BootstrapError(OBJECT_STORE_SETTING_INVALID) from None
    except ValueError:
        logger.error("the S3 client refused %s", S3_ENDPOINT_ENV)  # noqa: TRY400 - botocore's text quotes the value.
        raise BootstrapError(OBJECT_STORE_ENDPOINT_INVALID) from None
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise BootstrapError(OBJECT_STORE_UNAVAILABLE) from None


def _converge() -> None:
    """Run every convergence step in the order a deployment depends on.

    Synchronous on purpose. Two of these steps own an event loop of their own --
    the pool read-then-create, and the installed deployment's own entry point --
    and a loop already running here would leave the second unable to start one.
    """
    try:
        arguments = s3_client_arguments(os.environ)
        prefix = s3_key_prefix(os.environ)
    except EndpointSettingError as error:
        # The message names the setting and the rule, never the value (`storage._endpoint`).
        logger.error("%s", error)  # noqa: TRY400 - a fixed message; the family follows.
        raise BootstrapError(OBJECT_STORE_ENDPOINT_INVALID) from None
    except ValueError as error:
        # Storage's own checks: the message names the setting, never its value.
        logger.error("%s", error)  # noqa: TRY400 - a fixed message; the family follows.
        raise BootstrapError(OBJECT_STORE_SETTING_INVALID) from None
    client = _s3_client(arguments)
    check_store_settings(arguments["endpoint_url"])
    bucket = _required(S3_BUCKET_ENV)
    created_bucket = converge_bucket(client, bucket, arguments["region_name"])
    check_create_only(Boto3S3Client(client), bucket, prefix)

    try:
        created_pool = asyncio.run(_converge_pool(_required(WORK_POOL_ENV)))
    except BootstrapError:
        raise
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise BootstrapError(WORK_POOL_UNAVAILABLE) from None

    try:
        deployment_result = apply_deployment()
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise BootstrapError(DEPLOYMENT_FAILED) from None
    if deployment_result != 0:
        raise BootstrapError(DEPLOYMENT_FAILED)

    # Building the projection is what converges the product schema, so this call
    # is the schema step even though nothing here reads a record afterwards.
    try:
        service_product_projection()
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise BootstrapError(PRODUCT_STORE_UNAVAILABLE) from None

    logger.info("bucket %s", "created" if created_bucket else "present")
    logger.info("object store refuses a second create-only write")
    logger.info("work pool %s", "created" if created_pool else "present")
    logger.info("deployment applied")
    logger.info("product schema converged; the configuration registry is the operator's to fill")


def _configure_logging() -> None:
    """Report this job's progress lines at INFO.

    Importing Prefect configures the root logger before `main` runs, so `basicConfig` does
    nothing, and this logger inherits a level that drops every INFO line: the job would
    report only its refusals. Its own level is set instead; the root handlers print it.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | infrahub-sync bootstrap | %(message)s")
    logger.setLevel(logging.INFO)


def main() -> int:
    """Converge this deployment's durable objects, reporting only a family on failure."""
    _configure_logging()
    try:
        install_custom_ca_bundle()
    except TrustStoreError as error:
        logger.error("%s", error)  # noqa: TRY400 - the message names a file, never its content.
        return 1
    try:
        _converge()
    except BootstrapError as error:
        logger.error("refused: %s", error.family)  # noqa: TRY400 - the family is the whole report.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
