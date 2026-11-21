"""The removed credential format: the ``credentials`` block and ``$credential`` references (spec 027, FR-021).

A package in that format is refused when it is parsed, with a message that names
``credential: <name>``. A version that was stored before the removal is refused when a run of it
starts, and by the worker route that releases stored credential values, with the code
``credential-format-removed`` and a message that says to register a new version.

The refusals name no reference, no provider and no identifier of the refused package.
"""

from __future__ import annotations

import copy
import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from infrahub_sync.configuration import (
    ConfigurationPackage,
    ConfigurationPackageParseError,
    parse_configuration_package,
)
from infrahub_sync.configuration.models import RemovedCredentialFormatError
from tests.configuration.validation_packages import credential_package_data
from tests.helpers.removed_credential_format import (
    REMOVED_IDENTIFIER,
    REMOVED_REFERENCE_NAME,
    removed_format_content,
    store_in_removed_format,
)

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

REFERENCE_NAME = REMOVED_REFERENCE_NAME
IDENTIFIER = REMOVED_IDENTIFIER
NAMES_THE_NEW_FORMAT = "credential: <name>"
FORMAT_REMOVED = "credential-format-removed"
WORKER_TOKEN = "worker-token-of-the-environment-1"  # noqa: S105 - a test value, not a secret.


def _with_credentials_block(block: dict[str, Any]) -> dict[str, Any]:
    data = credential_package_data()
    data["credentials"] = block
    return data


def _with_reference_in_settings() -> dict[str, Any]:
    data = credential_package_data()
    del data["configuration"]["source"]["credential"]
    data["configuration"]["source"]["settings"]["token"] = {"$credential": REFERENCE_NAME}
    return data


def _with_nested_reference() -> dict[str, Any]:
    data = credential_package_data()
    data["configuration"]["source"]["settings"]["params"] = {"filters": [{"value": {"$credential": REFERENCE_NAME}}]}
    return data


def _old_format() -> dict[str, Any]:
    """Return a package as a version stored before the removal declares it."""
    return removed_format_content(credential_package_data())


REMOVED_FORMAT_PACKAGES = {
    "credentials-block": lambda: _with_credentials_block(
        {REFERENCE_NAME: {"provider": "env", "identifier": IDENTIFIER}}
    ),
    "empty-credentials-block": lambda: _with_credentials_block({}),
    "reference-in-a-credential-setting": _with_reference_in_settings,
    "reference-nested-in-a-setting": _with_nested_reference,
    "block-and-references": _old_format,
}


@pytest.mark.parametrize("case", sorted(REMOVED_FORMAT_PACKAGES))
def test_a_package_in_the_removed_format_is_refused_at_parse_time(case: str) -> None:
    data = REMOVED_FORMAT_PACKAGES[case]()

    with pytest.raises(RemovedCredentialFormatError) as refused:
        parse_configuration_package(data)

    message = str(refused.value)
    assert NAMES_THE_NEW_FORMAT in message
    assert REFERENCE_NAME not in message
    assert IDENTIFIER not in message
    assert isinstance(refused.value, ConfigurationPackageParseError)


def test_the_refusal_names_the_credentials_block_and_the_references_it_found() -> None:
    with pytest.raises(RemovedCredentialFormatError) as block:
        parse_configuration_package(_with_credentials_block({}))
    with pytest.raises(RemovedCredentialFormatError) as reference:
        parse_configuration_package(_with_reference_in_settings())
    with pytest.raises(RemovedCredentialFormatError) as both:
        parse_configuration_package(_old_format())

    assert "`credentials` block" in str(block.value)
    assert "$credential" not in str(block.value)
    assert "`$credential` references" in str(reference.value)
    assert "`credentials` block" not in str(reference.value)
    assert "`credentials` block" in str(both.value)
    assert "`$credential` references" in str(both.value)


@pytest.mark.parametrize("case", sorted(REMOVED_FORMAT_PACKAGES))
def test_the_package_model_refuses_the_removed_format_without_the_parse_function(case: str) -> None:
    with pytest.raises(ValidationError) as refused:
        ConfigurationPackage.model_validate(REMOVED_FORMAT_PACKAGES[case]())

    assert refused.value.error_count() == 1
    rendered = str(refused.value.errors(include_input=False, include_url=False))
    assert REFERENCE_NAME not in rendered
    assert IDENTIFIER not in rendered


def test_the_removed_format_is_reported_before_every_other_defect_of_the_package() -> None:
    data = _old_format()
    data["configuration"]["unknown_field"] = True
    data["unknown_root_field"] = True

    with pytest.raises(RemovedCredentialFormatError) as refused:
        parse_configuration_package(data)

    assert "unknown" not in str(refused.value)


def test_a_package_that_names_stored_credentials_is_parsed_and_declares_no_credentials_block() -> None:
    package = parse_configuration_package(credential_package_data())

    assert "credentials" not in package.declared_content()
    assert not hasattr(package, "credentials")


def test_a_setting_value_that_only_looks_like_a_reference_name_is_not_the_removed_format() -> None:
    data = credential_package_data()
    data["configuration"]["source"]["settings"]["params"] = {"note": "$credential", "credentials": "credentials"}

    package = parse_configuration_package(copy.deepcopy(data))

    assert package.configuration.source.settings is not None


# ----------------------------------------------------------------------
# A version stored before the removal
# ----------------------------------------------------------------------


class _StoredOldVersion:
    """The Sync API with one registered version whose stored content is rewritten to the removed format."""

    def __init__(self, api: Any, tmp_path: Path) -> None:  # noqa: ANN401 - the API of the service tests, imported only where the service is installed.
        self.api = api
        self._tmp_path = tmp_path
        self.version: dict[str, Any] = {}
        self._requests = 0

    def register(self) -> None:
        from tests.helpers.stored_credentials import create_stored_credential
        from tests.service.account_api import ADMIN_TOKEN, bearer

        data = credential_package_data()
        for block in ("source", "destination"):
            create_stored_credential(
                self.api.store, data["configuration"][block]["credential"], {"token": f"marker-value-of-{block}"}
            )
        registered = self.api.client.post(
            "/configs",
            headers={**bearer(ADMIN_TOKEN), "Idempotency-Key": "register-before-the-removal"},
            json={"package": data, "reason": "register"},
        )
        assert registered.status_code == 201, registered.json()["error"]["code"]
        self.version = registered.json()["version"]

    def rewrite_to_the_removed_format(self) -> None:
        """Store the content of the version as a release before the removal wrote it."""
        store_in_removed_format(
            self._tmp_path, self.version["config_id"], self.version["registry_version"], credential_package_data()
        )

    def execute(self, statement: str, values: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        databases = sorted(self._tmp_path.rglob("*.sqlite3"))
        assert len(databases) == 1
        with sqlite3.connect(databases[0]) as connection:
            return connection.execute(statement, values).fetchall()

    def start(self, operation: str) -> Response:
        from tests.service.account_api import ADMIN_TOKEN, bearer

        self._requests += 1
        return self.api.client.post(
            "/runs",
            headers={**bearer(ADMIN_TOKEN), "Idempotency-Key": f"start-{operation}-{self._requests}"},
            json={
                "operation": operation,
                "config_id": self.version["config_id"],
                "registry_version": self.version["registry_version"],
                "reason": "start a run of a version stored before the removal",
                "confirm_writes": True,
            },
        )

    def run_count(self) -> int:
        return int(self.execute("SELECT COUNT(*) FROM product_runs")[0][0])


@pytest.fixture
def stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _StoredOldVersion:
    """Build the Sync API; skipped where the service is not installed (Python 3.10, base install)."""
    pytest.importorskip("fastapi")
    pytest.importorskip("prefect")
    pytest.importorskip("cryptography")
    from infrahub_sync.service.auth import apply_worker_account
    from infrahub_sync.service.orchestration import Submission
    from tests.helpers.stored_credentials import TEST_ENCRYPTION_KEY
    from tests.service import account_api

    flow_runs = iter(range(1_000))

    async def submit(  # noqa: RUF029 - it replaces an asynchronous method.
        _self: object, parameters: dict[str, object], *, idempotency_key: str
    ) -> Submission:
        del parameters, idempotency_key
        return Submission(flow_run_id=f"flow-run-{next(flow_runs)}", state="pending")

    monkeypatch.setattr(account_api.Orchestration, "submit", submit)
    api = account_api.build_account_api(tmp_path, monkeypatch, credential_key=TEST_ENCRYPTION_KEY)
    apply_worker_account(api.store, WORKER_TOKEN)
    version = _StoredOldVersion(api, tmp_path)
    version.register()
    return version


def _format_removed(response: Response) -> dict[str, Any]:
    """Check the refusal, and that it says what to do and names nothing of the refused package."""
    assert response.status_code == 422, response.json()["error"]["code"]
    error = response.json()["error"]
    assert error["code"] == FORMAT_REMOVED
    assert "register a new version" in error["message"]
    assert NAMES_THE_NEW_FORMAT in error["message"]
    assert REFERENCE_NAME not in response.text
    assert IDENTIFIER not in response.text
    return error


@pytest.mark.parametrize("operation", ["plan", "sync"])
def test_a_run_of_a_version_stored_in_the_removed_format_is_refused_before_it_starts(
    stored: _StoredOldVersion, operation: str
) -> None:
    stored.rewrite_to_the_removed_format()

    response = stored.start(operation)

    _format_removed(response)
    assert stored.run_count() == 0


def test_a_registration_in_the_removed_format_is_refused_with_its_reason(stored: _StoredOldVersion) -> None:
    from tests.service.account_api import ADMIN_TOKEN, bearer

    response = stored.api.client.post(
        "/configs",
        headers={**bearer(ADMIN_TOKEN), "Idempotency-Key": "register-the-removed-format"},
        json={"package": _old_format(), "reason": "register a package in the removed format"},
    )

    assert response.status_code == 422, response.json()["error"]["code"]
    error = response.json()["error"]
    assert (error["code"], error["reason"]) == ("configs-validation", FORMAT_REMOVED)
    assert NAMES_THE_NEW_FORMAT in error["message"]
    assert REFERENCE_NAME not in response.text
    assert IDENTIFIER not in response.text


def test_the_validation_of_a_version_stored_in_the_removed_format_is_refused_with_its_reason(
    stored: _StoredOldVersion,
) -> None:
    from tests.service.account_api import ADMIN_TOKEN, bearer

    stored.rewrite_to_the_removed_format()

    response = stored.api.client.post(
        f"/configs/{stored.version['config_id']}/versions/{stored.version['registry_version']}/validate",
        headers=bearer(ADMIN_TOKEN),
    )

    assert response.status_code == 422, response.json()["error"]["code"]
    assert response.json()["error"]["reason"] == FORMAT_REMOVED
    assert REFERENCE_NAME not in response.text
    assert IDENTIFIER not in response.text


def test_the_worker_route_refuses_a_run_whose_stored_version_is_in_the_removed_format(
    stored: _StoredOldVersion,
) -> None:
    from tests.service.account_api import bearer

    accepted = stored.start("plan")
    assert accepted.status_code in {200, 201, 202}, accepted.json()["error"]["code"]
    run_id = accepted.json()["run"]["run_id"]
    run = stored.api.store.lookup_run(run_id).value
    assert run is not None
    flow_run_id = run.prefect_executions[-1].flow_run_id
    assert stored.api.store.claim_execution(run_id, flow_run_id, worker_id=str(uuid4()))
    stored.rewrite_to_the_removed_format()

    response = stored.api.client.post(
        f"/worker/runs/{run_id}/credentials", headers=bearer(WORKER_TOKEN), json={"flow_run_id": flow_run_id}
    )

    _format_removed(response)
    assert "marker-value-of-" not in response.text
    assert stored.api.store.credential_uses(run_id) == ()


def test_a_scheduled_start_of_a_version_stored_in_the_removed_format_is_refused(stored: _StoredOldVersion) -> None:
    from types import SimpleNamespace

    from infrahub_sync.service import schedule_admission

    stored.rewrite_to_the_removed_format()
    schedule = SimpleNamespace(
        config_id=stored.version["config_id"],
        registry_version=stored.version["registry_version"],
        write_target="direct",
    )

    refused = schedule_admission._runnable_version(stored.api.store, schedule)  # ty: ignore[invalid-argument-type] - TODO: a stand-in for the schedule record; the function reads three attributes.

    assert refused == FORMAT_REMOVED
