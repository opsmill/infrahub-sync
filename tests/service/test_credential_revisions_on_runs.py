"""The credential revisions that the detail of a run shows (spec 027, User Story 2, FR-003, SC-002, research R9).

``GET /runs/{run_id}`` answers ``credentials_used``: one entry for each block whose values a
stage of the run received, with the stage, the block, the name of the credential and its
revision. The entries come from ``run_credential_uses``, which the worker route writes when it
releases the values.

Each case changes a value through ``PUT /credentials/{name}/value`` and releases through
``POST /worker/runs/{run_id}/credentials``, so every revision in an answer is one the Sync API
recorded. Every value is a marker text. A check compares a boolean, so a failed check prints no
value.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")
pytest.importorskip("cryptography")

from infrahub_sync.product_store import PrefectExecutionLink
from infrahub_sync.service.auth import apply_worker_account
from infrahub_sync.service.orchestration import Submission
from tests.configuration.validation_packages import package_data
from tests.helpers.removed_credential_format import store_in_removed_format
from tests.helpers.stored_credentials import TEST_ENCRYPTION_KEY
from tests.service import account_api
from tests.service.account_api import ADMIN_TOKEN, AccountApi, bearer, build_account_api
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from pathlib import Path

WORKER_TOKEN = "worker-token-of-the-environment-1"  # noqa: S105 - a test value, not a secret.
SHARED_CREDENTIAL = "netbox-lab"
DESTINATION_CREDENTIAL = "infrahub-main"
MARKER_PREFIX = "revision-marker-value-"
PACKAGES = 5
ENTRY_KEYS = ["block", "name", "revision", "stage"]


def _package(name: str, *, source: str | None, destination: str | None) -> dict[str, Any]:
    """Return a package with this configuration name, whose blocks name these credentials or none."""
    data = package_data()
    data["configuration"]["name"] = name
    for block, credential in (("source", source), ("destination", destination)):
        if credential is not None:
            data["configuration"][block]["credential"] = credential
    return data


class Runs:
    """The API with a ``worker`` account, and the steps from a credential to the detail of a run."""

    def __init__(self, api: AccountApi, tmp_path: Path) -> None:
        self.api = api
        self.root = tmp_path.resolve()
        self._keys = 0
        self._values = 0

    def _headers(self) -> dict[str, str]:
        self._keys += 1
        return {**bearer(ADMIN_TOKEN), "Idempotency-Key": f"revisions-on-runs-{self._keys}"}

    def _value(self) -> dict[str, str]:
        self._values += 1
        return {"token": f"{MARKER_PREFIX}{self._values}"}

    def create_credential(self, name: str) -> None:
        response = self.api.client.post("/credentials", headers=self._headers(), json={"name": name, **self._value()})
        assert response.status_code == 201, response.json()["error"]["code"]

    def change_value(self, name: str) -> int:
        """Change the value of a credential with one request, and return the new revision."""
        response = self.api.client.put(f"/credentials/{name}/value", headers=self._headers(), json=self._value())
        assert response.status_code == 200, response.json()["error"]["code"]
        return response.json()["revision"]

    def register(self, name: str, *, source: str | None, destination: str | None) -> dict[str, Any]:
        response = self.api.client.post(
            "/configs",
            headers=self._headers(),
            json={"package": _package(name, source=source, destination=destination), "reason": "register"},
        )
        assert response.status_code == 201, response.json()["error"]["code"]
        return response.json()["version"]

    def plan(self, version: dict[str, Any]) -> tuple[str, str]:
        """Create a plan run that a worker claims, and return its identifier and that of its execution."""
        response = self.api.client.post(
            "/runs",
            headers=self._headers(),
            json={
                "operation": "plan",
                "config_id": version["config_id"],
                "registry_version": version["registry_version"],
                "reason": "review the plan",
            },
        )
        assert response.status_code in {200, 201, 202}, response.json()["error"]["code"]
        run_id = response.json()["run"]["run_id"]
        run = self.api.store.lookup_run(run_id).value
        assert run is not None
        flow_run_id = run.prefect_executions[-1].flow_run_id
        self.claim(run_id, flow_run_id)
        return run_id, flow_run_id

    def claim(self, run_id: str, flow_run_id: str) -> None:
        assert self.api.store.claim_execution(run_id, flow_run_id, worker_id=str(uuid4()))

    def release(self, run_id: str, flow_run_id: str) -> None:
        """Release the values of one stage execution, as the worker of that stage asks for them."""
        # A later release has a later time, so the entries of a run keep the order of its stages.
        self.api.clock.now += timedelta(seconds=1)
        response = self.api.client.post(
            f"/worker/runs/{run_id}/credentials", headers=bearer(WORKER_TOKEN), json={"flow_run_id": flow_run_id}
        )
        assert response.status_code == 200, response.json()["error"]["code"]

    def planned_and_released(self, version: dict[str, Any]) -> str:
        run_id, flow_run_id = self.plan(version)
        self.release(run_id, flow_run_id)
        return run_id

    def detail(self, run_id: str) -> dict[str, Any]:
        """Return the detail of a run, after the check that it holds no value."""
        response = self.api.client.get(f"/runs/{run_id}", headers=bearer(ADMIN_TOKEN))
        assert response.status_code == 200, response.json()["error"]["code"]
        holds_a_value = MARKER_PREFIX in response.text
        assert not holds_a_value
        return response.json()

    def credentials_used(self, run_id: str) -> list[dict[str, Any]]:
        return self.detail(run_id)["credentials_used"]

    def version_count(self, version: dict[str, Any]) -> int:
        return len(self.api.store.list_configuration_versions(version["config_id"]))

    def execute(self, statement: str, values: tuple[Any, ...] = ()) -> None:
        """Run one statement on the store's database, for a state that no route reaches."""
        databases = sorted(self.root.rglob("*.sqlite3"))
        assert len(databases) == 1
        with sqlite3.connect(databases[0]) as connection:
            connection.execute(statement, values)


@pytest.fixture
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Runs:
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
    return Runs(api, tmp_path)


def _entry(stage: str, block: str, name: str, revision: int) -> dict[str, Any]:
    return {"stage": stage, "block": block, "name": name, "revision": revision}


def test_one_change_of_a_value_reaches_the_next_run_of_five_packages_without_a_new_version(runs: Runs) -> None:
    runs.create_credential(SHARED_CREDENTIAL)
    versions = [
        runs.register(f"from-netbox-{number}", source=SHARED_CREDENTIAL, destination=None) for number in range(PACKAGES)
    ]
    before = [runs.credentials_used(runs.planned_and_released(version)) for version in versions]

    revision = runs.change_value(SHARED_CREDENTIAL)

    after = [runs.credentials_used(runs.planned_and_released(version)) for version in versions]
    assert revision == 2
    assert before == [[_entry("plan", "source", SHARED_CREDENTIAL, 1)]] * PACKAGES
    assert after == [[_entry("plan", "source", SHARED_CREDENTIAL, 2)]] * PACKAGES
    assert [runs.version_count(version) for version in versions] == [1] * PACKAGES


def test_an_apply_after_a_change_shows_its_own_revision_and_the_plan_keeps_the_one_it_read(runs: Runs) -> None:
    runs.create_credential(SHARED_CREDENTIAL)
    runs.create_credential(DESTINATION_CREDENTIAL)
    runs.change_value(DESTINATION_CREDENTIAL)
    assert runs.change_value(DESTINATION_CREDENTIAL) == 3
    version = runs.register("from-netbox", source=SHARED_CREDENTIAL, destination=DESTINATION_CREDENTIAL)
    run_id, plan_flow_run_id = runs.plan(version)
    runs.release(run_id, plan_flow_run_id)
    # The plan stage ends: a run admits a write only when none of its executions is in progress.
    runs.execute(
        "UPDATE prefect_executions SET terminal_at = ?, terminal_state = 'completed', terminal_outcome = 'succeeded' "
        "WHERE run_id = ? AND flow_run_id = ?",
        (runs.api.clock.now.isoformat(), run_id, plan_flow_run_id),
    )

    assert runs.change_value(DESTINATION_CREDENTIAL) == 4
    apply = append_execution(
        runs.api.store, run_id, PrefectExecutionLink(flow_run_id="flow-run-of-the-apply", purpose="apply", attempt=1)
    )
    runs.claim(run_id, apply.flow_run_id)
    runs.release(run_id, apply.flow_run_id)

    used = runs.credentials_used(run_id)
    assert used == [
        _entry("plan", "destination", DESTINATION_CREDENTIAL, 3),
        _entry("plan", "source", SHARED_CREDENTIAL, 1),
        # An apply connects to the destination only.
        _entry("apply", "destination", DESTINATION_CREDENTIAL, 4),
    ]
    assert [sorted(entry) for entry in used] == [ENTRY_KEYS] * 3


def test_a_run_of_a_package_that_names_no_credential_shows_none(runs: Runs) -> None:
    version = runs.register("from-netbox", source=None, destination=None)
    run_id, _ = runs.plan(version)

    assert runs.credentials_used(run_id) == []


def test_a_run_of_a_version_in_the_removed_format_shows_none(runs: Runs) -> None:
    version = runs.register("from-netbox", source=None, destination=None)
    run_id, _ = runs.plan(version)
    store_in_removed_format(
        runs.root,
        version["config_id"],
        version["registry_version"],
        _package("from-netbox", source=None, destination=None),
    )

    assert runs.credentials_used(run_id) == []


def test_a_credential_that_no_longer_exists_keeps_its_name_and_revision_on_the_run(runs: Runs) -> None:
    runs.create_credential(SHARED_CREDENTIAL)
    version = runs.register("from-netbox", source=SHARED_CREDENTIAL, destination=None)
    run_id = runs.planned_and_released(version)

    # No route deletes a credential that the latest version of a configuration names.
    runs.execute(
        "DELETE FROM stored_credential_revisions WHERE credential_id IN "
        "(SELECT credential_id FROM stored_credentials WHERE name = ?)",
        (SHARED_CREDENTIAL,),
    )
    runs.execute("DELETE FROM stored_credentials WHERE name = ?", (SHARED_CREDENTIAL,))

    assert runs.api.store.lookup_stored_credential(SHARED_CREDENTIAL) is None
    assert runs.credentials_used(run_id) == [_entry("plan", "source", SHARED_CREDENTIAL, 1)]
