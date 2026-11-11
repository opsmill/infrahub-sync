"""Credential variables the worker holds, checked by `configs validate --branch` (spec 019 FR-035, T063).

`configs validate` runs in the Sync API, which does not hold the variables a run reads: Compose
gives the worker and the API different environments. So each worker publishes, on each poll,
the NAMES of its set and non-empty `INFRAHUB_SYNC_CREDENTIAL_*` variables to the product-store
table `worker_environments` (data-model.md "Worker environment record"), never a value.
Validation with a branch reports `credential-not-set-in-worker` for each referenced variable no
live worker holds, and the warning `worker-environment-unknown` when no live worker has
published. Compose passes an unset variable as an empty string, so an empty value counts as
unset.

The worker reaches the product store through `infrahub_sync.service.storage.service_product_projection`,
the service's own store factory; these tests replace it with a local SQLite projection (also on
`infrahub_sync.service.worker`, in case the worker imports the name). That seam is this test's
proposal: T069 does not name how the worker opens the store.

The SC-007 credentials (five characters, and one that holds `"`, `\\` and `,`) are set in the
worker's environment. Every table of the store, every validation response and every log record
is searched for the values and their JSON-escaped forms.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.configuration import schema_validation
from infrahub_sync.configuration.capabilities import BUILTIN_ADAPTER_CAPABILITIES
from infrahub_sync.configuration.credentials import ENV_CREDENTIAL_PREFIX
from infrahub_sync.product_store import ProductStoreProviderError, local_product_projection
from infrahub_sync.service import storage as service_storage
from infrahub_sync.service import worker as service_worker
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.service import RunService
from infrahub_sync.service.worker import ServiceProcessWorker
from tests.configuration.test_validate_permissions import _StandInClient
from tests.configuration.validation_packages import package_data
from tests.service.failure_cases import (
    SHORT_CREDENTIAL,
    SHORT_CREDENTIAL_NAME,
    SPECIAL_CREDENTIAL,
    SPECIAL_CREDENTIAL_NAME,
)
from tests.service.test_config_routes import _Orchestration

if TYPE_CHECKING:
    from pathlib import Path

    from prefect.client.schemas.objects import WorkPool

    from infrahub_sync.product_store.store import ProductProjection

_ADMIN_TOKEN = "worker-environment-admin-0005"  # noqa: S105 - deliberate non-secret canary.
_POOL_NAME = "service-pool"
_POOL_ID = UUID("e0679e8a-9460-4ca7-8bf1-70bf967eed2d")
_WORKER_NAME = "infrahub-sync-service-0b6f6c1e-3d2a-4c55-9a51-7e2f0d9a1c11"
_EMPTY_NAME = "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"
_DATABASE_PASSWORD = "db-Pw-9137"  # noqa: S105 - deliberate non-secret canary.
_DATABASE_URL = f"postgresql://sync:{_DATABASE_PASSWORD}@db/sync"
# The value forms that must appear nowhere: each value, and the escaped forms JSON gives the special one.
_VALUE_FORMS = (
    SHORT_CREDENTIAL,
    SPECIAL_CREDENTIAL,
    json.dumps(SPECIAL_CREDENTIAL)[1:-1],
    SPECIAL_CREDENTIAL.replace("\\", "\\\\"),
    _DATABASE_PASSWORD,
)


def _set_worker_environment(monkeypatch: pytest.MonkeyPatch, variables: dict[str, str]) -> None:
    """Give this process exactly `variables` in the credential namespace, plus a database setting."""
    for name in list(os.environ):
        if name.startswith(ENV_CREDENTIAL_PREFIX):
            monkeypatch.delenv(name)
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("INFRAHUB_SYNC_DATABASE_URL", _DATABASE_URL)


# Two set credentials, one empty one, and a database setting outside the credential namespace.
_WORKER_VARIABLES = {
    SHORT_CREDENTIAL_NAME: SHORT_CREDENTIAL,
    SPECIAL_CREDENTIAL_NAME: SPECIAL_CREDENTIAL,
    _EMPTY_NAME: "",
}


def _store_reached_by_the_worker(monkeypatch: pytest.MonkeyPatch, projection: ProductProjection) -> None:
    def factory(*_args: object, **_kwargs: object) -> ProductProjection:
        return projection

    monkeypatch.setattr(service_storage, "service_product_projection", factory)
    monkeypatch.setattr(service_worker, "service_product_projection", factory, raising=False)


def _worker() -> ServiceProcessWorker:
    worker = ServiceProcessWorker(work_pool_name=_POOL_NAME, name=_WORKER_NAME)
    worker._work_pool = cast(
        "WorkPool",
        SimpleNamespace(id=_POOL_ID, name=_POOL_NAME, base_job_template=worker.get_default_base_job_template()),
    )
    return worker


async def _poll(worker: ServiceProcessWorker) -> None:
    # No worker identity is resolved, so the poll submits nothing; it still publishes.
    assert await worker.get_and_submit_flow_runs() == []


def _tables(tmp_path: Path) -> dict[str, str]:
    """Every table of the local store, each as one text of all its rows."""
    with sqlite3.connect(tmp_path / "product-records.sqlite3") as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {
            name: " ".join(
                " ".join(str(value) for value in row)
                # The table names come from the store's own catalog, not from input.
                for row in connection.execute(f'SELECT * FROM "{name}"')  # noqa: S608
            )
            for name in names
        }


def _validation_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, projection: ProductProjection, *, bundled_reads: bool = False
) -> TestClient:
    """An administrator client of the Sync API on `projection`.

    The destination schema is fixed and empty, unless `bundled_reads` keeps the bundled schema
    and access reads, which then reach whatever `infrahub_sdk.InfrahubClientSync` returns.
    """
    if not bundled_reads:
        table = dict(BUILTIN_ADAPTER_CAPABILITIES)
        table["infrahub"] = replace(
            table["infrahub"],
            destination_schema_accessor=lambda _package, _branch: {},
            destination_access_accessor=None,
        )
        monkeypatch.setattr(schema_validation, "BUILTIN_ADAPTER_CAPABILITIES", table)
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": _ADMIN_TOKEN, "administrator": True}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    del tmp_path
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration(), secrets=resolver.secret_values),
            resolver,
            ConfigurationRoutes(product_projection=projection, secrets=resolver.secret_values),
        )
    )
    client.headers.update({"Authorization": f"Bearer {_ADMIN_TOKEN}"})
    return client


def _referencing_package() -> dict[str, Any]:
    """A package whose source reads the short credential and whose destination reads the empty one."""
    data = package_data()
    data["credentials"]["netbox-token"]["identifier"] = SHORT_CREDENTIAL_NAME
    data["credentials"]["infrahub-token"]["identifier"] = _EMPTY_NAME
    return data


def _validate(client: TestClient, *, branch: str | None = "main", data: dict[str, Any] | None = None) -> dict[str, Any]:
    content = _referencing_package() if data is None else data
    registered = client.post(
        "/configs", headers={"Idempotency-Key": "worker-env"}, json={"package": content, "reason": "t"}
    )
    assert registered.status_code == 201, registered.text
    config_id = registered.json()["configuration"]["config_id"]
    params = {} if branch is None else {"branch": branch}
    response = client.post(f"/configs/{config_id}/versions/1/validate", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _worker_findings(body: dict[str, Any]) -> list[dict[str, Any]]:
    codes = {"credential-not-set-in-worker", "worker-environment-unknown"}
    return [finding for finding in body["findings"] if finding["code"] in codes]


def _assert_no_value(text: str) -> None:
    for form in _VALUE_FORMS:
        assert form not in text


async def test_a_poll_publishes_the_names_of_set_credential_variables_and_no_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    projection = local_product_projection(tmp_path)
    _store_reached_by_the_worker(monkeypatch, projection)
    _set_worker_environment(monkeypatch, _WORKER_VARIABLES)

    with caplog.at_level("DEBUG"):
        await _poll(_worker())

    tables = _tables(tmp_path)
    assert "worker_environments" in tables
    published = tables["worker_environments"]
    assert SHORT_CREDENTIAL_NAME in published
    assert SPECIAL_CREDENTIAL_NAME in published
    assert _EMPTY_NAME not in published
    assert "INFRAHUB_SYNC_DATABASE_URL" not in published
    _assert_no_value(" ".join(tables.values()))
    _assert_no_value(caplog.text)


async def test_each_poll_publishes_the_variables_the_worker_holds_then(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = local_product_projection(tmp_path)
    _store_reached_by_the_worker(monkeypatch, projection)
    worker = _worker()
    _set_worker_environment(monkeypatch, {SHORT_CREDENTIAL_NAME: SHORT_CREDENTIAL})
    await _poll(worker)
    _set_worker_environment(monkeypatch, {SPECIAL_CREDENTIAL_NAME: SPECIAL_CREDENTIAL})

    await _poll(worker)

    tables = _tables(tmp_path)
    assert "worker_environments" in tables
    assert SPECIAL_CREDENTIAL_NAME in tables["worker_environments"]
    assert SHORT_CREDENTIAL_NAME not in tables["worker_environments"]


async def test_a_store_that_cannot_be_reached_does_not_stop_the_poll(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Publishing is best effort: an unavailable store leaves the poll loop running."""

    def unavailable(*_args: object, **_kwargs: object) -> ProductProjection:
        raise ProductStoreProviderError(sqlstate="08006")

    monkeypatch.setattr(service_storage, "service_product_projection", unavailable)
    monkeypatch.setattr(service_worker, "service_product_projection", unavailable, raising=False)
    _set_worker_environment(monkeypatch, _WORKER_VARIABLES)
    worker = _worker()

    with caplog.at_level("DEBUG"):
        await _poll(worker)
        await _poll(worker)

    _assert_no_value(caplog.text)


async def test_validation_names_each_referenced_variable_no_live_worker_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The empty variable counts as unset; the set one gives no finding; no value is shown."""
    projection = local_product_projection(tmp_path)
    _store_reached_by_the_worker(monkeypatch, projection)
    _set_worker_environment(monkeypatch, _WORKER_VARIABLES)
    await _poll(_worker())
    client = _validation_client(tmp_path, monkeypatch, projection)

    body = _validate(client)

    assert [(finding["code"], finding["severity"]) for finding in _worker_findings(body)] == [
        ("credential-not-set-in-worker", "error")
    ]
    assert _EMPTY_NAME in _worker_findings(body)[0]["message"]
    _assert_no_value(json.dumps(body))


async def test_a_variable_set_only_in_the_api_process_still_counts_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation compares with what the worker published, never with its own environment."""
    projection = local_product_projection(tmp_path)
    _store_reached_by_the_worker(monkeypatch, projection)
    _set_worker_environment(monkeypatch, {_EMPTY_NAME: SPECIAL_CREDENTIAL})
    await _poll(_worker())
    # The API process holds the short credential and not the one the worker published.
    _set_worker_environment(monkeypatch, {SHORT_CREDENTIAL_NAME: SHORT_CREDENTIAL})
    client = _validation_client(tmp_path, monkeypatch, projection)

    body = _validate(client)

    found = _worker_findings(body)
    assert [finding["code"] for finding in found] == ["credential-not-set-in-worker"]
    assert SHORT_CREDENTIAL_NAME in found[0]["message"]
    assert _EMPTY_NAME not in found[0]["message"]
    _assert_no_value(json.dumps(body))


def test_no_published_worker_gives_a_warning_and_no_credential_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = local_product_projection(tmp_path)
    _set_worker_environment(monkeypatch, _WORKER_VARIABLES)
    client = _validation_client(tmp_path, monkeypatch, projection)

    body = _validate(client)

    assert [(finding["code"], finding["severity"]) for finding in _worker_findings(body)] == [
        ("worker-environment-unknown", "warning")
    ]


async def test_a_publication_from_a_worker_that_stopped_long_ago_is_not_a_live_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projection = local_product_projection(tmp_path)
    _store_reached_by_the_worker(monkeypatch, projection)
    _set_worker_environment(monkeypatch, _WORKER_VARIABLES)
    await _poll(_worker())
    database = tmp_path / "product-records.sqlite3"
    assert "worker_environments" in _tables(tmp_path)
    with sqlite3.connect(database) as connection:
        (observed,) = connection.execute("SELECT observed_at FROM worker_environments").fetchone()
        stale = datetime.fromisoformat(str(observed).replace("Z", "+00:00")) - timedelta(days=1)
        rendered = stale.isoformat().replace("+00:00", "Z") if str(observed).endswith("Z") else stale.isoformat()
        connection.execute("UPDATE worker_environments SET observed_at = ?", (rendered,))
    client = _validation_client(tmp_path, monkeypatch, projection)

    body = _validate(client)

    assert [finding["code"] for finding in _worker_findings(body)] == ["worker-environment-unknown"]


async def test_validation_without_a_branch_reports_no_worker_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a branch, validation stays as it is today."""
    projection = local_product_projection(tmp_path)
    _store_reached_by_the_worker(monkeypatch, projection)
    _set_worker_environment(monkeypatch, _WORKER_VARIABLES)
    await _poll(_worker())
    client = _validation_client(tmp_path, monkeypatch, projection)

    body = _validate(client, branch=None)

    assert _worker_findings(body) == []


async def test_one_validation_reports_a_missing_attribute_an_unset_credential_and_a_missing_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The US5 independent test, SC-009 and SC-011 on one configuration, through the bundled reads.

    The mapping maps `serial`, which the branch schema does not hold; the source credential is
    held by no live worker; and the import account cannot create `InfraDevice` on the branch.
    One validation names all three, and no run is created.
    """
    projection = local_product_projection(tmp_path)
    _store_reached_by_the_worker(monkeypatch, projection)
    _set_worker_environment(monkeypatch, {_EMPTY_NAME: "worker-held-token"})
    await _poll(_worker())
    # The API holds the destination token the schema read needs (Compose gives it to the API).
    monkeypatch.setenv(_EMPTY_NAME, "api-held-token")
    stand_in = _StandInClient(("object:Infra:Device:view:allow_all", "object:Infra:Device:update:allow_all"), nodes={})

    def _sdk_client(address: str, config: object) -> _StandInClient:
        del address, config
        return stand_in

    monkeypatch.setattr("infrahub_sdk.InfrahubClientSync", _sdk_client)
    client = _validation_client(tmp_path, monkeypatch, projection, bundled_reads=True)
    data = package_data()
    data["configuration"]["schema_mapping"] = [
        {
            "name": "InfraDevice",
            "mapping": "dcim.devices",
            "identifiers": ["name"],
            "fields": [{"name": "name", "mapping": "name"}, {"name": "serial", "mapping": "serial"}],
        }
    ]

    body = _validate(client, branch="review", data=data)

    found = {(finding["code"], finding["location"]): finding["message"] for finding in body["findings"]}
    assert "serial" in found["destination-schema-mismatch", "/configuration/schema_mapping/0/fields/1/name"]
    assert "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN" in found["credential-not-set-in-worker", "/credentials/netbox-token"]
    permission = found["destination-permission-missing", "/configuration/schema_mapping/0/name"]
    assert "InfraDevice" in permission
    assert "object:Infra:Device:create" in permission
    assert set(stand_in.schema.branches) == {"review"}
    runs = client.get("/runs")
    assert runs.status_code == 200, runs.text
    assert runs.json()["items"] == []
