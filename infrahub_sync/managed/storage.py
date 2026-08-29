"""Managed-only adapters for PostgreSQL records and S3-compatible artifacts."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from ipaddress import AddressValueError, IPv6Address
from typing import TYPE_CHECKING, Any

import boto3
import psycopg
from botocore.exceptions import ClientError

from infrahub_sync.product_store import (
    DuplicateArtifactError,
    ProductProjection,
    ProductStoreProviderError,
    production_product_projection,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_CONDITIONAL_CONFLICT_ATTEMPTS = 3
DATABASE_URL_ENV = "INFRAHUB_SYNC_DATABASE_URL"
S3_BUCKET_ENV = "INFRAHUB_SYNC_S3_BUCKET"
S3_PREFIX_ENV = "INFRAHUB_SYNC_S3_PREFIX"
S3_ENDPOINT_ENV = "INFRAHUB_SYNC_S3_ENDPOINT_URL"
S3_REGION_ENV = "INFRAHUB_SYNC_S3_REGION"
_PERCENT_ENCODED = r"%[0-9A-Fa-f]{2}"
_UNRESERVED = r"[A-Za-z0-9._~-]"
_SUB_DELIMS = r"[!$&'()*+,;=]"
_REG_NAME = rf"(?:{_UNRESERVED}|{_SUB_DELIMS}|{_PERCENT_ENCODED})+"
_IPV_FUTURE = rf"[Vv][0-9A-Fa-f]+\.(?:{_UNRESERVED}|{_SUB_DELIMS}|:)+"
_HOST = rf"(?:\[(?P<ipvfuture>{_IPV_FUTURE})\]|\[(?P<ipv6>[0-9A-Fa-f:.]+)\]|(?P<reg_name>{_REG_NAME}))"
_PCHAR = rf"(?:{_UNRESERVED}|{_SUB_DELIMS}|{_PERCENT_ENCODED}|[:@])"
_ENDPOINT_URI = re.compile(
    rf"(?P<scheme>(?i:http|https))://{_HOST}(?::(?P<port>[0-9]+))?"
    rf"(?:/{_PCHAR}*)*(?:\?(?:{_PCHAR}|[/?])*)?(?:#(?:{_PCHAR}|[/?])*)?"
)


class ManagedStorageStartupError(RuntimeError):
    """Managed durable storage could not initialize at process construction."""

    def __init__(self) -> None:
        super().__init__("managed durable storage startup failed")


class S3ProtocolError(RuntimeError):
    """An S3 SDK response does not satisfy the product-store byte contract."""

    def __init__(self) -> None:
        super().__init__("S3 get response body must return bytes")


@dataclass(frozen=True)
class _EndpointUri:
    """One exact raw endpoint that passed the complete accepted URI construction."""

    value: str


class PsycopgConnectionFactory:
    """Convert exact Psycopg connection failures to the driver-neutral provider error."""

    def __init__(self, connector: Callable[[str], Any] = psycopg.connect) -> None:
        self._connector = connector

    def __call__(self, database_url: str) -> Any:
        try:
            return _PsycopgConnection(self._connector(database_url))
        except psycopg.Error as error:
            raise _provider_error(error) from None


def _provider_error(error: psycopg.Error) -> ProductStoreProviderError:
    """Discard driver details while retaining the documented SQLSTATE discriminator."""
    sqlstate = error.sqlstate if isinstance(error.sqlstate, str) else None
    return ProductStoreProviderError(sqlstate=sqlstate)


class _PsycopgCursor:
    """DB-API cursor boundary that marks only Psycopg failures."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def execute(self, statement: str, parameters: Any = None) -> Any:
        try:
            if parameters is None:
                return self._cursor.execute(statement)
            return self._cursor.execute(statement, parameters)
        except psycopg.Error as error:
            raise _provider_error(error) from None

    def fetchone(self) -> Any:
        try:
            return self._cursor.fetchone()
        except psycopg.Error as error:
            raise _provider_error(error) from None

    def fetchall(self) -> Any:
        try:
            return self._cursor.fetchall()
        except psycopg.Error as error:
            raise _provider_error(error) from None

    @property
    def rowcount(self) -> int:
        try:
            return self._cursor.rowcount
        except psycopg.Error as error:
            raise _provider_error(error) from None

    def close(self) -> None:
        try:
            self._cursor.close()
        except psycopg.Error as error:
            raise _provider_error(error) from None


class _PsycopgConnection:
    """DB-API connection boundary that marks only Psycopg failures."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def cursor(self) -> _PsycopgCursor:
        try:
            return _PsycopgCursor(self._connection.cursor())
        except psycopg.Error as error:
            raise _provider_error(error) from None

    def commit(self) -> None:
        try:
            self._connection.commit()
        except psycopg.Error as error:
            raise _provider_error(error) from None

    def rollback(self) -> None:
        try:
            self._connection.rollback()
        except psycopg.Error as error:
            raise _provider_error(error) from None

    def close(self) -> None:
        try:
            self._connection.close()
        except psycopg.Error as error:
            raise _provider_error(error) from None


def _required_setting(values: Mapping[str, object], name: str) -> str:
    """Return a non-empty string setting without reflecting its value."""
    value = values.get(name)
    if type(value) is not str or not value.strip():  # pylint: disable=unidiomatic-typecheck  # Exact environment boundary.
        msg = f"{name} must be set"
        raise ValueError(msg)
    return value


def _optional_setting(values: Mapping[str, object], name: str) -> str | None:
    """Return one optional non-empty setting without accepting hostile values."""
    value = values.get(name)
    if value is None:
        return None
    if type(value) is not str or not value.strip():  # pylint: disable=unidiomatic-typecheck  # Exact environment boundary.
        msg = f"{name} must be a non-empty string when set"
        raise ValueError(msg)
    return value


def _valid_port(port: str | None) -> bool:
    """Check a decimal port's range without an unbounded integer conversion."""
    if port is None:
        return True
    significant_digits = port.lstrip("0") or "0"
    return len(significant_digits) < 5 or (len(significant_digits) == 5 and significant_digits <= "65535")


def _parse_endpoint(value: str) -> _EndpointUri | None:
    """Construct one accepted endpoint from the complete raw RFC 3986 HTTP URI grammar."""
    match = _ENDPOINT_URI.fullmatch(value)
    if match is None or not _valid_port(match["port"]):
        return None
    ipv6 = match["ipv6"]
    if ipv6 is not None:
        try:
            IPv6Address(ipv6)
        except AddressValueError:
            return None
    return _EndpointUri(value)


def _endpoint(values: Mapping[str, object]) -> str | None:
    """Validate the optional S3-compatible endpoint as an absolute HTTP URL."""
    endpoint = _optional_setting(values, S3_ENDPOINT_ENV)
    if endpoint is None:
        return None
    parsed = _parse_endpoint(endpoint)
    if parsed is None:
        msg = f"{S3_ENDPOINT_ENV} must be an absolute http or https URL"
        raise ValueError(msg) from None
    return parsed.value


def managed_product_projection(
    *,
    environ: Mapping[str, object] | None = None,
    database_connect: Callable[[], Any] | None = None,
    s3_client_builder: Callable[..., Any] = boto3.client,
    projection_builder: Callable[..., ProductProjection] = production_product_projection,
) -> ProductProjection:
    """Build the one PostgreSQL/S3 projection used by a managed process."""
    values: Mapping[str, object] = os.environ if environ is None else environ
    database_url = _required_setting(values, DATABASE_URL_ENV)
    bucket = _required_setting(values, S3_BUCKET_ENV)
    prefix = (_optional_setting(values, S3_PREFIX_ENV) or "infrahub-sync").strip("/")
    endpoint = _endpoint(values)
    region = _optional_setting(values, S3_REGION_ENV)
    connect = database_connect or (lambda: PsycopgConnectionFactory()(database_url))
    client = Boto3S3Client(s3_client_builder("s3", endpoint_url=endpoint, region_name=region))
    try:
        return projection_builder(connect=connect, s3_client=client, bucket=bucket, prefix=prefix)
    except ProductStoreProviderError:
        raise ManagedStorageStartupError from None


def _error_code(error: ClientError) -> str | None:
    """Return the SDK service code without reading an error message."""
    value = error.response.get("Error", {}).get("Code")
    return value if isinstance(value, str) else None


class Boto3S3Client:
    """Adapt Boto3's low-level client to the product-store S3 protocol."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def put(self, *, bucket: str, key: str, data: bytes, if_absent: bool = False) -> None:
        """Write one byte object, preserving the create-only remote precondition."""
        arguments: dict[str, object] = {"Bucket": bucket, "Key": key, "Body": data}
        if not if_absent:
            self._client.put_object(**arguments)
            return
        arguments["IfNoneMatch"] = "*"
        for attempt in range(_CONDITIONAL_CONFLICT_ATTEMPTS):
            try:
                self._client.put_object(**arguments)
            except ClientError as error:
                code = _error_code(error)
                if code == "PreconditionFailed":
                    msg = "S3 object already exists"
                    raise DuplicateArtifactError(msg) from None
                if code == "ConditionalRequestConflict" and attempt + 1 < _CONDITIONAL_CONFLICT_ATTEMPTS:
                    continue
                raise
            return

    def get(self, *, bucket: str, key: str) -> bytes | None:
        """Return exact object bytes, mapping only the exact missing-object response."""
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if _error_code(error) == "NoSuchKey":
                return None
            raise
        try:
            result = response["Body"].read()
        except (AttributeError, KeyError, TypeError):
            raise S3ProtocolError from None
        if type(result) is not bytes:  # pylint: disable=unidiomatic-typecheck  # Exact protocol contract.
            raise S3ProtocolError
        return result

    def copy(self, *, bucket: str, source: str, destination: str) -> None:
        """Copy exactly one object within the configured bucket."""
        self._client.copy_object(Bucket=bucket, Key=destination, CopySource={"Bucket": bucket, "Key": source})

    def delete(self, *, bucket: str, key: str) -> None:
        """Delete exactly one object key."""
        self._client.delete_object(Bucket=bucket, Key=key)
