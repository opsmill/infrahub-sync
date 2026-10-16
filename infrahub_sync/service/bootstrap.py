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
them. A refused object store setting adds the setting's name, never its value.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import sys
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from botocore.exceptions import BotoCoreError, ClientError
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
    S3_PREFIX_ENV,
    S3_REGION_ENV,
    Boto3S3Client,
    build_s3_client,
    service_product_projection,
)
from .trust_store import CustomCertificateError, install_custom_ca_bundle

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

# The pool type this deployment's worker joins. Prefect's own process worker
# supplies the template, so a pool created here is the one `prefect work-pool
# create --type process` would have made.
PROCESS_POOL_TYPE = "process"

# The exact object-store responses that mean the bucket is already there. Any
# other client error is a real failure, and is reported as one.
_BUCKET_PRESENT = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})

# The S3 API's default region takes no location constraint; any other region needs one.
_DEFAULT_REGION = "us-east-1"

# The Compose service name of the bundled object store. An endpoint that names
# another host while this name resolves means the bundled store was not left out.
BUNDLED_OBJECT_STORE_HOST = "object-store"

# Where the create-only check writes, under the deployment's own key prefix.
_CHECK_PATH = ".infrahub-sync/bootstrap-check"


# Every way this job can fail, named. The family is the whole public message:
# each input it reads is an endpoint or a credential, and the providers
# underneath render both into their own exception text. The one exception is a
# setting the shared S3 client builder refuses: its text names the setting and
# never its value, so it is printed after the family.
OBJECT_STORE_UNAVAILABLE = "object-store-unavailable"
OBJECT_STORE_ACCESS_REFUSED = "object-store-access-refused"
OBJECT_STORE_UNSUPPORTED = "object-store-unsupported"
OBJECT_STORE_SETTING_INVALID = "object-store-setting-invalid"
BUNDLED_OBJECT_STORE_NOT_LEFT_OUT = "bundled-object-store-not-left-out"
WORK_POOL_UNAVAILABLE = "work-pool-unavailable"
WORK_POOL_CONFLICT = "work-pool-conflict"
DEPLOYMENT_FAILED = "deployment-failed"
PRODUCT_STORE_UNAVAILABLE = "product-store-unavailable"
SETTING_MISSING = "required-setting-missing"


class BootstrapError(RuntimeError):
    """One fixed, secret-safe bootstrap failure family.

    The message is the family name and nothing else. Every input this job reads
    is an endpoint or a credential, and the providers underneath render both into
    their exception text, so no provider detail crosses this boundary. `setting`
    is the shared builder's own text for a refused setting, which names the
    setting only.
    """

    def __init__(self, family: str, setting: str | None = None) -> None:
        super().__init__(family)
        self.family = family
        self.setting = setting

    @property
    def report(self) -> str:
        """The printed refusal: the family, then the refused setting when there is one."""
        return self.family if self.setting is None else f"{self.family}: {self.setting}"


def _status(error: ClientError) -> int | None:
    """Return the response status of one SDK error, reading no message text."""
    value = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return value if isinstance(value, int) else None


def _object_store_family(error: ClientError | BotoCoreError) -> str:
    """Name one SDK failure from its status and code, reading no message text.

    403 is a credential the store refuses; 501 or `NotImplemented` is a request the
    store does not carry out, such as a conditional write. Anything else, a missing
    status included, is the store being unavailable to this job.
    """
    if isinstance(error, ClientError):
        status = _status(error)
        if status == 403:
            return OBJECT_STORE_ACCESS_REFUSED
        if status == 501 or error.response.get("Error", {}).get("Code") == "NotImplemented":
            return OBJECT_STORE_UNSUPPORTED
    return OBJECT_STORE_UNAVAILABLE


def refuse_unused_bundled_object_store(endpoint: str | None) -> None:
    """Stop when the endpoint names another store while the bundled one is running.

    Compose cannot tie the endpoint setting to leaving out the bundled store. An
    external endpoint with the bundled store still running would leave an unused
    MinIO holding the external store's credential as its root credential.
    """
    try:
        host = urlsplit(endpoint).hostname if endpoint else None
    except ValueError:
        host = None
    if host == BUNDLED_OBJECT_STORE_HOST:
        return
    try:
        socket.getaddrinfo(BUNDLED_OBJECT_STORE_HOST, None)
    except OSError:
        return
    raise BootstrapError(BUNDLED_OBJECT_STORE_NOT_LEFT_OUT)


def converge_bucket(client: Any, bucket: str, region: str | None = None) -> bool:
    """Create the artifact bucket only when the store reports it absent.

    Check first, so a credential that may use the bucket but not create one still
    bootstraps against a bucket the operator made. Return whether this call made it.
    """
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if _status(error) != 404:
            raise BootstrapError(_object_store_family(error)) from None
    except BotoCoreError as error:
        raise BootstrapError(_object_store_family(error)) from None
    else:
        return False

    request: dict[str, Any] = {"Bucket": bucket}
    if region and region != _DEFAULT_REGION:
        request["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        client.create_bucket(**request)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in _BUCKET_PRESENT:
            return False
        raise BootstrapError(_object_store_family(error)) from None
    except BotoCoreError as error:
        raise BootstrapError(_object_store_family(error)) from None
    return True


@contextlib.contextmanager
def _object_store_failures() -> Iterator[None]:
    """Map every SDK failure inside the block to its object store family."""
    try:
        yield
    except (ClientError, BotoCoreError) as error:
        raise BootstrapError(_object_store_family(error)) from None


def check_create_only(store_client: Boto3S3Client, bucket: str, prefix: str) -> None:
    """Check that the store refuses a second create-only write of one key.

    A store that ignores the condition would let a second artifact write replace
    a stored one. The check key is deleted on every path; when a write already
    failed, that failure is the one reported, whatever the delete does.
    """
    key = "/".join(part for part in (prefix, _CHECK_PATH, str(uuid.uuid4())) if part)
    refusing = False
    failure: BootstrapError | None = None
    try:
        with _object_store_failures():
            store_client.put(bucket=bucket, key=key, data=b"", if_absent=True)
            try:
                store_client.put(bucket=bucket, key=key, data=b"", if_absent=True)
            except DuplicateArtifactError:
                refusing = True
    except BootstrapError as error:
        failure = error
    try:
        with _object_store_failures():
            store_client.delete(bucket=bucket, key=key)
    except BootstrapError:
        if failure is None:
            raise
    if failure is not None:
        raise failure
    if not refusing:
        raise BootstrapError(OBJECT_STORE_UNSUPPORTED)


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


def _converge() -> None:
    """Run every convergence step in the order a deployment depends on.

    Synchronous on purpose. Two of these steps own an event loop of their own --
    the pool read-then-create, and the installed deployment's own entry point --
    and a loop already running here would leave the second unable to start one.
    """
    try:
        client = build_s3_client(os.environ)
    except ValueError as error:
        # The shared builder's text for a refused setting names the setting, never its value.
        raise BootstrapError(OBJECT_STORE_SETTING_INVALID, str(error)) from None
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        raise BootstrapError(OBJECT_STORE_UNAVAILABLE) from None
    refuse_unused_bundled_object_store(os.environ.get(S3_ENDPOINT_ENV))
    bucket = _required(S3_BUCKET_ENV)
    created_bucket = converge_bucket(client, bucket, os.environ.get(S3_REGION_ENV) or None)
    prefix = (os.environ.get(S3_PREFIX_ENV) or "infrahub-sync").strip("/")
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


def main() -> int:
    """Converge this deployment's durable objects, reporting only a family on failure."""
    try:
        install_custom_ca_bundle()
    except CustomCertificateError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | infrahub-sync bootstrap | %(message)s")
    try:
        _converge()
    except BootstrapError as error:
        logger.error("refused: %s", error.report)  # noqa: TRY400 - a family and at most a setting name.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
