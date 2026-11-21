"""The worker route that reads the states of a schedule's earlier proposed changes (spec 027, FR-004).

``POST /worker/schedules/{schedule_id}/proposed-change-states`` answers the ``worker`` account
only. A scheduled start with branch review reads these states before its run exists, so the
worker cannot receive the destination token for it. The Sync API decrypts the token, reads each
proposed change from Infrahub on ``main``, and answers with the state of each run's proposed
change. The answer holds no value, no address and no token.

The destination address and the proposed-change identifiers come from the stored version and
the stored branch rows of the schedule. The request has no body that the route reads.

Every value is a marker text. A check compares digests or a boolean, so a failed check prints no
value.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")
pytest.importorskip("cryptography")
pytest.importorskip("opsmill_prefect_extras")

from fastapi.testclient import TestClient
from infrahub_sdk.exceptions import AuthenticationError, ServerNotReachableError
from starlette.applications import Starlette
from starlette.routing import Mount
from structlog.testing import capture_logs

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.product_store import ScheduleRecord, local_product_projection
from infrahub_sync.service import branch_review, credential_client
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import StoreAccountResolver, apply_worker_account
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.credential_crypto import CredentialCipher
from infrahub_sync.service.schedule_admission import admit_scheduled_start
from infrahub_sync.service.service import RunService
from infrahub_sync.service.settings import WorkerSettings
from tests.configuration.validation_packages import SOURCE_CREDENTIAL
from tests.helpers.stored_credentials import (
    TEST_ENCRYPTION_KEY,
    create_stored_credential,
    create_stored_credential_names,
)
from tests.service.accounts import IDLE_SECONDS, seed_accounts
from tests.service.schedule_fixtures import (
    ACCOUNTS,
    ADMIN_TOKEN,
    OWNER_TOKEN,
    WORKER_ID,
    FakeOrchestration,
    ScheduleApi,
    package,
)

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.product_store import AuditEvent, ProductProjection

WORKER_TOKEN = "worker-token-of-the-environment-1"  # noqa: S105 - a test value, not a secret.
DESTINATION_TOKEN = "states-marker-destination-token-52c7"  # noqa: S105 - a test value, not a secret.
API_PROCESS_TOKEN = "states-marker-api-process-token-91d4"  # noqa: S105 - a test value, not a secret.
MARKERS = (DESTINATION_TOKEN, API_PROCESS_TOKEN)
DESTINATION_CREDENTIAL = "infrahub-main"
STORED_ADDRESS = "https://infrahub.example"
OTHER_ADDRESS = "https://attacker.example"
SCHEDULE_ID = "s-0123456789abcdef0123456789abcdef"
OTHER_SCHEDULE_ID = "s-fedcba9876543210fedcba9876543210"
T0 = datetime(2026, 3, 2, 2, tzinfo=timezone.utc)
OTHER_KEY = bytes(range(32, 64))


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def _holds_a_value(text: str) -> bool:
    """Return whether the text holds a marker value; the caller asserts the boolean."""
    return any(marker in text for marker in MARKERS)


def _package(name: str = "from-netbox", *, stored: bool = True) -> ConfigurationPackage:
    """Return a package whose destination names the stored credential, or no credential at all."""
    data = package(name).model_dump(mode="json")
    if stored:
        data["configuration"]["destination"]["credential"] = DESTINATION_CREDENTIAL
    else:
        data["configuration"]["destination"].pop("credential")
    return ConfigurationPackage.model_validate(data)


@dataclass
class Infrahub:
    """The Infrahub of the destination: the state of each proposed change, and what each client was built with."""

    states: dict[str, str] = field(default_factory=dict)
    approved: set[str] = field(default_factory=set)
    failure: BaseException | None = None
    clients: list[tuple[str, str]] = field(default_factory=list)
    requests: list[tuple[str, str | None]] = field(default_factory=list)

    def execute_graphql(
        self, query: str, variables: dict | None = None, branch_name: str | None = None, **_: str | None
    ) -> dict[str, Any]:
        del query
        identifier = (variables or {})["id"]
        self.requests.append((identifier, branch_name))
        if self.failure is not None:
            raise self.failure
        if identifier not in self.states:
            return {"CoreProposedChange": {"edges": []}}
        approvers = [{"node": {"id": "account-1"}}] if identifier in self.approved else []
        node = {"id": identifier, "state": {"value": self.states[identifier]}, "approved_by": {"edges": approvers}}
        return {"CoreProposedChange": {"edges": [{"node": node}]}}


@dataclass
class States:
    """The Sync API with a ``worker`` account, one schedule with branch review, and the fake Infrahub."""

    api: ScheduleApi
    infrahub: Infrahub
    tmp_path: Path
    served: TestClient

    @property
    def store(self) -> ProductProjection:
        return self.api.projection

    def schedule(self, schedule_id: str = SCHEDULE_ID, *, config_id: str | None = None) -> ScheduleRecord:
        created = T0 - timedelta(minutes=30)
        record = ScheduleRecord.model_validate(
            {
                "schedule_id": schedule_id,
                "name": f"nightly-{schedule_id[-4:]}",
                "config_id": config_id or self.api.config_id,
                "registry_version": 1,
                "cron": "0 2 * * *",
                "timezone": "UTC",
                "write_target": "branch-review",
                "state": "active",
                "owner": self.api.account_id("owner"),
                "created_at": created,
                "updated_at": created,
            }
        )
        self.store.create_schedule(record)
        return record

    def earlier_run(self, proposed_change_id: str | None, *, day: int = 0, schedule_id: str = SCHEDULE_ID) -> str:
        """Store one finished run of the schedule; with an identifier, its branch row is ``open``."""
        run_id = self.api.scheduled_run(schedule_id, T0 + timedelta(days=day))
        self.execute(
            "UPDATE prefect_executions SET terminal_at = ?, terminal_state = 'completed', "
            "terminal_outcome = 'succeeded' WHERE run_id = ?",
            ((T0 + timedelta(days=day, minutes=5)).isoformat(), run_id),
        )
        if proposed_change_id is not None:
            row = self.store.lookup_schedule_branch(run_id).value
            assert row is not None
            self.store.update_schedule_branch(
                row.model_copy(update={"proposed_change_id": proposed_change_id, "state": "open"})
            )
        return run_id

    def execute(self, statement: str, values: tuple[Any, ...] = ()) -> None:
        """Run one statement on the store's database, for a state the store API reaches only through a run."""
        databases = sorted(self.tmp_path.rglob("*.sqlite3"))
        assert len(databases) == 1
        with sqlite3.connect(databases[0]) as connection:
            connection.execute(statement, values)

    def delete_credential(self) -> None:
        self.execute(
            "DELETE FROM stored_credential_revisions WHERE credential_id IN "
            "(SELECT credential_id FROM stored_credentials WHERE name = ?)",
            (DESTINATION_CREDENTIAL,),
        )
        self.execute("DELETE FROM stored_credentials WHERE name = ?", (DESTINATION_CREDENTIAL,))

    def read(
        self,
        schedule_id: str = SCHEDULE_ID,
        *,
        token: str | None = WORKER_TOKEN,
        body: dict[str, Any] | None = None,
    ) -> Response:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        return self.api.client.post(
            f"/worker/schedules/{schedule_id}/proposed-change-states", headers=headers, json=body
        )

    def events(self) -> list[AuditEvent]:
        return list(self.store.audit_events())

    def stored_text(self) -> str:
        databases = sorted(self.tmp_path.rglob("*.sqlite3"))
        with sqlite3.connect(databases[0]) as connection:
            return "\n".join(connection.iterdump())


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, key: bytes | None = TEST_ENCRYPTION_KEY) -> States:
    store = local_product_projection(tmp_path.resolve())
    seed_accounts(store, ACCOUNTS, environment_token=True)
    apply_worker_account(store, WORKER_TOKEN)
    create_stored_credential(store, DESTINATION_CREDENTIAL, {"token": DESTINATION_TOKEN})
    # The source of the shared package names a credential too; no test of this file reads its value.
    create_stored_credential_names(store, SOURCE_CREDENTIAL)
    config_id = store.create_configuration(_package()).config_id
    resolver = StoreAccountResolver(
        store, idle_seconds=IDLE_SECONDS, admin_token=ADMIN_TOKEN, worker_token=WORKER_TOKEN
    )
    orchestration = FakeOrchestration()
    service = RunService(store, orchestration, secrets=resolver.secret_values)
    routes = ConfigurationRoutes(product_projection=store, secrets=resolver.secret_values)
    cipher = None if key is None else CredentialCipher(key)
    app = create_app(service, resolver, routes, credential_cipher=cipher)
    infrahub = Infrahub()
    build_client = branch_review.build_client

    def fake_client(settings: Any, branch: str) -> Infrahub:  # noqa: ANN401 - the destination settings.
        # The real client is built, with no request, to read the address and the token it would use.
        client = build_client(settings, branch)
        infrahub.clients.append((client.address, _digest(client.config.api_token)))
        return infrahub

    monkeypatch.setattr(branch_review, "build_client", fake_client)
    api = ScheduleApi(store, orchestration, cast("Any", None), app, TestClient(app), config_id)
    served = TestClient(Starlette(routes=[Mount("/api", app=app)]))
    return States(api=api, infrahub=infrahub, tmp_path=tmp_path, served=served)


@pytest.fixture
def states(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> States:
    built = _build(tmp_path, monkeypatch)
    built.schedule()
    return built


def _refused(response: Response, status: int, code: str) -> dict[str, Any]:
    """Check a refusal and that it holds no value, and return its error."""
    leaked = _holds_a_value(response.text)
    assert not leaked
    assert response.status_code == status, response.json()["error"]["code"]
    error = response.json()["error"]
    assert error["code"] == code
    return error


# --- the answer ---------------------------------------------------------------------------


def test_the_worker_receives_the_state_of_each_earlier_proposed_change_and_no_value(states: States) -> None:
    first = states.earlier_run("pc-1")
    second = states.earlier_run("pc-2", day=1)
    third = states.earlier_run("pc-3", day=2)
    states.infrahub.states = {"pc-1": "open", "pc-2": "merged"}
    states.infrahub.approved = {"pc-1"}

    response = states.read()

    assert response.status_code == 200
    assert response.json() == {"states": {first: "approved", second: "merged", third: "missing"}}
    leaked = _holds_a_value(response.text) or STORED_ADDRESS in response.text or "pc-1" in response.text
    assert not leaked


def test_the_read_uses_the_stored_address_the_decrypted_token_and_the_default_branch(states: States) -> None:
    states.earlier_run("pc-1")
    states.infrahub.states = {"pc-1": "open"}

    states.read()

    assert states.infrahub.clients == [(STORED_ADDRESS, _digest(DESTINATION_TOKEN))]
    assert states.infrahub.requests == [("pc-1", "main")]


def test_a_branch_row_without_a_proposed_change_or_in_a_final_state_is_not_read(states: States) -> None:
    states.earlier_run(None)
    kept = states.earlier_run("pc-2", day=1)
    row = states.store.lookup_schedule_branch(kept).value
    assert row is not None
    states.store.update_schedule_branch(row.model_copy(update={"state": "kept"}))
    states.infrahub.states = {"pc-2": "merged"}

    response = states.read()

    assert (response.status_code, response.json()) == (200, {"states": {}})
    assert states.infrahub.requests == []


def test_the_rows_of_another_schedule_are_not_read(states: States) -> None:
    # A configuration has one schedule, so the other schedule has its own configuration.
    other = states.store.create_configuration(_package("from-netbox-other")).config_id
    states.schedule(OTHER_SCHEDULE_ID, config_id=other)
    mine = states.earlier_run("pc-1")
    states.earlier_run("pc-9", day=1, schedule_id=OTHER_SCHEDULE_ID)
    states.infrahub.states = {"pc-1": "open", "pc-9": "open"}

    response = states.read()

    assert response.json() == {"states": {mine: "open"}}
    assert states.infrahub.requests == [("pc-1", "main")]


# --- the request cannot choose what is read ------------------------------------------------


def test_a_request_body_cannot_change_the_address_or_the_proposed_changes(states: States) -> None:
    run_id = states.earlier_run("pc-1")
    states.infrahub.states = {"pc-1": "open", "pc-7": "merged"}

    response = states.read(
        body={
            "url": OTHER_ADDRESS,
            "address": OTHER_ADDRESS,
            "destination": {"settings": {"url": OTHER_ADDRESS}},
            "proposed_change_ids": ["pc-7"],
            "branch": "other",
        }
    )

    assert response.json() == {"states": {run_id: "open"}}
    assert states.infrahub.clients == [(STORED_ADDRESS, _digest(DESTINATION_TOKEN))]
    assert states.infrahub.requests == [("pc-1", "main")]


def test_the_variables_of_the_api_process_change_neither_the_address_nor_the_token(
    states: States, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", API_PROCESS_TOKEN)
    monkeypatch.setenv("INFRAHUB_ADDRESS", OTHER_ADDRESS)
    monkeypatch.setenv("INFRAHUB_URL", OTHER_ADDRESS)
    states.earlier_run("pc-1")
    states.infrahub.states = {"pc-1": "open"}

    states.read()

    assert states.infrahub.clients == [(STORED_ADDRESS, _digest(DESTINATION_TOKEN))]


def test_a_destination_that_names_no_stored_credential_is_refused_before_any_read(
    states: States, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INFRAHUB_API_TOKEN", API_PROCESS_TOKEN)
    config_id = states.store.create_configuration(_package("from-netbox-other", stored=False)).config_id
    states.schedule(OTHER_SCHEDULE_ID, config_id=config_id)
    states.earlier_run("pc-1", schedule_id=OTHER_SCHEDULE_ID)

    response = states.read(OTHER_SCHEDULE_ID)

    _refused(response, 422, "destination-credential-not-named")
    assert states.infrahub.clients == []


# --- who may ask ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", [ADMIN_TOKEN, OWNER_TOKEN], ids=("administrator", "account"))
def test_every_account_but_the_worker_is_forbidden(states: States, token: str) -> None:
    states.earlier_run("pc-1")

    response = states.read(token=token)

    _refused(response, 403, "forbidden")
    assert states.infrahub.clients == []


def test_a_request_without_a_token_is_unauthenticated(states: States) -> None:
    response = states.read(token=None)

    _refused(response, 401, "unauthenticated")


@pytest.mark.parametrize("path", ["/credentials", "/runs", "/events", "/me"])
def test_the_worker_is_still_refused_on_the_routes_for_accounts(states: States, path: str) -> None:
    response = states.api.client.get(path, headers={"Authorization": f"Bearer {WORKER_TOKEN}"})

    _refused(response, 403, "forbidden")


# --- refusals ------------------------------------------------------------------------------


def test_an_unknown_schedule_is_not_found(states: States) -> None:
    response = states.read(OTHER_SCHEDULE_ID)

    _refused(response, 404, "schedule-not-found")


def test_a_missing_credential_is_refused_with_the_code_of_the_release_route(states: States) -> None:
    states.earlier_run("pc-1")
    states.delete_credential()

    error = _refused(states.read(), 422, "credential-not-found")

    assert error["details"] == {"block": "destination", "name": DESTINATION_CREDENTIAL}
    assert states.infrahub.clients == []


def test_a_credential_without_the_token_is_refused_and_names_the_field(states: States) -> None:
    states.earlier_run("pc-1")
    create_stored_credential(states.store, DESTINATION_CREDENTIAL, {"username": "sync", "password": DESTINATION_TOKEN})

    error = _refused(states.read(), 422, "credential-field-missing")

    assert error["details"] == {"block": "destination", "name": DESTINATION_CREDENTIAL, "fields": ["token"]}
    assert states.infrahub.clients == []


def test_a_value_that_the_current_key_cannot_decrypt_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    states = _build(tmp_path, monkeypatch, key=OTHER_KEY)
    states.schedule()
    states.earlier_run("pc-1")

    error = _refused(states.read(), 500, "credential-undecryptable")

    assert error["details"] == {"block": "destination", "name": DESTINATION_CREDENTIAL}
    assert states.infrahub.clients == []


def test_the_route_answers_503_when_no_key_is_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    states = _build(tmp_path, monkeypatch, key=None)
    states.schedule()
    states.earlier_run("pc-1")

    _refused(states.read(), 503, "credential-key-not-configured")


@pytest.mark.parametrize(
    ("failure", "failure_class"),
    [
        (ServerNotReachableError(address=f"{STORED_ADDRESS}/?token={DESTINATION_TOKEN}"), "system_unreachable"),
        (AuthenticationError(f"the token {DESTINATION_TOKEN} was refused"), "token_rejected"),
    ],
    ids=("unreachable", "token-refused"),
)
def test_a_failed_read_of_infrahub_answers_its_failure_class_and_never_its_text(
    states: States, failure: BaseException, failure_class: str
) -> None:
    states.earlier_run("pc-1")
    states.infrahub.failure = failure

    with capture_logs() as logs:
        response = states.read()

    error = _refused(response, 424, "proposed-change-read-failed")
    assert error["details"] == {"failure": failure_class}
    assert STORED_ADDRESS not in response.text
    logged = _holds_a_value(repr(logs))
    assert not logged


def test_an_answer_of_infrahub_that_is_not_a_state_is_a_failed_read(states: States) -> None:
    states.earlier_run("pc-1")
    states.infrahub.states = {"pc-1": "a-state-that-infrahub-does-not-define"}

    error = _refused(states.read(), 424, "proposed-change-read-failed")

    assert error["details"] == {"failure": "proposed_change_read_failed"}


# --- what the read leaves behind -----------------------------------------------------------


def test_the_read_writes_no_receipt_no_event_and_no_stored_value(states: States) -> None:
    states.earlier_run("pc-1")
    states.infrahub.states = {"pc-1": "open"}
    before = [event.event_type for event in states.events()]

    with capture_logs() as logs:
        first = states.read()
        second = states.api.client.post(
            f"/worker/schedules/{SCHEDULE_ID}/proposed-change-states",
            headers={"Authorization": f"Bearer {WORKER_TOKEN}", "Idempotency-Key": "ignored-key"},
        )

    assert first.json() == second.json()
    assert [event.event_type for event in states.events()] == before
    leaked = _holds_a_value(states.stored_text()) or _holds_a_value(repr(logs))
    assert not leaked
    (line,) = [entry for entry in logs if entry["event"] == "credential.used_for_proposed_change_read"][:1]
    assert (line["schedule_id"], line["name"], line["revision"]) == (SCHEDULE_ID, DESTINATION_CREDENTIAL, 1)


# --- the admission of a scheduled start, through the worker's client and the served route -----


def _admit(states: States, monkeypatch: pytest.MonkeyPatch, start: datetime) -> Any:  # noqa: ANN401 - StartAdmission.
    """Decide on one start as the worker does: the reader of the flow, the real client, the served route."""
    transport = states.served._transport
    read = partial(
        credential_client.read_schedule_proposed_change_states,
        settings=WorkerSettings(api_url="http://testserver", token=WORKER_TOKEN),
        sleep=lambda _seconds: None,
        transport=transport,
    )
    monkeypatch.setattr(service_flow, "read_schedule_proposed_change_states", read)
    return admit_scheduled_start(
        states.store,
        schedule_id=SCHEDULE_ID,
        scheduled_start_at=start,
        flow_run_id=f"flow-{start:%Y%m%d}",
        worker_id=WORKER_ID,
        now=start + timedelta(seconds=5),
        receipt_age_limit=timedelta(seconds=60),
        read_proposed_changes=service_flow._earlier_proposed_changes_reader(states.store),
    )


def _schedule_events(states: States, suffix: str) -> list[AuditEvent]:
    return [event for event in states.events() if event.event_type == f"infrahub_sync.schedule.{suffix}"]


def test_a_start_with_a_stored_destination_credential_is_skipped_for_an_approved_proposed_change(
    states: States, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = states.earlier_run("pc-1")
    states.infrahub.states = {"pc-1": "open"}
    states.infrahub.approved = {"pc-1"}

    admission = _admit(states, monkeypatch, T0 + timedelta(days=1))

    assert (admission.run, admission.cause) == (None, "skipped-approved-change-open")
    (skipped,) = _schedule_events(states, "start_skipped")
    assert (skipped.outcome, skipped.run_id) == ("skipped-approved-change-open", first)
    assert states.infrahub.clients == [(STORED_ADDRESS, _digest(DESTINATION_TOKEN))]


def test_a_start_with_a_stored_destination_credential_keeps_a_merged_proposed_change_and_runs(
    states: States, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = states.earlier_run("pc-1")
    states.infrahub.states = {"pc-1": "merged"}

    admission = _admit(states, monkeypatch, T0 + timedelta(days=1))

    assert admission.run is not None
    row = states.store.lookup_schedule_branch(first).value
    assert row is not None
    assert row.state == "kept"
    (kept,) = _schedule_events(states, "proposed_change_kept")
    assert kept.outcome == "kept-merged"


def test_a_start_with_a_stored_destination_credential_fails_with_the_class_of_a_failed_read(
    states: States, monkeypatch: pytest.MonkeyPatch
) -> None:
    states.earlier_run("pc-1")
    states.infrahub.failure = ServerNotReachableError(address=f"{STORED_ADDRESS}/?token={DESTINATION_TOKEN}")

    with capture_logs() as logs:
        admission = _admit(states, monkeypatch, T0 + timedelta(days=1))

    assert (admission.run, admission.cause) == (None, "failed")
    (failed,) = _schedule_events(states, "start_failed")
    assert failed.details is not None
    assert failed.details.cause == "system_unreachable"
    leaked = _holds_a_value(repr(logs)) or _holds_a_value(states.stored_text())
    assert not leaked


def test_a_start_fails_with_the_credential_cause_when_the_credential_was_deleted(
    states: States, monkeypatch: pytest.MonkeyPatch
) -> None:
    states.earlier_run("pc-1")
    states.delete_credential()

    admission = _admit(states, monkeypatch, T0 + timedelta(days=1))

    assert (admission.run, admission.cause) == (None, "failed")
    (failed,) = _schedule_events(states, "start_failed")
    assert failed.details is not None
    assert failed.details.cause == "credential_unavailable"


def test_a_start_fails_and_creates_no_run_when_the_answer_lacks_the_state_of_a_row(
    states: States, monkeypatch: pytest.MonkeyPatch
) -> None:
    states.earlier_run("pc-1")
    states.infrahub.states = {"pc-1": "open"}
    monkeypatch.setattr(service_flow, "read_schedule_proposed_change_states", lambda _schedule_id: {})

    admission = admit_scheduled_start(
        states.store,
        schedule_id=SCHEDULE_ID,
        scheduled_start_at=T0 + timedelta(days=1),
        flow_run_id="flow-incomplete",
        worker_id=WORKER_ID,
        now=T0 + timedelta(days=1, seconds=5),
        receipt_age_limit=timedelta(seconds=60),
        read_proposed_changes=service_flow._earlier_proposed_changes_reader(states.store),
    )

    # A row with no state must not read as "not approved": the start fails and no run replaces it.
    assert (admission.run, admission.cause) == (None, "failed")
    (failed,) = _schedule_events(states, "start_failed")
    assert failed.details is not None
    assert failed.details.cause == "proposed-change-states-incomplete"
