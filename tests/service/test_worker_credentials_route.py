"""The worker route that releases stored credential values (spec 027, contracts/http-api.md, research R5).

``POST /worker/runs/{run_id}/credentials`` is the only route whose answer holds a value. It
answers the ``worker`` account only, for an execution that a worker claimed and that has not
ended, with the blocks the stage of that execution needs (FR-004, FR-005, FR-016).

Every value is a marker text. A check compares digests or a boolean, so a failed check prints no
value. The stage of an execution is its ``purpose``; the cases of the write stages set it in the
store directly, because the route reads only that column and the admission of a write is not
what these cases check.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")
pytest.importorskip("cryptography")

from structlog.testing import capture_logs

from infrahub_sync.service.auth import apply_worker_account
from infrahub_sync.service.credential_crypto import CredentialCipher
from infrahub_sync.service.orchestration import Submission
from tests.configuration.validation_packages import package_data
from tests.helpers.stored_credentials import TEST_ENCRYPTION_KEY, create_stored_credential
from tests.service import account_api
from tests.service.account_api import ADMIN_TOKEN, AccountApi, bearer, build_account_api

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.product_store import AuditEvent

WORKER_TOKEN = "worker-token-of-the-environment-1"  # noqa: S105 - a test value, not a secret.
SOURCE_TOKEN = "released-marker-source-token-4d1a"  # noqa: S105 - a test value, not a secret.
DESTINATION_TOKEN = "released-marker-destination-token-8e2b"  # noqa: S105 - a test value, not a secret.
MARKERS = (SOURCE_TOKEN, DESTINATION_TOKEN)
SOURCE_CREDENTIAL = "netbox-lab"
DESTINATION_CREDENTIAL = "infrahub-main"
RELEASED = "infrahub_sync.credential.released"
OTHER_KEY = bytes(range(32, 64))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _holds_a_value(text: str) -> bool:
    """Return whether the text holds a marker value; the caller asserts the boolean."""
    return any(marker in text for marker in MARKERS)


def _package() -> dict[str, Any]:
    """Return a package whose source and destination each name a stored credential."""
    data = package_data()
    data.pop("credentials", None)
    for block, name in (("source", SOURCE_CREDENTIAL), ("destination", DESTINATION_CREDENTIAL)):
        data["configuration"][block]["settings"].pop("token", None)
        data["configuration"][block]["credential"] = name
    return data


class Released:
    """The API with a ``worker`` account, and the steps that bring a run to a claimed execution."""

    def __init__(self, api: AccountApi, tmp_path: Path) -> None:
        self.api = api
        self._tmp_path = tmp_path
        self._version: dict[str, Any] | None = None
        self._runs = 0

    def store_credentials(self) -> None:
        create_stored_credential(self.api.store, SOURCE_CREDENTIAL, {"token": SOURCE_TOKEN})
        create_stored_credential(self.api.store, DESTINATION_CREDENTIAL, {"token": DESTINATION_TOKEN})

    def run(self, *, stage: str = "plan", claimed: bool = True) -> tuple[str, str]:
        """Create a plan run, and return its identifier and the identifier of its execution."""
        if self._version is None:
            registered = self.api.client.post(
                "/configs",
                headers={**bearer(ADMIN_TOKEN), "Idempotency-Key": "register-for-release"},
                json={"package": _package(), "reason": "register"},
            )
            assert registered.status_code == 201, registered.json()["error"]["code"]
            self._version = registered.json()["version"]
        self._runs += 1
        created = self.api.client.post(
            "/runs",
            headers={**bearer(ADMIN_TOKEN), "Idempotency-Key": f"release-run-{self._runs}"},
            json={
                "operation": "plan",
                "config_id": self._version["config_id"],
                "registry_version": self._version["registry_version"],
                "reason": "review the plan",
            },
        )
        assert created.status_code in {200, 201, 202}, created.json()["error"]["code"]
        run_id = created.json()["run"]["run_id"]
        run = self.api.store.lookup_run(run_id).value
        assert run is not None
        flow_run_id = run.prefect_executions[-1].flow_run_id
        if claimed:
            assert self.api.store.claim_execution(run_id, flow_run_id, worker_id=str(uuid4()))
        if stage != "plan":
            self.execute("UPDATE prefect_executions SET purpose = ? WHERE run_id = ?", (stage, run_id))
        return run_id, flow_run_id

    def execute(self, statement: str, values: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Run one statement on the store's database, for a state the store API reaches only through a run."""
        databases = sorted(self._tmp_path.rglob("*.sqlite3"))
        assert len(databases) == 1
        with sqlite3.connect(databases[0]) as connection:
            return connection.execute(statement, values).fetchall()

    def dump(self) -> str:
        databases = sorted(self._tmp_path.rglob("*.sqlite3"))
        with sqlite3.connect(databases[0]) as connection:
            return "\n".join(connection.iterdump())

    def release(self, run_id: str, flow_run_id: str, *, headers: dict[str, str] | None = None) -> Response:
        return self.api.client.post(
            f"/worker/runs/{run_id}/credentials",
            headers=bearer(WORKER_TOKEN) if headers is None else headers,
            json={"flow_run_id": flow_run_id},
        )

    def released_events(self) -> list[AuditEvent]:
        return [event for event in self.api.events() if event.event_type == RELEASED]


@pytest.fixture
def released(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Released:
    flow_runs = iter(range(1_000))

    async def submit(  # noqa: RUF029 - it replaces an asynchronous method.
        _self: object, parameters: dict[str, object], *, idempotency_key: str
    ) -> Submission:
        del parameters, idempotency_key
        return Submission(flow_run_id=f"flow-run-{next(flow_runs)}", state="pending")

    # Each run gets its own flow run identifier, so that one run can present another run's.
    monkeypatch.setattr(account_api.Orchestration, "submit", submit)
    api = build_account_api(tmp_path, monkeypatch, credential_key=TEST_ENCRYPTION_KEY)
    apply_worker_account(api.store, WORKER_TOKEN)
    return Released(api, tmp_path)


def _refused(response: Response, status: int, code: str) -> dict[str, Any]:
    """Check a refusal and that it holds no value, and return its error."""
    leaked = _holds_a_value(response.text)
    assert not leaked
    assert response.status_code == status, response.json()["error"]["code"]
    error = response.json()["error"]
    assert error["code"] == code
    return error


def test_a_claimed_execution_receives_the_values_of_both_blocks(released: Released) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run()

    response = released.release(run_id, flow_run_id)

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert sorted(body["blocks"]) == ["destination", "source"]
    source, destination = body["blocks"]["source"], body["blocks"]["destination"]
    assert (source["name"], source["revision"], sorted(source["fields"])) == (SOURCE_CREDENTIAL, 1, ["token"])
    assert (destination["name"], destination["revision"]) == (DESTINATION_CREDENTIAL, 1)
    assert _digest(source["fields"]["token"]) == _digest(SOURCE_TOKEN)
    assert _digest(destination["fields"]["token"]) == _digest(DESTINATION_TOKEN)


@pytest.mark.parametrize(
    ("stage", "blocks"),
    [
        ("plan", ["destination", "source"]),
        ("sync", ["destination", "source"]),
        ("apply", ["destination"]),
        ("resume", ["destination"]),
        ("verify", ["destination"]),
    ],
)
def test_a_stage_receives_only_the_blocks_it_needs(released: Released, stage: str, blocks: list[str]) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run(stage=stage)

    response = released.release(run_id, flow_run_id)

    assert response.status_code == 200
    assert sorted(response.json()["blocks"]) == blocks
    source_released = SOURCE_TOKEN in response.text
    assert source_released is ("source" in blocks)
    assert [(use.stage, use.block) for use in released.api.store.credential_uses(run_id)] == [
        (stage, block) for block in blocks
    ]


def test_a_stage_that_does_not_need_the_source_is_not_refused_for_its_credential(released: Released) -> None:
    create_stored_credential(released.api.store, DESTINATION_CREDENTIAL, {"token": DESTINATION_TOKEN})
    run_id, flow_run_id = released.run(stage="apply")

    response = released.release(run_id, flow_run_id)

    assert response.status_code == 200
    assert sorted(response.json()["blocks"]) == ["destination"]


def test_an_execution_that_no_worker_claimed_receives_nothing(released: Released) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run(claimed=False)

    _refused(released.release(run_id, flow_run_id), 409, "execution-not-in-progress")

    assert released.api.store.credential_uses(run_id) == ()
    assert released.released_events() == []


@pytest.mark.parametrize(
    ("state", "outcome"), [("completed", "succeeded"), ("failed", "failed"), ("cancelled", "cancelled")]
)
def test_an_execution_that_ended_receives_nothing(released: Released, state: str, outcome: str) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run()
    released.execute(
        "UPDATE prefect_executions SET terminal_at = claimed_at, terminal_state = ?, terminal_outcome = ? "
        "WHERE run_id = ?",
        (state, outcome, run_id),
    )

    _refused(released.release(run_id, flow_run_id), 409, "execution-not-in-progress")


def test_a_finished_run_receives_nothing(released: Released) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run()
    released.execute(
        "UPDATE prefect_executions SET terminal_at = claimed_at, terminal_state = 'completed', "
        "terminal_outcome = 'succeeded' WHERE run_id = ?",
        (run_id,),
    )
    released.execute(
        "UPDATE product_runs SET phase = 'planned', outcome = 'succeeded', finished_at = started_at WHERE run_id = ?",
        (run_id,),
    )
    finished = released.api.store.lookup_run(run_id).value
    assert finished is not None
    assert finished.finished_at is not None

    _refused(released.release(run_id, flow_run_id), 409, "execution-not-in-progress")


def test_the_flow_run_of_another_run_receives_nothing(released: Released) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run()
    other_run_id, other_flow_run_id = released.run()
    assert flow_run_id != other_flow_run_id

    _refused(released.release(run_id, other_flow_run_id), 409, "execution-not-in-progress")
    _refused(released.release(other_run_id, flow_run_id), 409, "execution-not-in-progress")

    assert released.api.store.credential_uses(run_id) == ()
    assert released.api.store.credential_uses(other_run_id) == ()


def test_an_unknown_run_is_not_found(released: Released) -> None:
    released.store_credentials()

    _refused(released.release("run_missing", "flow-run-0"), 404, "run-not-found")


def test_a_release_writes_no_mutation_receipt_and_takes_no_idempotency_key(released: Released) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run()
    before = released.execute("SELECT COUNT(*) FROM mutation_receipts")

    response = released.release(run_id, flow_run_id)

    assert response.status_code == 200
    assert released.execute("SELECT COUNT(*) FROM mutation_receipts") == before


def test_a_release_records_the_revision_each_block_received(released: Released) -> None:
    released.store_credentials()
    create_stored_credential(released.api.store, SOURCE_CREDENTIAL, {"token": SOURCE_TOKEN})
    run_id, flow_run_id = released.run()
    source = released.api.store.lookup_stored_credential(SOURCE_CREDENTIAL)
    destination = released.api.store.lookup_stored_credential(DESTINATION_CREDENTIAL)
    assert source is not None
    assert destination is not None

    assert released.release(run_id, flow_run_id).status_code == 200

    uses = released.api.store.credential_uses(run_id)
    assert [
        (use.flow_run_id, use.stage, use.block, use.credential_id, use.credential_name, use.revision) for use in uses
    ] == [
        (flow_run_id, "plan", "destination", destination.credential_id, DESTINATION_CREDENTIAL, 1),
        (flow_run_id, "plan", "source", source.credential_id, SOURCE_CREDENTIAL, 2),
    ]


def test_a_repeated_release_answers_again_and_replaces_its_rows(released: Released) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run()

    first = released.release(run_id, flow_run_id)
    second = released.release(run_id, flow_run_id)

    assert (first.status_code, second.status_code) == (200, 200)
    assert len(released.api.store.credential_uses(run_id)) == 2


def test_a_release_writes_one_event_and_one_log_line_with_names_and_no_value(released: Released) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run()

    with capture_logs() as logs:
        assert released.release(run_id, flow_run_id).status_code == 200

    events = released.released_events()
    assert len(events) == 1
    event = events[0]
    assert event.run_id == run_id
    assert event.outcome == "accepted"
    assert sorted(item.id for item in event.related if item.kind == "credential") == [
        DESTINATION_CREDENTIAL,
        SOURCE_CREDENTIAL,
    ]
    lines = [line for line in logs if line["event"] == "credential.released"]
    assert len(lines) == 1
    assert lines[0]["run_id"] == run_id
    assert lines[0]["stage"] == "plan"
    assert lines[0]["credentials"] == [
        {"block": "destination", "name": DESTINATION_CREDENTIAL, "revision": 1},
        {"block": "source", "name": SOURCE_CREDENTIAL, "revision": 1},
    ]
    leaked = (
        _holds_a_value(repr(logs)) or _holds_a_value(repr(released.api.events())) or _holds_a_value(released.dump())
    )
    assert not leaked


def test_the_event_of_a_release_of_one_credential_names_its_revision(released: Released) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run(stage="verify")

    assert released.release(run_id, flow_run_id).status_code == 200

    (event,) = released.released_events()
    assert [item.id for item in event.related if item.kind == "credential"] == [DESTINATION_CREDENTIAL]
    assert event.details is not None
    assert event.details.revision == 1


def test_a_credential_name_that_equals_a_released_value_stays_intact(released: Released) -> None:
    """A released value is in the redaction registry of the request; a name equal to it is still recorded."""
    create_stored_credential(released.api.store, SOURCE_CREDENTIAL, {"token": DESTINATION_CREDENTIAL})
    create_stored_credential(released.api.store, DESTINATION_CREDENTIAL, {"token": SOURCE_CREDENTIAL})
    run_id, flow_run_id = released.run()

    with capture_logs() as logs:
        response = released.release(run_id, flow_run_id)

    assert response.status_code == 200
    assert response.json()["blocks"]["source"]["name"] == SOURCE_CREDENTIAL
    assert sorted(use.credential_name for use in released.api.store.credential_uses(run_id)) == [
        DESTINATION_CREDENTIAL,
        SOURCE_CREDENTIAL,
    ]
    (event,) = released.released_events()
    assert sorted(item.id for item in event.related if item.kind == "credential") == [
        DESTINATION_CREDENTIAL,
        SOURCE_CREDENTIAL,
    ]
    (line,) = [line for line in logs if line["event"] == "credential.released"]
    assert [entry["name"] for entry in line["credentials"]] == [DESTINATION_CREDENTIAL, SOURCE_CREDENTIAL]


def test_a_missing_credential_is_refused_and_names_the_block(released: Released) -> None:
    create_stored_credential(released.api.store, DESTINATION_CREDENTIAL, {"token": DESTINATION_TOKEN})
    run_id, flow_run_id = released.run()

    error = _refused(released.release(run_id, flow_run_id), 422, "credential-not-found")

    assert error["details"] == {"block": "source", "name": SOURCE_CREDENTIAL}
    assert released.api.store.credential_uses(run_id) == ()
    assert released.released_events() == []


def test_a_credential_without_a_needed_field_is_refused_and_names_the_fields(released: Released) -> None:
    create_stored_credential(released.api.store, DESTINATION_CREDENTIAL, {"token": DESTINATION_TOKEN})
    create_stored_credential(
        released.api.store, SOURCE_CREDENTIAL, {"username": "released-marker-user", "password": SOURCE_TOKEN}
    )
    run_id, flow_run_id = released.run()

    error = _refused(released.release(run_id, flow_run_id), 422, "credential-field-missing")

    assert error["details"] == {"block": "source", "name": SOURCE_CREDENTIAL, "fields": ["token"]}
    assert released.api.store.credential_uses(run_id) == ()


def test_a_value_that_the_current_key_cannot_decrypt_is_refused(released: Released) -> None:
    create_stored_credential(released.api.store, DESTINATION_CREDENTIAL, {"token": DESTINATION_TOKEN})
    other = CredentialCipher(OTHER_KEY)
    released.api.store.create_stored_credential(
        name=SOURCE_CREDENTIAL,
        description="",
        fields=("token",),
        encrypt=lambda credential_id, revision: tuple(other.encrypt(credential_id, revision, {"token": SOURCE_TOKEN})),
        actor="test",
    )
    run_id, flow_run_id = released.run()

    with capture_logs() as logs:
        error = _refused(released.release(run_id, flow_run_id), 500, "credential-undecryptable")

    assert error["details"] == {"block": "source", "name": SOURCE_CREDENTIAL}
    leaked = _holds_a_value(repr(logs))
    assert not leaked
    assert released.api.store.credential_uses(run_id) == ()
    assert released.released_events() == []


def test_the_route_answers_503_when_no_key_is_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = build_account_api(tmp_path, monkeypatch)
    apply_worker_account(api.store, WORKER_TOKEN)
    without_key = Released(api, tmp_path)
    without_key.store_credentials()
    run_id, flow_run_id = without_key.run()

    _refused(without_key.release(run_id, flow_run_id), 503, "credential-key-not-configured")


@pytest.mark.parametrize(
    "presented", ["admin-token", "administrator-token", "administrator-session", "account-token", "account-session"]
)
def test_every_account_but_the_worker_is_forbidden(released: Released, presented: str) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run()
    api = released.api
    headers = {
        "admin-token": lambda: bearer(ADMIN_TOKEN),
        "administrator-token": lambda: api.token_headers("root"),
        "administrator-session": lambda: api.session_headers("root"),
        "account-token": lambda: api.token_headers("alice"),
        "account-session": lambda: api.session_headers("alice"),
    }[presented]()

    _refused(released.release(run_id, flow_run_id, headers=headers), 403, "forbidden")

    assert api.store.credential_uses(run_id) == ()
    assert released.released_events() == []


def test_a_request_without_a_token_is_unauthenticated(released: Released) -> None:
    released.store_credentials()
    run_id, flow_run_id = released.run()

    _refused(released.release(run_id, flow_run_id, headers={}), 401, "unauthenticated")


def test_a_request_without_a_flow_run_identifier_is_invalid(released: Released) -> None:
    released.store_credentials()
    run_id, _flow_run_id = released.run()

    response = released.api.client.post(
        f"/worker/runs/{run_id}/credentials", headers=bearer(WORKER_TOKEN), json={"flow_run": "flow-run-0"}
    )

    _refused(response, 422, "request-invalid")
