"""The destination schema read takes the token of a stored credential (spec 027, FR-004).

A destination block that names a stored credential declares no token. Two processes read the
destination schema:

- The Sync API, when it validates a registered version with a branch. It decrypts the token
  inside the request, gives it to the read, and returns it nowhere.
- A worker, which gives the read the token that the Sync API released to its run.

Every value is a marker text. A check compares digests or a boolean, so a failed check prints no
value.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")
pytest.importorskip("cryptography")

from fastapi.testclient import TestClient

from infrahub_sync.configuration import capabilities as capabilities_module
from infrahub_sync.configuration import parse_configuration_package, schema_validation
from infrahub_sync.configuration.capabilities import (
    BUILTIN_ADAPTER_CAPABILITIES,
    DestinationSchemaReadError,
    destination_token,
)
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.execution import REDACTED
from infrahub_sync.runtime_schema import build_runtime_model_plan
from infrahub_sync.runtime_schema import worker as worker_module
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import StoreAccountResolver
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.credential_crypto import CredentialCipher
from tests.configuration.validation_packages import package_data
from tests.helpers.stored_credentials import TEST_ENCRYPTION_KEY, create_stored_credential
from tests.service.account_api import ADMIN_TOKEN, AccountApi, bearer, build_account_api

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.configuration import ConfigurationPackage

SOURCE_TOKEN = "schema-read-marker-source-token-2f6a"  # noqa: S105 - a test value, not a secret.
DESTINATION_TOKEN = "schema-read-marker-destination-token-d09c"  # noqa: S105 - a test value, not a secret.
SOURCE_CREDENTIAL = "netbox-lab"
DESTINATION_CREDENTIAL = "infrahub-main"
SNAPSHOT: dict[str, Any] = {
    "BuiltinTag": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": {"kind": "Text", "optional": False, "default_value": None, "unique": True}},
        "relationships": {},
    },
}
MAPPING = [{"name": "BuiltinTag", "mapping": "extras.tags", "fields": [{"name": "name", "mapping": "name"}]}]
READ_FAILED = "destination-schema-read-failed"


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def _package() -> dict[str, Any]:
    data = package_data()
    data.pop("credentials", None)
    data["configuration"]["schema_mapping"] = MAPPING
    for block, name in (("source", SOURCE_CREDENTIAL), ("destination", DESTINATION_CREDENTIAL)):
        data["configuration"][block]["settings"].pop("token", None)
        data["configuration"][block]["credential"] = name
    return data


class Reads:
    """The tokens that the destination reads were given, as digests, and what a read does next."""

    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.fail_with_the_token = False
        self.refuse_with_the_token = False

    def accessor(self, package: ConfigurationPackage, branch: str) -> dict[str, Any]:
        """Build the client settings as the Infrahub accessor does, and answer a fixed snapshot."""
        _, sdk_config = capabilities_module._resolved_client_settings(package, branch)
        token = sdk_config["api_token"]
        self.tokens.append(_digest(token))
        if self.refuse_with_the_token:
            # What Infrahub answers with a refusal is the `detail` of the read error, which the
            # finding shows; here it repeats the token it was given.
            message = "the destination refused the schema read"
            raise DestinationSchemaReadError(message, reason="unauthorized", detail=f"token {token} is not valid")
        if self.fail_with_the_token:
            logging.getLogger("infrahub_sync.configuration.test").error("the destination refused the token %s", token)
            msg = f"the destination refused the token {token}"
            raise RuntimeError(msg)
        return SNAPSHOT


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> Reads:
    """Replace the request of the Infrahub schema read; the settings of the read stay the product's own."""
    spy = Reads()
    table = dict(BUILTIN_ADAPTER_CAPABILITIES)
    table["infrahub"] = replace(
        table["infrahub"], destination_schema_accessor=spy.accessor, destination_access_accessor=None
    )
    monkeypatch.setattr(schema_validation, "BUILTIN_ADAPTER_CAPABILITIES", table)
    return spy


class Validation:
    """The API with both stored credentials and one registered version that names them."""

    def __init__(self, api: AccountApi, tmp_path: Path) -> None:
        self.api = api
        self.tmp_path = tmp_path
        create_stored_credential(api.store, SOURCE_CREDENTIAL, {"token": SOURCE_TOKEN})
        create_stored_credential(api.store, DESTINATION_CREDENTIAL, {"token": DESTINATION_TOKEN})
        registered = api.client.post(
            "/configs",
            headers={**bearer(ADMIN_TOKEN), "Idempotency-Key": "register-for-schema-read"},
            json={"package": _package(), "reason": "register"},
        )
        assert registered.status_code == 201, registered.json()["error"]["code"]
        self.version: dict[str, Any] = registered.json()["version"]

    def validate(self, branch: str | None = "main", token: str = ADMIN_TOKEN) -> Response:
        path = f"/configs/{self.version['config_id']}/versions/{self.version['registry_version']}/validate"
        return self.api.client.post(path, headers=bearer(token), params={} if branch is None else {"branch": branch})


@pytest.fixture
def validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Validation:
    return Validation(build_account_api(tmp_path, monkeypatch, credential_key=TEST_ENCRYPTION_KEY), tmp_path)


def _codes(response: Response) -> list[str]:
    return [finding["code"] for finding in response.json()["findings"]]


# --- the Sync API ---------------------------------------------------------------------------


def test_validation_reads_the_destination_with_the_decrypted_token_and_returns_it_nowhere(
    validation: Validation, reads: Reads
) -> None:
    response = validation.validate()

    assert response.status_code == 200
    assert reads.tokens == [_digest(DESTINATION_TOKEN)]
    assert READ_FAILED not in _codes(response)
    assert response.json()["destination_schema_fingerprint"] is not None
    returned = DESTINATION_TOKEN in response.text or SOURCE_TOKEN in response.text
    stored = DESTINATION_TOKEN in validation.api.stored_text()
    assert not returned
    assert not stored


def test_an_account_that_is_not_an_administrator_validates_without_receiving_the_token(
    validation: Validation, reads: Reads
) -> None:
    response = validation.validate(token=validation.api.token("alice"))

    assert response.status_code == 200
    assert reads.tokens == [_digest(DESTINATION_TOKEN)]
    returned = DESTINATION_TOKEN in response.text
    assert not returned


def test_validation_without_a_branch_decrypts_nothing(validation: Validation, reads: Reads) -> None:
    response = validation.validate(branch=None)

    assert response.status_code == 200
    assert reads.tokens == []
    assert _codes(response) == []


def test_an_error_of_the_read_that_holds_the_token_reaches_no_answer_and_no_log(
    validation: Validation, reads: Reads, caplog: pytest.LogCaptureFixture
) -> None:
    reads.fail_with_the_token = True
    caplog.set_level(logging.DEBUG)

    try:
        response = validation.validate()
        text = response.text
    except RuntimeError as error:
        # The test client raises what the application did not handle; a served API answers 500.
        text = str(error)

    assert reads.tokens == [_digest(DESTINATION_TOKEN)]
    in_answer = DESTINATION_TOKEN in text
    assert not in_answer
    service_lines = [
        record.getMessage() for record in caplog.records if record.name.startswith("infrahub_sync.service")
    ]
    in_service_log = any(DESTINATION_TOKEN in line for line in service_lines)
    assert not in_service_log


def test_a_finding_of_the_read_that_repeats_the_token_is_redacted(validation: Validation, reads: Reads) -> None:
    reads.refuse_with_the_token = True
    # The routes hold a fixed tuple of secrets: only the registry of the request holds the token,
    # and the answer of the request is redacted with that registry.
    served = _served_again(validation, key=TEST_ENCRYPTION_KEY, fixed_secrets=True)

    response = served.validate()

    assert response.status_code == 200
    assert reads.tokens == [_digest(DESTINATION_TOKEN)]
    findings = {finding["code"]: finding["message"] for finding in response.json()["findings"]}
    returned = DESTINATION_TOKEN in response.text
    assert not returned
    assert f"unauthorized (token {REDACTED} is not valid)" in findings[READ_FAILED]


def test_a_deleted_destination_credential_is_a_finding_and_no_request(validation: Validation, reads: Reads) -> None:
    databases = sorted(validation.tmp_path.rglob("*.sqlite3"))
    assert len(databases) == 1
    with sqlite3.connect(databases[0]) as connection:
        connection.execute(
            "DELETE FROM stored_credential_revisions WHERE credential_id IN "
            "(SELECT credential_id FROM stored_credentials WHERE name = ?)",
            (DESTINATION_CREDENTIAL,),
        )
        connection.execute("DELETE FROM stored_credentials WHERE name = ?", (DESTINATION_CREDENTIAL,))

    response = validation.validate()

    assert response.status_code == 200
    assert reads.tokens == []
    findings = {finding["code"]: finding["message"] for finding in response.json()["findings"]}
    assert "credentials" in findings[READ_FAILED]
    assert "credential-not-found" in findings


def test_validation_without_an_encryption_key_is_a_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reads: Reads
) -> None:
    # The version is registered by an API that holds the key; the API that validates holds none.
    with_key = Validation(build_account_api(tmp_path, monkeypatch, credential_key=TEST_ENCRYPTION_KEY), tmp_path)
    without_key = _without_a_key(with_key)

    response = without_key.validate()

    assert response.status_code == 200
    assert reads.tokens == []
    findings = {finding["code"]: finding["message"] for finding in response.json()["findings"]}
    assert "credentials" in findings[READ_FAILED]


def _without_a_key(first: Validation) -> Validation:
    """Return the same registered version, served by an API over the same store with no encryption key."""
    return _served_again(first, key=None)


def _served_again(first: Validation, *, key: bytes | None, fixed_secrets: bool = False) -> Validation:
    """Return the same registered version, served by another API over the same store.

    With ``fixed_secrets``, the configuration routes hold a fixed tuple of secrets, as a served
    Sync API gives them, and not the live list of the test API.
    """
    store = first.api.store
    resolver = StoreAccountResolver(store, idle_seconds=600, admin_token=ADMIN_TOKEN, clock=first.api.clock)
    secrets = (ADMIN_TOKEN,) if fixed_secrets else resolver.secret_values
    routes = ConfigurationRoutes(product_projection=store, secrets=secrets)
    cipher = None if key is None else CredentialCipher(key)
    application = create_app(first.api.service, resolver, routes, credential_cipher=cipher)
    second = object.__new__(Validation)
    second.api = replace(first.api, client=TestClient(application))
    second.tmp_path = first.tmp_path
    second.version = first.version
    return second


# --- the settings of the read ---------------------------------------------------------------


def test_a_read_outside_every_given_token_is_refused_before_any_request() -> None:
    package = parse_configuration_package(_package())

    with pytest.raises(DestinationSchemaReadError) as refused:
        capabilities_module._resolved_client_settings(package, "main")

    assert refused.value.reason == "credentials"


def test_a_read_without_a_token_sends_none_and_reads_no_worker_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "ambient-token-of-the-worker")
    package = parse_configuration_package(_package())

    with destination_token(None):
        _, sdk_config = capabilities_module._resolved_client_settings(package, "main")

    assert sdk_config["api_token"] is None


def test_the_given_token_ends_with_its_block() -> None:
    package = parse_configuration_package(_package())

    with destination_token(DESTINATION_TOKEN):
        _, sdk_config = capabilities_module._resolved_client_settings(package, "main")
    assert _digest(sdk_config["api_token"]) == _digest(DESTINATION_TOKEN)

    with pytest.raises(DestinationSchemaReadError):
        capabilities_module._resolved_client_settings(package, "main")


def test_a_package_that_names_no_credential_ignores_the_given_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", "old-path-infrahub-value-0001")
    package = parse_configuration_package(package_data())

    with destination_token(DESTINATION_TOKEN):
        _, sdk_config = capabilities_module._resolved_client_settings(package, "main")

    assert _digest(sdk_config["api_token"]) == _digest("old-path-infrahub-value-0001")


# --- the worker -----------------------------------------------------------------------------


def test_the_worker_reads_the_schema_with_the_token_released_to_its_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def read(package: ConfigurationPackage, branch: str) -> dict[str, Any]:
        _, sdk_config = capabilities_module._resolved_client_settings(package, branch)
        seen.append(_digest(sdk_config["api_token"]))
        return SNAPSHOT

    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", read)
    package = parse_configuration_package(_package())
    instance = resolve_runtime_instance(
        package,
        directory=str(tmp_path),
        resolve_source_credentials=False,
        released={"destination": {"token": DESTINATION_TOKEN}},
    )

    build_runtime_model_plan(package=package, instance=instance, run_branch=None, scope="destination")

    assert seen == [_digest(DESTINATION_TOKEN)]
    assert REDACTED not in seen
