"""``DELETE /credentials/{name}`` (spec 027, User Story 4, FR-010, FR-011, contracts/http-api.md).

An administrator deletes a stored credential that nothing current names. The Sync API refuses
the deletion with ``409 credential-in-use`` while the latest version of a configuration names
the credential, or while a schedule that is not deleted pins a version that names it, and the
refusal lists those configurations and schedules. An older version does not block the deletion;
a run of that older version is then refused before it starts.

A run record keeps the name and the revision it used after the deletion. A run of an older
version that started before the deletion receives no value: the worker route checks the
credential again when it releases.

Every value is a marker text. A check compares a boolean, so a failed check prints no value.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")
pytest.importorskip("cryptography")

from infrahub_sync.product_store import ScheduleRecord
from infrahub_sync.service.auth import apply_worker_account
from infrahub_sync.service.orchestration import Submission
from tests.configuration.validation_packages import package_data
from tests.helpers.stored_credentials import TEST_ENCRYPTION_KEY
from tests.service import account_api
from tests.service.account_api import ADMIN_TOKEN, T0, AccountApi, bearer, build_account_api

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.product_store import AuditEvent

WORKER_TOKEN = "worker-token-of-the-environment-1"  # noqa: S105 - a test value, not a secret.
CREDENTIAL = "netbox-lab"
MARKER_PREFIX = "deletion-marker-value-"
DELETED_EVENT = "infrahub_sync.credential.deleted"
REFUSED_EVENT = "infrahub_sync.credential.refused"


def _package(name: str, *, source: str | None) -> dict[str, Any]:
    """Return a package with this configuration name, whose source names this credential or none."""
    data = package_data()
    data["configuration"]["name"] = name
    if source is not None:
        data["configuration"]["source"]["credential"] = source
    return data


def _checked(response: Response) -> Response:
    """Return the answer after the check that it holds no value."""
    holds_a_value = MARKER_PREFIX in response.text
    assert not holds_a_value
    return response


def _in_use(response: Response) -> dict[str, Any]:
    """Check a ``409 credential-in-use`` refusal and return its details."""
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "credential-in-use"
    return error["details"]


class Deletion:
    """The API with a ``worker`` account, and the steps around the deletion of a credential."""

    def __init__(self, api: AccountApi, tmp_path: Path) -> None:
        self.api = api
        self.root = tmp_path.resolve()
        self._keys = 0
        self._values = 0

    def headers(self, token: str = ADMIN_TOKEN, key: str | None = None) -> dict[str, str]:
        self._keys += 1
        return {**bearer(token), "Idempotency-Key": key or f"credential-deletion-{self._keys}"}

    def _value(self) -> dict[str, str]:
        self._values += 1
        return {"token": f"{MARKER_PREFIX}{self._values}"}

    def create_credential(self, name: str = CREDENTIAL) -> None:
        response = self.api.client.post("/credentials", headers=self.headers(), json={"name": name, **self._value()})
        assert response.status_code == 201, response.json()["error"]["code"]

    def change_value(self, name: str = CREDENTIAL) -> int:
        response = self.api.client.put(f"/credentials/{name}/value", headers=self.headers(), json=self._value())
        assert response.status_code == 200, response.json()["error"]["code"]
        return response.json()["revision"]

    def register(self, name: str = "from-netbox", *, source: str | None = CREDENTIAL) -> dict[str, Any]:
        """Register a configuration and return its first version."""
        response = self.api.client.post(
            "/configs", headers=self.headers(), json={"package": _package(name, source=source), "reason": "register"}
        )
        assert response.status_code == 201, response.json()["error"]["code"]
        return response.json()["version"]

    def stop_naming(self, version: dict[str, Any], name: str = "from-netbox") -> None:
        """Register a second version of the configuration that names no credential."""
        response = self.api.client.post(
            f"/configs/{version['config_id']}/versions",
            headers=self.headers(),
            json={"package": _package(name, source=None), "reason": "use another credential"},
        )
        assert response.status_code == 201, response.json()["error"]["code"]
        assert response.json()["version"]["registry_version"] == 2

    def schedule(self, version: dict[str, Any]) -> ScheduleRecord:
        """Store an active schedule that pins this version, as the schedule routes store it."""
        schedule = ScheduleRecord.model_validate(
            {
                "schedule_id": f"s-{uuid4().hex}",
                "name": "from-netbox-nightly",
                "config_id": version["config_id"],
                "registry_version": version["registry_version"],
                "cron": "0 2 * * *",
                "write_target": "default-branch",
                "state": "active",
                "owner": self.api.identifier("root"),
                "created_at": T0,
                "updated_at": T0,
            }
        )
        self.api.store.create_schedule(schedule)
        return schedule

    def execute(self, statement: str, values: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Run one statement on the store's database, for a state or a count that no route gives."""
        databases = sorted(self.root.rglob("*.sqlite3"))
        assert len(databases) == 1
        with sqlite3.connect(databases[0]) as connection:
            return connection.execute(statement, values).fetchall()

    def delete(self, name: str = CREDENTIAL, *, token: str = ADMIN_TOKEN, key: str | None = None) -> Response:
        return _checked(self.api.client.delete(f"/credentials/{name}", headers=self.headers(token, key)))

    def exists(self, name: str = CREDENTIAL) -> bool:
        return self.api.client.get(f"/credentials/{name}", headers=bearer(ADMIN_TOKEN)).status_code == 200

    def start_plan(self, version: dict[str, Any]) -> Response:
        return _checked(
            self.api.client.post(
                "/runs",
                headers=self.headers(),
                json={
                    "operation": "plan",
                    "config_id": version["config_id"],
                    "registry_version": version["registry_version"],
                    "reason": "review the plan",
                },
            )
        )

    def claimed_plan(self, version: dict[str, Any]) -> tuple[str, str]:
        """Create a plan run that a worker claims, and return its identifier and that of its execution."""
        response = self.start_plan(version)
        assert response.status_code in {200, 201, 202}, response.json()["error"]["code"]
        run_id = response.json()["run"]["run_id"]
        run = self.api.store.lookup_run(run_id).value
        assert run is not None
        flow_run_id = run.prefect_executions[-1].flow_run_id
        assert self.api.store.claim_execution(run_id, flow_run_id, worker_id=str(uuid4()))
        return run_id, flow_run_id

    def release(self, run_id: str, flow_run_id: str) -> Response:
        """Ask for the values of one stage execution, as the worker of that stage does."""
        return self.api.client.post(
            f"/worker/runs/{run_id}/credentials", headers=bearer(WORKER_TOKEN), json={"flow_run_id": flow_run_id}
        )

    def credentials_used(self, run_id: str) -> list[dict[str, Any]]:
        response = _checked(self.api.client.get(f"/runs/{run_id}", headers=bearer(ADMIN_TOKEN)))
        assert response.status_code == 200, response.json()["error"]["code"]
        return response.json()["credentials_used"]

    def runs(self) -> int:
        listed = self.api.client.get("/runs", headers=bearer(ADMIN_TOKEN))
        assert listed.status_code == 200
        return len(listed.json()["items"])

    def events(self, event_type: str) -> list[AuditEvent]:
        return [event for event in self.api.events() if event.event_type == event_type]


@pytest.fixture
def deletion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Deletion:
    flow_runs = iter(range(1_000))

    async def submit(  # noqa: RUF029 - it replaces an asynchronous method.
        _self: object, parameters: dict[str, object], *, idempotency_key: str
    ) -> Submission:
        del parameters, idempotency_key
        return Submission(flow_run_id=f"flow-run-{next(flow_runs)}", state="pending")

    # Each run gets its own flow run identifier, as each execution has in a deployment.
    monkeypatch.setattr(account_api.Orchestration, "submit", submit)
    api = build_account_api(tmp_path, monkeypatch, credential_key=TEST_ENCRYPTION_KEY)
    apply_worker_account(api.store, WORKER_TOKEN)
    return Deletion(api, tmp_path)


# --- the refusals of FR-010 ---------------------------------------------------------------


def test_a_deletion_is_refused_while_the_latest_version_of_a_configuration_names_the_credential(
    deletion: Deletion,
) -> None:
    deletion.create_credential()
    version = deletion.register()

    details = _in_use(deletion.delete())

    assert details == {"configurations": [version["config_id"]], "schedules": []}
    assert deletion.exists()
    assert deletion.events(DELETED_EVENT) == []


def test_the_refusal_lists_every_configuration_that_names_the_credential(deletion: Deletion) -> None:
    deletion.create_credential()
    versions = [deletion.register(f"from-netbox-{number}") for number in range(3)]
    deletion.register("from-netbox-other", source=None)

    details = _in_use(deletion.delete())

    assert details["configurations"] == sorted(version["config_id"] for version in versions)


def test_a_deletion_is_refused_while_a_schedule_that_is_not_deleted_pins_a_version_that_names_the_credential(
    deletion: Deletion,
) -> None:
    deletion.create_credential()
    version = deletion.register()
    deletion.stop_naming(version)
    schedule = deletion.schedule(version)

    details = _in_use(deletion.delete())

    # The latest version names no credential: only the schedule, which pins version 1, blocks.
    assert details == {"configurations": [], "schedules": [schedule.name]}
    assert deletion.exists()


def test_a_deleted_schedule_does_not_block_the_deletion(deletion: Deletion) -> None:
    deletion.create_credential()
    version = deletion.register()
    deletion.stop_naming(version)
    schedule = deletion.schedule(version)
    deletion.execute("UPDATE schedules SET state = 'deleted' WHERE schedule_id = ?", (schedule.schedule_id,))

    response = deletion.delete()

    assert response.status_code == 204, response.text
    assert not deletion.exists()


def test_an_older_version_does_not_block_the_deletion_and_a_run_of_it_is_refused_before_it_starts(
    deletion: Deletion,
) -> None:
    deletion.create_credential()
    version = deletion.register()
    deletion.stop_naming(version)

    response = deletion.delete()

    assert response.status_code == 204, response.text
    assert response.content == b""
    assert not deletion.exists()
    assert deletion.execute("SELECT COUNT(*) FROM stored_credentials") == [(0,)]
    assert deletion.execute("SELECT COUNT(*) FROM stored_credential_revisions") == [(0,)]
    # A run of the older version is refused before it starts (FR-008).
    refused = deletion.start_plan(version)
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "credential-not-found"
    assert error["details"] == {"block": "source", "name": CREDENTIAL}
    assert deletion.runs() == 0


# --- what stays after a deletion ------------------------------------------------------------


def test_a_run_keeps_the_name_and_the_revision_it_used_after_the_deletion(deletion: Deletion) -> None:
    deletion.create_credential()
    assert deletion.change_value() == 2
    version = deletion.register()
    run_id, flow_run_id = deletion.claimed_plan(version)
    assert deletion.release(run_id, flow_run_id).status_code == 200
    used = deletion.credentials_used(run_id)
    deletion.stop_naming(version)

    response = deletion.delete()

    assert response.status_code == 204, response.text
    assert used == [{"stage": "plan", "block": "source", "name": CREDENTIAL, "revision": 2}]
    assert deletion.credentials_used(run_id) == used


def test_a_run_of_an_older_version_that_started_before_the_deletion_receives_no_value(deletion: Deletion) -> None:
    deletion.create_credential()
    version = deletion.register()
    deletion.stop_naming(version)
    # The run passed the check at its start: the credential existed.
    run_id, flow_run_id = deletion.claimed_plan(version)
    assert deletion.delete().status_code == 204

    released = _checked(deletion.release(run_id, flow_run_id))

    # The worker route checks the credential again, so the stage gets a refusal and no value.
    assert released.status_code == 422, released.text
    assert released.json()["error"]["code"] == "credential-not-found"
    assert "blocks" not in released.json()
    assert deletion.credentials_used(run_id) == []


def test_a_credential_created_again_with_the_name_starts_at_revision_1(deletion: Deletion) -> None:
    deletion.create_credential()
    deletion.change_value()
    assert deletion.delete().status_code == 204

    deletion.create_credential()

    shown = deletion.api.client.get(f"/credentials/{CREDENTIAL}", headers=bearer(ADMIN_TOKEN))
    assert shown.json()["revision"] == 1


# --- rights, the receipt and the event ----------------------------------------------------


def test_a_deletion_needs_an_administrator(deletion: Deletion) -> None:
    deletion.create_credential()

    response = deletion.delete(token=deletion.api.token("alice"))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert deletion.exists()
    refusals = deletion.events(REFUSED_EVENT)
    assert [(event.operation, event.outcome) for event in refusals] == [("credential-delete", "refused-authorization")]


def test_the_worker_account_cannot_delete_a_credential(deletion: Deletion) -> None:
    deletion.create_credential()

    response = deletion.delete(token=WORKER_TOKEN)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert deletion.exists()


def test_a_deletion_without_a_token_is_refused(deletion: Deletion) -> None:
    deletion.create_credential()

    response = deletion.api.client.delete(f"/credentials/{CREDENTIAL}", headers={"Idempotency-Key": "no-token"})

    assert response.status_code == 401
    assert deletion.exists()


def test_the_deletion_of_a_credential_that_does_not_exist_is_not_found(deletion: Deletion) -> None:
    response = deletion.delete()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "credential-not-found"
    assert deletion.events(DELETED_EVENT) == []


def test_a_repeated_deletion_with_the_same_key_answers_204_again(deletion: Deletion) -> None:
    deletion.create_credential()
    first = deletion.delete(key="delete-netbox-lab-once")

    repeated = deletion.delete(key="delete-netbox-lab-once")
    another = deletion.delete(key="delete-netbox-lab-again")

    assert (first.status_code, repeated.status_code) == (204, 204)
    assert repeated.content == b""
    # A new request finds no credential: the deletion ran once.
    assert another.status_code == 404
    assert len(deletion.events(DELETED_EVENT)) == 1


def test_a_refused_deletion_can_be_sent_again_with_the_same_key_once_nothing_names_the_credential(
    deletion: Deletion,
) -> None:
    deletion.create_credential()
    version = deletion.register()
    _in_use(deletion.delete(key="delete-netbox-lab"))
    deletion.stop_naming(version)

    response = deletion.delete(key="delete-netbox-lab")

    assert response.status_code == 204, response.text
    assert not deletion.exists()


def test_the_deletion_writes_one_event_with_the_name_and_the_last_revision(deletion: Deletion) -> None:
    deletion.create_credential()
    assert deletion.change_value() == 2

    assert deletion.delete().status_code == 204

    (event,) = deletion.events(DELETED_EVENT)
    assert event.operation == "credential-delete"
    assert event.outcome == "accepted"
    assert event.actor == deletion.api.identifier("admin")
    assert ("credential", CREDENTIAL) in [(resource.kind, resource.id) for resource in event.related]
    assert event.details is not None
    assert event.details.revision == 2
    holds_a_value = MARKER_PREFIX in deletion.api.stored_text()
    assert not holds_a_value


def test_the_receipt_of_a_deletion_names_the_credential(deletion: Deletion) -> None:
    deletion.create_credential()

    assert deletion.delete().status_code == 204

    receipts = deletion.execute(
        "SELECT resource_kind, resource_id, state, response_status FROM mutation_receipts "
        "WHERE operation = 'credential-delete'"
    )
    assert receipts == [("credential", CREDENTIAL, "accepted", 204)]


# --- FR-011: the name cannot change ---------------------------------------------------------


def test_no_route_renames_a_credential(deletion: Deletion) -> None:
    paths = deletion.api.client.get("/openapi.json").json()["paths"]
    routes = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/credentials")
        for method in operations
    }

    # Create, list, view, change the value, change the description and delete: nothing else.
    assert routes == {
        ("GET", "/credentials"),
        ("POST", "/credentials"),
        ("GET", "/credentials/{name}"),
        ("PATCH", "/credentials/{name}"),
        ("DELETE", "/credentials/{name}"),
        ("PUT", "/credentials/{name}/value"),
    }


def test_a_change_of_the_description_that_also_sends_a_name_is_refused(deletion: Deletion) -> None:
    deletion.create_credential()

    response = deletion.api.client.patch(
        f"/credentials/{CREDENTIAL}",
        headers=deletion.headers(),
        json={"name": "netbox-renamed", "description": "another name"},
    )

    assert response.status_code == 422
    assert deletion.exists()
    assert not deletion.exists("netbox-renamed")
