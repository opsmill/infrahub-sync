"""Registration and run start check the stored credentials that a package names (spec 027, FR-008).

A package names one stored credential per block with ``credential: <name>``. Registration refuses
a package when a named credential does not exist, or does not hold a field that the block's
adapter and ``auth_method`` need. A run is refused before it starts when that became true after
registration: the credential was deleted, or its fields changed.

The store cases run on the three profiles of ``test_stored_credentials``. The cases of the run
routes use the in-process API on the SQLite store, and remove a credential with SQL statements,
because the deletion route refuses a credential that the latest version of a configuration names.

No test puts a credential value in an assertion message.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from infrahub_sync.product_store import ProductProjection, configs
from infrahub_sync.product_store.models import ExecutionFinishWriteback
from tests.configuration.validation_packages import package_data
from tests.helpers.stored_credentials import TEST_ENCRYPTION_KEY, create_stored_credential
from tests.product_store.test_contract import _PROFILES_WITH_POSTGRESQL, _projection_profile

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.configuration import ValidationFinding
    from tests.service.account_api import AccountApi

_T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
_ADMIN = "acct_" + "a" * 32
SOURCE_CREDENTIAL = "netbox-lab"
DESTINATION_CREDENTIAL = "infrahub-main"
CREATE_COMMAND = "infrahub-sync credentials create"
CHECKSUM = "c" * 64
SOURCE_VALUE = "admission-source-value-51c7"
DESTINATION_VALUE = "admission-destination-value-9a3e"
ADMIN_TOKEN = "admin-token-canary-0022"  # noqa: S105 - the test value of `tests.service.account_api`.


def _fake_encrypt(credential_id: str, revision: int) -> tuple[str, bytes, bytes]:
    """Return fixed bytes: the store holds no value, and these cases read none."""
    del credential_id, revision
    return "0123456789abcdef", bytes(range(12)), b"\x00" * 32


def _store(projection: ProductProjection, name: str, fields: tuple[str, ...] = ("token",)) -> None:
    if projection.lookup_stored_credential(name) is None:
        projection.create_stored_credential(
            name=name, description="", fields=fields, encrypt=_fake_encrypt, actor=_ADMIN, now=_T0
        )
    else:
        projection.change_stored_credential_value(name=name, fields=fields, encrypt=_fake_encrypt, actor=_ADMIN)


def _package(*, source: dict[str, Any] | None = None, url: str | None = None) -> dict[str, Any]:
    """Return a package whose source and destination each name a stored credential."""
    data = package_data()
    data.pop("credentials", None)
    for block, name in (("source", SOURCE_CREDENTIAL), ("destination", DESTINATION_CREDENTIAL)):
        data["configuration"][block]["settings"].pop("token", None)
        data["configuration"][block]["credential"] = name
    if source is not None:
        data["configuration"]["source"] = source
    if url is not None:
        data["configuration"]["source"]["settings"]["url"] = url
    return data


def _aci_source() -> dict[str, Any]:
    """Return a source block whose adapter needs a ``username`` and a ``password``."""
    return {"name": "aci", "credential": SOURCE_CREDENTIAL, "settings": {"url": "https://apic.example.com"}}


def _finding(refusal: configs.ConfigsValidationError, code: str) -> ValidationFinding:
    found = [finding for finding in refusal.findings if finding.code == code]
    assert len(found) == 1, [finding.code for finding in refusal.findings]
    return found[0]


@pytest.fixture(params=_PROFILES_WITH_POSTGRESQL)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the credential admission's PostgreSQL parameter")


def _delete_credential(tmp_path: Path, name: str) -> None:
    """Remove a credential and its revisions from the one SQLite store under ``tmp_path``."""
    databases = sorted(tmp_path.rglob("*.sqlite3"))
    assert len(databases) == 1
    with sqlite3.connect(databases[0]) as connection:
        connection.execute(
            "DELETE FROM stored_credential_revisions WHERE credential_id IN "
            "(SELECT credential_id FROM stored_credentials WHERE name = ?)",
            (name,),
        )
        connection.execute("DELETE FROM stored_credentials WHERE name = ?", (name,))


# --- registration -------------------------------------------------------------------------


def test_registration_refuses_a_credential_that_does_not_exist(projection: ProductProjection) -> None:
    _store(projection, DESTINATION_CREDENTIAL)

    with pytest.raises(configs.ConfigsValidationError) as refused:
        configs.register(package=_package(), projection=projection)

    finding = _finding(refused.value, "credential-not-found")
    assert finding.severity == "error"
    assert finding.location == "/configuration/source/credential"
    # The message says who can correct it, and with which command (critique P5).
    assert f"{CREATE_COMMAND} {SOURCE_CREDENTIAL}" in finding.message
    assert "administrator" in finding.message
    assert refused.value.reason == "credential-not-found"
    assert configs.list_configs(projection=projection).items == ()


def test_registration_refuses_a_credential_without_a_field_its_block_needs(projection: ProductProjection) -> None:
    # The credential holds only a token; the ACI adapter needs a username and a password.
    _store(projection, SOURCE_CREDENTIAL, ("token",))
    _store(projection, DESTINATION_CREDENTIAL)

    with pytest.raises(configs.ConfigsValidationError) as refused:
        configs.register(package=_package(source=_aci_source()), projection=projection)

    finding = _finding(refused.value, "credential-field-missing")
    assert finding.severity == "error"
    assert finding.location == "/configuration/source/credential"
    assert "password, username" in finding.message
    assert SOURCE_CREDENTIAL in finding.message
    assert refused.value.reason == "credential-field-missing"
    assert configs.list_configs(projection=projection).items == ()


def test_registration_reports_every_block_whose_credential_fails(projection: ProductProjection) -> None:
    with pytest.raises(configs.ConfigsValidationError) as refused:
        configs.register(package=_package(), projection=projection)

    assert sorted(finding.location for finding in refused.value.findings if finding.code == "credential-not-found") == [
        "/configuration/destination/credential",
        "/configuration/source/credential",
    ]


def test_a_new_version_is_refused_for_a_credential_that_does_not_exist(projection: ProductProjection) -> None:
    _store(projection, SOURCE_CREDENTIAL)
    _store(projection, DESTINATION_CREDENTIAL)
    registered = configs.register(package=_package(), projection=projection)
    changed = _package()
    changed["configuration"]["source"]["credential"] = "netbox-other"

    with pytest.raises(configs.ConfigsValidationError) as refused:
        configs.create_version(config_id=registered.configuration.config_id, package=changed, projection=projection)

    assert _finding(refused.value, "credential-not-found").location == "/configuration/source/credential"
    versions = configs.list_versions(config_id=registered.configuration.config_id, projection=projection)
    assert [version.registry_version for version in versions.items] == [1]


def test_registration_records_the_credential_each_block_names(projection: ProductProjection) -> None:
    _store(projection, SOURCE_CREDENTIAL)
    _store(projection, DESTINATION_CREDENTIAL)

    registered = configs.register(package=_package(), projection=projection)
    config_id = registered.configuration.config_id
    changed = _package()
    changed["configuration"]["source"]["credential"] = DESTINATION_CREDENTIAL
    second = configs.create_version(config_id=config_id, package=changed, projection=projection)

    assert projection.version_credentials(config_id, 1) == {
        "source": SOURCE_CREDENTIAL,
        "destination": DESTINATION_CREDENTIAL,
    }
    assert second.created is True
    assert projection.version_credentials(config_id, 2) == {
        "source": DESTINATION_CREDENTIAL,
        "destination": DESTINATION_CREDENTIAL,
    }


def test_an_identical_version_keeps_its_recorded_credentials(projection: ProductProjection) -> None:
    _store(projection, SOURCE_CREDENTIAL)
    _store(projection, DESTINATION_CREDENTIAL)
    registered = configs.register(package=_package(), projection=projection)
    config_id = registered.configuration.config_id

    again = configs.create_version(config_id=config_id, package=_package(), projection=projection)

    assert again.created is False
    assert projection.version_credentials(config_id, 1) == {
        "source": SOURCE_CREDENTIAL,
        "destination": DESTINATION_CREDENTIAL,
    }


def test_a_package_that_names_no_credential_records_none(projection: ProductProjection, monkeypatch: Any) -> None:  # noqa: ANN401
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN", "old-path-netbox-value")
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", "old-path-infrahub-value")

    registered = configs.register(package=package_data(), projection=projection)

    assert projection.version_credentials(registered.configuration.config_id, 1) == {}


def test_one_credential_serves_packages_with_different_addresses(projection: ProductProjection) -> None:
    # Spec assumption: a credential is not bound to an address.
    _store(projection, SOURCE_CREDENTIAL)
    _store(projection, DESTINATION_CREDENTIAL)

    first = configs.register(package=_package(url="https://netbox-lab.example.com"), projection=projection)
    second = configs.register(package=_package(url="https://netbox-prod.example.com"), projection=projection)

    assert first.configuration.config_id != second.configuration.config_id
    for registered in (first, second):
        assert projection.version_credentials(registered.configuration.config_id, 1)["source"] == SOURCE_CREDENTIAL


def test_validation_of_a_registered_version_reports_a_credential_deleted_since(tmp_path: Path) -> None:
    projection = next(_projection_profile("local", tmp_path, "unused"))
    _store(projection, SOURCE_CREDENTIAL)
    _store(projection, DESTINATION_CREDENTIAL)
    registered = configs.register(package=_package(), projection=projection)
    _delete_credential(tmp_path, SOURCE_CREDENTIAL)

    report = configs.validate(config_id=registered.configuration.config_id, registry_version=1, projection=projection)

    assert [(finding.code, finding.location) for finding in report.findings] == [
        ("credential-not-found", "/configuration/source/credential")
    ]


# --- the run routes -----------------------------------------------------------------------


class Admission:
    """The API with both stored credentials, one registered version that names them, and its runs."""

    def __init__(self, api: AccountApi, tmp_path: Path) -> None:
        self.api = api
        self.tmp_path = tmp_path
        self._keys = 0
        create_stored_credential(api.store, SOURCE_CREDENTIAL, {"token": SOURCE_VALUE})
        create_stored_credential(api.store, DESTINATION_CREDENTIAL, {"token": DESTINATION_VALUE})
        registered = self.register(_package())
        assert registered.status_code == 201, registered.json()["error"]["code"]
        self.version: dict[str, Any] = registered.json()["version"]

    def headers(self, token: str = ADMIN_TOKEN) -> dict[str, str]:
        self._keys += 1
        return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"admission-{self._keys}"}

    def register(self, package: dict[str, Any], token: str = ADMIN_TOKEN) -> Response:
        return self.api.client.post(
            "/configs", headers=self.headers(token), json={"package": package, "reason": "register"}
        )

    def create_run(self, operation: str = "plan") -> Response:
        body: dict[str, Any] = {
            "operation": operation,
            "config_id": self.version["config_id"],
            "registry_version": self.version["registry_version"],
        }
        if operation == "sync":
            body["confirm_writes"] = True
        return self.api.client.post("/runs", headers=self.headers(), json=body)

    def run(self) -> str:
        created = self.create_run()
        assert created.status_code in {200, 201, 202}, created.json()["error"]["code"]
        return created.json()["run"]["run_id"]

    def planned_run(self) -> str:
        """Return a run whose plan stage ended with the phase ``planned``, as its worker ends it."""
        run_id = self.run()
        stored = self.api.store.lookup_run(run_id).value
        assert stored is not None
        flow_run_id, worker_id = stored.prefect_executions[-1].flow_run_id, str(uuid4())
        assert self.api.store.claim_execution(run_id, flow_run_id, worker_id=worker_id)
        assert self.api.store.commit_claimed_execution(
            run_id,
            flow_run_id,
            worker_id=worker_id,
            terminal_at=datetime.now(timezone.utc),
            terminal_state="completed",
            terminal_outcome="succeeded",
            writeback=ExecutionFinishWriteback(
                phase="planned",
                outcome="planned",
                finished_at=datetime.now(timezone.utc),
                summary={"sync_name": "from-netbox"},
                results={"plan": {"outcome": "planned"}},
            ),
        )
        return run_id

    def stage(self, run_id: str, operation: str) -> Response:
        body: dict[str, Any] = {}
        if operation in {"apply", "resume"}:
            body = {"expected_checksum": CHECKSUM, "confirm_writes": True}
        if operation == "approve":
            body = {"expected_checksum": CHECKSUM}
        return self.api.client.post(f"/runs/{run_id}/{operation}", headers=self.headers(), json=body)

    def delete(self, name: str) -> None:
        _delete_credential(self.tmp_path, name)

    def runs(self) -> int:
        listed = self.api.client.get("/runs", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert listed.status_code == 200
        return len(listed.json()["items"])


@pytest.fixture
def admission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Admission:
    # The Sync service needs the `service` extra, which Python 3.10 does not install.
    for module in ("fastapi", "prefect", "cryptography", "psycopg"):
        pytest.importorskip(module)
    from tests.service import account_api

    assert account_api.ADMIN_TOKEN == ADMIN_TOKEN
    build_account_api = account_api.build_account_api
    return Admission(build_account_api(tmp_path, monkeypatch, credential_key=TEST_ENCRYPTION_KEY), tmp_path)


def _refused(response: Response, code: str) -> dict[str, Any]:
    """Check a ``422`` refusal with this code, and that it holds no value; return its error."""
    leaked = SOURCE_VALUE in response.text or DESTINATION_VALUE in response.text
    assert not leaked
    assert response.status_code == 422, response.json()["error"]["code"]
    error = response.json()["error"]
    assert error["code"] == code
    return error


def test_the_registration_route_refuses_a_credential_that_does_not_exist(admission: Admission) -> None:
    package = _package()
    package["configuration"]["source"]["credential"] = "netbox-other"

    response = admission.register(package)

    assert response.status_code == 422
    error = response.json()["error"]
    assert (error["code"], error["reason"]) == ("configs-validation", "credential-not-found")


def test_an_account_that_is_not_an_administrator_cannot_register_a_package_that_names_a_credential(
    admission: Admission,
) -> None:
    package = _package(url="https://netbox-second.example.com")

    response = admission.register(package, token=admission.api.token("alice"))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert len(configs.list_configs(projection=admission.api.store).items) == 1


@pytest.mark.parametrize("operation", ["plan", "sync"])
def test_a_run_is_refused_before_it_starts_when_a_credential_was_deleted(admission: Admission, operation: str) -> None:
    admission.delete(SOURCE_CREDENTIAL)

    error = _refused(admission.create_run(operation), "credential-not-found")

    assert f"{CREATE_COMMAND} {SOURCE_CREDENTIAL}" in error["message"]
    assert "administrator" in error["message"]
    assert error["details"] == {"block": "source", "name": SOURCE_CREDENTIAL}
    assert admission.runs() == 0


@pytest.mark.parametrize("operation", ["plan", "sync"])
def test_a_run_is_refused_before_it_starts_when_a_credential_lost_a_field(admission: Admission, operation: str) -> None:
    # The value changed after registration: the credential now holds no token.
    create_stored_credential(admission.api.store, SOURCE_CREDENTIAL, {"username": "sync", "password": SOURCE_VALUE})

    error = _refused(admission.create_run(operation), "credential-field-missing")

    assert error["details"] == {"block": "source", "name": SOURCE_CREDENTIAL, "fields": ["token"]}
    assert admission.runs() == 0


@pytest.mark.parametrize("operation", ["verify", "apply", "approve"])
def test_a_stage_of_a_run_is_refused_when_the_destination_credential_was_deleted(
    admission: Admission, operation: str
) -> None:
    run_id = admission.planned_run()
    admission.delete(DESTINATION_CREDENTIAL)

    error = _refused(admission.stage(run_id, operation), "credential-not-found")

    assert error["details"] == {"block": "destination", "name": DESTINATION_CREDENTIAL}
    stored = admission.api.store.lookup_run(run_id).value
    assert stored is not None
    assert len(stored.prefect_executions) == 1


@pytest.mark.parametrize("operation", ["verify", "apply", "approve"])
def test_a_stage_that_connects_to_the_destination_only_is_not_refused_for_the_source_credential(
    admission: Admission, operation: str
) -> None:
    run_id = admission.planned_run()
    admission.delete(SOURCE_CREDENTIAL)

    response = admission.stage(run_id, operation)

    # The run saved no plan in this test, so the request is refused later, for that reason.
    assert response.json()["error"]["code"] not in {"credential-not-found", "credential-field-missing"}


@pytest.mark.parametrize("operation", ["verify", "apply", "resume"])
def test_the_check_of_a_run_names_the_blocks_of_its_stage(admission: Admission, operation: str) -> None:
    # `resume` reaches this check only for an interrupted write, so the check is called directly.
    run_id = admission.planned_run()
    run = admission.api.store.lookup_run(run_id).value
    assert run is not None
    admission.delete(SOURCE_CREDENTIAL)
    admission.api.service._require_supported_run_configuration(run, operation)
    admission.delete(DESTINATION_CREDENTIAL)
    from infrahub_sync.service.service import ServiceAPIError

    with pytest.raises(ServiceAPIError) as refused:
        admission.api.service._require_supported_run_configuration(run, operation)

    assert (refused.value.status, refused.value.code) == (422, "credential-not-found")
