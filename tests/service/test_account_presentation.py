"""Answers of the Sync API show account names, never account identifiers (spec 022, research R7).

A record holds an account identifier. These tests drive real routes of an application whose
callers are local accounts, and check each answer at the HTTP boundary.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from fastapi.testclient import TestClient
from pydantic import BaseModel

from infrahub_sync.client import models as client_models
from infrahub_sync.client.models import ResumeResource
from infrahub_sync.product_store import (
    Account,
    AutomaticResume,
    ProductProjection,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.product_store.store import ProductStoreProviderError
from infrahub_sync.service import models as service_models
from infrahub_sync.service import service as service_module
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import StoreAccountResolver
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.credential_crypto import CredentialCipher
from infrahub_sync.service.credentials import digest, new_api_token
from infrahub_sync.service.events import RUN_AUTOMATIC_RESUME_STARTED, run_event
from infrahub_sync.service.liveness import AUTOMATIC_RESUME_ACTOR
from infrahub_sync.service.models import PlanOperationResource, PlanResource, PlanSummaryResource
from infrahub_sync.service.presentation import (
    ACCOUNT_FIELDS,
    AUTOMATIC_RESUME_DISPLAY_NAME,
    RELATED_KEY,
    account_identifiers,
    present_accounts,
)
from infrahub_sync.service.resume_view import automatic_resume_view
from infrahub_sync.service.schedules import ScheduleService
from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService
from tests.configuration.validation_packages import package_data
from tests.helpers.stored_credentials import TEST_ENCRYPTION_KEY
from tests.service.health_fixtures import fake_health_checker
from tests.service.schedule_fixtures import FakeOrchestration
from tests.service.test_approval import _RecordingOrchestration

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from httpx import Response

_T0 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
# The store reads no password, so any text stands for a hash here.
_HASH = "scrypt$32768$8$3$c2FsdA$a2V5"
_CHECKSUM = "a" * 64
_RUN_ID = "run-presentation-001"
_OWNER = "Alice"
_ADMINISTRATOR = "Root"
_ID_A = "acct_" + "a" * 32
_ID_B = "acct_" + "b" * 32
_ID_UNKNOWN = "acct_" + "c" * 32
_MARK = "acct_"


def _stored(account_id: str, *, name: str | None, short_id: str = "0badc0de", deleted: bool = False) -> Account:
    return Account(
        account_id=account_id,
        short_id=short_id,
        name=name,
        name_key=None if name is None else name.lower(),
        created_at=_T0,
        updated_at=_T0,
        password_changed_at=_T0,
        deleted_at=_T0 if deleted else None,
    )


_ACCOUNTS = {
    _ID_A: _stored(_ID_A, name="Alice"),
    _ID_B: _stored(_ID_B, name=None, short_id="1234abcd", deleted=True),
}


class _Reader:
    """An account read over fixed accounts that records each call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, account_ids: Sequence[str]) -> dict[str, Account]:
        self.calls.append(list(account_ids))
        return {account_id: _ACCOUNTS[account_id] for account_id in account_ids if account_id in _ACCOUNTS}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param({"actor": _ID_A}, {"actor": "Alice"}, id="actor"),
        pytest.param({"approved_by": _ID_A}, {"approved_by": "Alice"}, id="approved-by"),
        pytest.param({"registered_by": _ID_A}, {"registered_by": "Alice"}, id="registered-by"),
        pytest.param({"actor": _ID_B}, {"actor": "Deleted account 1234abcd"}, id="deleted-account"),
        pytest.param(
            {"run": {"approval": {"approved_by": _ID_A, "reason": "ok"}, "actor": _ID_B}},
            {"run": {"approval": {"approved_by": "Alice", "reason": "ok"}, "actor": "Deleted account 1234abcd"}},
            id="nested",
        ),
        pytest.param(
            {"items": [{"actor": _ID_A}, {"actor": "alice"}, {"actor": None}]},
            {"items": [{"actor": "Alice"}, {"actor": "alice"}, {"actor": None}]},
            id="list-of-records",
        ),
        pytest.param([[{"actor": _ID_A}]], [[{"actor": "Alice"}]], id="list-at-the-top"),
        pytest.param(
            {"related": [{"kind": "account", "id": _ID_A}, {"kind": "config", "id": _ID_A}]},
            {"related": [{"kind": "account", "id": "Alice"}, {"kind": "config", "id": _ID_A}]},
            id="related-resource-of-kind-account-only",
        ),
        pytest.param(
            {"related": [{"kind": "run", "id": "r", "nested": {"actor": _ID_A}}]},
            {"related": [{"kind": "run", "id": "r", "nested": {"actor": "Alice"}}]},
            id="field-inside-another-related-resource",
        ),
        pytest.param(
            {"primary_resource": {"kind": "account", "id": _ID_A}},
            {"primary_resource": {"kind": "account", "id": "Alice"}},
            id="primary-resource-of-kind-account",
        ),
        pytest.param(
            {"primary_resource": {"kind": "account", "id": _ID_B}},
            {"primary_resource": {"kind": "account", "id": "Deleted account 1234abcd"}},
            id="primary-resource-of-a-deleted-account",
        ),
        pytest.param(
            {"primary_resource": {"kind": "run", "id": _ID_A}, "resource": {"kind": "account", "id": _ID_A}},
            {"primary_resource": {"kind": "run", "id": _ID_A}, "resource": {"kind": "account", "id": _ID_A}},
            id="resource-outside-related-and-primary-is-unchanged",
        ),
        pytest.param({"primary_resource": None}, {"primary_resource": None}, id="no-primary-resource"),
        pytest.param(
            {"actor": _ID_A, "reason": f"asked by {_ID_A}", "message": _ID_A, "id": _ID_A},
            {"actor": "Alice", "reason": f"asked by {_ID_A}", "message": _ID_A, "id": _ID_A},
            id="other-keys-are-unchanged",
        ),
        pytest.param({"actor": _ID_UNKNOWN}, {"actor": _ID_UNKNOWN}, id="identifier-with-no-account"),
        pytest.param({"actor": f" {_ID_A}"}, {"actor": f" {_ID_A}"}, id="value-that-only-contains-an-identifier"),
        pytest.param({"actor": _ID_A.upper()}, {"actor": _ID_A.upper()}, id="upper-case-is-not-an-identifier"),
        pytest.param({"actor": {"name": _ID_A}}, {"actor": {"name": _ID_A}}, id="field-that-is-not-a-string"),
    ],
)
def test_present_accounts_replaces_identifiers_and_nothing_else(body: object, expected: object) -> None:
    original = json.loads(json.dumps(body))

    assert present_accounts(body, _Reader()) == expected
    assert body == original


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"actor": "alice", "approved_by": "unknown", "registered_by": None}, id="plain-names"),
        pytest.param({"actor": "unauthenticated", "related": [{"kind": "account", "id": "alice"}]}, id="sentinel"),
        pytest.param({"reason": _ID_A, "items": [], "count": 3}, id="identifier-under-another-key"),
        pytest.param("text", id="not-a-collection"),
        pytest.param(None, id="nothing"),
    ],
)
def test_present_accounts_makes_no_read_for_a_body_without_an_identifier(body: object) -> None:
    reader = _Reader()

    assert present_accounts(body, reader) is body
    assert reader.calls == []


def test_present_accounts_reads_every_identifier_of_a_body_with_one_call() -> None:
    reader = _Reader()
    body = {
        "items": [{"actor": _ID_A, "approval": {"approved_by": _ID_B}}, {"actor": _ID_A}],
        "related": [{"kind": "account", "id": _ID_UNKNOWN}],
    }

    present_accounts(body, reader)

    assert account_identifiers(body) == {_ID_A, _ID_B, _ID_UNKNOWN}
    assert reader.calls == [sorted([_ID_A, _ID_B, _ID_UNKNOWN])]


@dataclass
class _Api:
    """A Sync API whose callers are local accounts, and the account reads its answers made."""

    client: TestClient
    store: ProductProjection
    service: RunService
    owner: Account
    administrator: Account
    # Left out of the representation: pytest prints it when a test fails, and these hold token values.
    owner_headers: dict[str, str] = field(repr=False)
    administrator_headers: dict[str, str] = field(repr=False)
    reads: list[list[str]] = field(default_factory=list)


def _headers(store: ProductProjection, account: Account) -> dict[str, str]:
    value = new_api_token()
    store.create_account_token(account.account_id, name="ci", digest=digest(value), now=_T0)
    return {"Authorization": f"Bearer {value}"}


@pytest.fixture(name="api")
def _api_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Api:
    store = local_product_projection(tmp_path.resolve())
    owner = store.create_account(name=_OWNER, password_hash=_HASH, administrator=False, now=_T0)
    administrator = store.create_account(name=_ADMINISTRATOR, password_hash=_HASH, administrator=True, now=_T0)
    service = RunService(store, _RecordingOrchestration())
    reads: list[list[str]] = []
    read_accounts = service.read_accounts

    def recorded(account_ids: Sequence[str]) -> dict[str, Account]:
        reads.append(list(account_ids))
        return read_accounts(account_ids)

    monkeypatch.setattr(service, "read_accounts", recorded)
    resolver = StoreAccountResolver(store, idle_seconds=600)
    schedules = ScheduleService(
        store, FakeOrchestration(), resolver, delivered_write_targets=("branch-review", "default-branch")
    )
    application = create_app(
        service,
        resolver,
        ConfigurationRoutes(product_projection=store),
        schedules=schedules,
        credential_cipher=CredentialCipher(TEST_ENCRYPTION_KEY),
        # Its answer lists workers, so a health answer is proven to make no account read.
        health_checker=fake_health_checker(),
    )
    return _Api(
        client=TestClient(application),
        store=store,
        service=service,
        owner=owner,
        administrator=administrator,
        owner_headers=_headers(store, owner),
        administrator_headers=_headers(store, administrator),
        reads=reads,
    )


def _register(api: _Api, *, key: str = "register-1") -> Response:
    return api.client.post(
        "/configs",
        headers={**api.administrator_headers, "Idempotency-Key": key},
        json={"package": package_data(), "reason": "register this package"},
    )


def _planned_run(api: _Api, *, actor: str | None = None, run_id: str = _RUN_ID) -> str:
    """Register a configuration and store a planned run of it with a saved plan; return the configuration."""
    config_id = _register(api, key=f"register-{run_id}").json()["configuration"]["config_id"]
    version = api.store.lookup_configuration_version(config_id, 1).value
    assert version is not None
    api.store.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference=f"{config_id}@1",
            config_id=config_id,
            registry_version=1,
            package_checksum=version.package_checksum,
            actor=api.owner.account_id if actor is None else actor,
            started_at=_T0,
            phase="planned",
        )
    )
    plan = PlanResource(
        run_id=run_id,
        checksum=_CHECKSUM,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"create": 1},
            by_kind={"Device": 1},
            total=1,
            delete_operations_computed=True,
            deletes_not_executed=0,
        ),
        operations=(
            PlanOperationResource(
                operation_id="op-001", action="create", kind="Device", identity={"name": "edge-01"}, tier=0
            ),
        ),
    )
    api.store.publish_artifact(
        run_id,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=plan.model_dump_json().encode(),
    )
    return config_id


def _approve(api: _Api, *, key: str = "approve-1", headers: dict[str, str] | None = None) -> Response:
    return api.client.post(
        f"/runs/{_RUN_ID}/approve",
        headers={**(api.administrator_headers if headers is None else headers), "Idempotency-Key": key},
        json={"expected_checksum": _CHECKSUM, "reason": "reviewed by the network team"},
    )


def _mark_deleted(api: _Api, account: Account) -> None:
    """Empty the row as a deletion does. The store has no deletion function yet."""
    records = cast("Any", api.store)._records
    with records._transaction() as cursor:
        cursor.execute(
            records._sql(
                "UPDATE accounts SET name = NULL, name_key = NULL, password_hash = NULL, deleted_at = ? "
                "WHERE account_id = ?"
            ),
            (_T0.isoformat(), account.account_id),
        )


def _values(body: object, key: str) -> list[Any]:
    """Return what an answer holds for one account field: each value of the key, or each related account."""
    found: list[Any] = []
    if isinstance(body, list):
        for item in body:
            found.extend(_values(item, key))
    elif isinstance(body, dict):
        for name, item in body.items():
            if name != key:
                found.extend(_values(item, key))
            elif key == RELATED_KEY:
                found.extend(entry["id"] for entry in cast("list[dict[str, Any]]", item) if entry["kind"] == "account")
            else:
                found.append(item)
    return found


def _run_answers(api: _Api) -> list[Response]:
    _planned_run(api)
    return [
        api.client.get(f"/runs/{_RUN_ID}", headers=api.owner_headers),
        api.client.get("/runs", headers=api.owner_headers),
    ]


def _approval_answers(api: _Api) -> list[Response]:
    _planned_run(api)
    approved = _approve(api)
    return [
        approved,
        api.client.get(f"/runs/{_RUN_ID}", headers=api.owner_headers),
        api.client.get("/runs", headers=api.owner_headers),
        _approve(api, key="approve-2", headers=api.owner_headers),
    ]


def _event_answers(api: _Api) -> list[Response]:
    _planned_run(api)
    _approve(api)
    _approve(api, key="approve-2", headers=api.owner_headers)
    return [
        api.client.get("/events", headers=api.owner_headers),
        api.client.get("/events", headers=api.owner_headers, params={"run_id": _RUN_ID}),
    ]


def _version_answers(api: _Api) -> list[Response]:
    config_id = _register(api).json()["configuration"]["config_id"]
    return [api.client.get(f"/configs/{config_id}/versions", headers=api.owner_headers)]


def _me_answers(api: _Api) -> list[Response]:
    return [
        api.client.get("/me", headers=api.owner_headers),
        api.client.get("/me", headers=api.administrator_headers),
    ]


def _resume_answers(api: _Api) -> list[Response]:
    """Return a run answer with one resume.

    A resume needs an interrupted write, which this test does not build: the service's list of
    resumes is replaced, and the route and the boundary are the real ones.
    """
    _planned_run(api)
    resume = ResumeResource(actor=api.administrator.account_id, requested_at=_T0, reason=None)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(service_module, "run_resumes", lambda *_args, **_kwargs: (resume,))
        return [api.client.get(f"/runs/{_RUN_ID}", headers=api.owner_headers)]


def _schedule_answers(api: _Api) -> list[Response]:
    """Return the answers of a schedule that the owner created and the administrator changed."""
    config_id = _register(api).json()["configuration"]["config_id"]
    created = api.client.post(
        "/schedules",
        headers={**api.owner_headers, "Idempotency-Key": "schedule-1"},
        json={
            "name": "nightly",
            "config_id": config_id,
            "registry_version": 1,
            "cron": "0 2 * * *",
            "timezone": "UTC",
            "write_target": "default-branch",
        },
    )
    schedule_id = created.json()["schedule_id"]
    changed = api.client.post(
        f"/schedules/{schedule_id}/change",
        headers={**api.administrator_headers, "Idempotency-Key": "schedule-2"},
        json={"write_when_left_out": True},
    )
    return [
        created,
        changed,
        api.client.get(f"/schedules/{schedule_id}", headers=api.owner_headers),
        api.client.get("/schedules", headers=api.owner_headers),
    ]


def _credential_answers(api: _Api) -> list[Response]:
    """Return the answers of a credential that the administrator created and changed."""
    created = api.client.post(
        "/credentials",
        headers={**api.administrator_headers, "Idempotency-Key": "credential-1"},
        json={"name": "netbox-lab", "token": "presentation-token-value"},
    )
    changed = api.client.put(
        "/credentials/netbox-lab/value",
        headers={**api.administrator_headers, "Idempotency-Key": "credential-2"},
        json={"token": "presentation-token-value-2"},
    )
    return [
        created,
        changed,
        api.client.get("/credentials/netbox-lab", headers=api.owner_headers),
        api.client.get("/credentials", headers=api.owner_headers),
    ]


# One entry per response model that declares an account field: the routes that answer with it.
_ANSWERS: dict[str, Callable[[_Api], list[Response]]] = {
    "PublicRunResource.actor": _run_answers,
    # The copies of `PublicRunResource` that the service emits for a run and for a run list.
    "EmittedRunResource.actor": _run_answers,
    "_EmittedRunPageItem.actor": _run_answers,
    "RunApprovalResource.approved_by": _approval_answers,
    "PrincipalResource.actor": _me_answers,
    "ResumeResource.actor": _resume_answers,
    "EventResource.actor": _event_answers,
    "EventResource.related": _event_answers,
    "ConfigurationVersionSummaryResource.registered_by": _version_answers,
    "ScheduleResource.owner": _schedule_answers,
    "ScheduleResource.left_out_set_by": _schedule_answers,
    # The copy of `ScheduleResource` that the service emits.
    "EmittedScheduleResource.left_out_set_by": _schedule_answers,
    "CredentialResource.created_by": _credential_answers,
    "CredentialResource.updated_by": _credential_answers,
}
# Fields whose key is an account field of the presentation but whose value is not an account.
_NOT_ACCOUNTS = frozenset(
    {
        "PlanLineageResource.owner",  # A node of the destination, by kind and name.
        "PlanLastChangeResource.updated_by",  # An account of Infrahub, as an object, not an account of Sync.
        # Always the reserved actor of the automatic resume, which
        # `test_a_run_answer_names_the_actor_of_each_automatic_resume_record` covers.
        "AutomaticResumeResource.actor",
    }
)


def _account_fields() -> list[str]:
    """Name every account field that a response model of the API declares, as `Model.field`."""
    declared: set[str] = set()
    for module in (client_models, service_models):
        for name, model in inspect.getmembers(module, inspect.isclass):
            if issubclass(model, BaseModel) and not name.endswith("Request"):
                own = vars(model).get("__annotations__", {})
                declared.update(f"{name}.{key}" for key in own if key in ACCOUNT_FIELDS or key == RELATED_KEY)
    return sorted(declared - _NOT_ACCOUNTS)


def test_every_response_model_with_an_account_field_has_a_route_case() -> None:
    declared = _account_fields()

    assert declared == sorted(_ANSWERS), (
        "a response model declares a key of `ACCOUNT_FIELDS` or `related`; "
        "add the routes that answer with it to _ANSWERS, or remove the entry of a model that is gone"
    )


@pytest.mark.parametrize("model_field", _account_fields())
def test_an_answer_shows_a_name_for_each_account_field_of_a_response_model(api: _Api, model_field: str) -> None:
    assert model_field in _ANSWERS, f"no route case for {model_field}; add one to _ANSWERS"
    key = model_field.split(".")[1]

    answers = _ANSWERS[model_field](api)

    shown: list[Any] = []
    for answer in answers:
        assert _MARK not in answer.text
        shown.extend(value for value in _values(answer.json(), key) if value is not None)
    assert shown
    assert set(shown) <= {_OWNER, _ADMINISTRATOR}
    assert all(len(read) == len(set(read)) for read in api.reads)


def test_a_run_and_the_run_list_show_the_name_of_the_initiating_account(api: _Api) -> None:
    _planned_run(api)

    run = api.client.get(f"/runs/{_RUN_ID}", headers=api.owner_headers)
    listed = api.client.get("/runs", headers=api.owner_headers)

    assert run.json()["run"]["actor"] == _OWNER
    assert [item["actor"] for item in listed.json()["items"]] == [_OWNER]
    stored = api.store.lookup_run(_RUN_ID).value
    assert stored is not None
    assert stored.actor == api.owner.account_id
    assert int(run.headers["content-length"]) == len(run.content)


def test_an_answer_reads_its_accounts_with_one_store_call(api: _Api) -> None:
    _planned_run(api)
    _approve(api)
    api.reads.clear()

    answer = api.client.get(f"/runs/{_RUN_ID}", headers=api.owner_headers)

    assert answer.json()["run"]["approval"]["approved_by"] == _ADMINISTRATOR
    assert api.reads == [sorted([api.owner.account_id, api.administrator.account_id])]


def test_an_approval_shows_the_name_of_the_approving_account(api: _Api) -> None:
    _planned_run(api)

    approved = _approve(api)

    assert approved.status_code == 200
    assert approved.json()["run"]["approval"]["approved_by"] == _ADMINISTRATOR
    assert approved.json()["run"]["actor"] == _OWNER
    assert _MARK not in approved.text


def test_the_already_approved_refusal_names_the_account_in_its_details_only(api: _Api) -> None:
    _planned_run(api)
    _approve(api)

    refused = _approve(api, key="approve-2", headers=api.owner_headers)

    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "already-approved"
    assert error["message"] == "the run's plan was already approved"
    assert error["details"]["approval"]["approved_by"] == _ADMINISTRATOR
    assert _MARK not in refused.text


def test_an_event_list_shows_names_for_the_actor_and_the_related_account(api: _Api) -> None:
    _planned_run(api)
    _approve(api)

    events = api.client.get("/events", headers=api.owner_headers, params={"run_id": _RUN_ID})

    items = events.json()["items"]
    assert [item["actor"] for item in items] == [_ADMINISTRATOR]
    assert {"kind": "account", "id": _ADMINISTRATOR} in items[0]["related"]
    assert items[0]["reason"] == "reviewed by the network team"
    assert _MARK not in events.text
    stored = api.store.audit_events(_RUN_ID)
    assert {event.actor for event in stored} == {api.administrator.account_id}


def test_a_configuration_version_shows_the_name_of_the_registering_account(api: _Api) -> None:
    config_id = _register(api).json()["configuration"]["config_id"]

    versions = api.client.get(f"/configs/{config_id}/versions", headers=api.owner_headers)

    assert [item["registered_by"] for item in versions.json()["items"]] == [_ADMINISTRATOR]
    assert _MARK not in versions.text


def test_a_replayed_answer_of_a_run_route_shows_a_name_and_the_receipt_keeps_the_identifier(api: _Api) -> None:
    _planned_run(api)
    first = _approve(api)

    replayed = _approve(api)

    assert replayed.status_code == 200
    assert replayed.json() == first.json()
    assert replayed.json()["run"]["approval"]["approved_by"] == _ADMINISTRATOR
    assert _MARK not in replayed.text
    receipt = api.store.lookup_mutation(api.administrator.account_id, sha256(b"approve-1").hexdigest()).value
    assert receipt is not None
    assert receipt.response_body is not None
    assert receipt.response_body["run"]["approval"]["approved_by"] == api.administrator.account_id
    assert receipt.response_body["run"]["actor"] == api.owner.account_id


def test_a_replayed_answer_of_a_configuration_route_shows_no_identifier(api: _Api) -> None:
    first = _register(api)

    replayed = _register(api)

    assert first.status_code == replayed.status_code == 201
    assert replayed.json() == first.json()
    assert _MARK not in replayed.text
    receipt = api.store.lookup_mutation(api.administrator.account_id, sha256(b"register-1").hexdigest()).value
    assert receipt is not None
    assert receipt.actor == api.administrator.account_id


def test_a_replayed_configuration_answer_that_holds_an_identifier_shows_a_name(api: _Api) -> None:
    """The stored answer of a registration holds no account today; this one is given one."""
    first = _register(api)
    receipt = api.store.lookup_mutation(api.administrator.account_id, sha256(b"register-1").hexdigest()).value
    assert receipt is not None
    assert receipt.response_body is not None
    body = json.loads(json.dumps(receipt.response_body))
    body["version"]["registered_by"] = api.administrator.account_id
    records = cast("Any", api.store)._records
    with records._transaction() as cursor:
        cursor.execute(
            records._sql("UPDATE mutation_receipts SET response_body = ? WHERE receipt_id = ?"),
            (json.dumps(body), receipt.receipt_id),
        )

    replayed = _register(api)

    assert replayed.status_code == 201
    assert replayed.json()["version"]["registered_by"] == _ADMINISTRATOR
    assert replayed.json()["configuration"] == first.json()["configuration"]
    assert _MARK not in replayed.text


def test_a_value_that_is_not_an_identifier_is_shown_as_stored_with_no_account_read(api: _Api) -> None:
    _planned_run(api, actor="alice")
    api.reads.clear()

    run = api.client.get(f"/runs/{_RUN_ID}", headers=api.administrator_headers)

    assert run.json()["run"]["actor"] == "alice"
    assert api.reads == []


def test_an_identifier_with_no_account_is_shown_as_stored(api: _Api) -> None:
    _planned_run(api, actor=_ID_UNKNOWN)

    run = api.client.get(f"/runs/{_RUN_ID}", headers=api.administrator_headers)

    assert run.status_code == 200
    assert run.json()["run"]["actor"] == _ID_UNKNOWN


def test_a_deleted_account_is_shown_as_its_placeholder_also_in_a_replayed_answer(api: _Api) -> None:
    _planned_run(api)
    _approve(api)
    _mark_deleted(api, api.owner)
    placeholder = f"Deleted account {api.owner.short_id}"

    run = api.client.get(f"/runs/{_RUN_ID}", headers=api.administrator_headers)
    replayed = _approve(api)

    assert run.json()["run"]["actor"] == placeholder
    assert run.json()["run"]["approval"]["approved_by"] == _ADMINISTRATOR
    assert replayed.json()["run"]["actor"] == placeholder
    assert _OWNER not in run.text
    assert _MARK not in run.text + replayed.text


def test_a_reason_that_contains_an_identifier_stays_as_typed(api: _Api) -> None:
    _planned_run(api)
    reason = f"asked by {api.owner.account_id}"

    approved = api.client.post(
        f"/runs/{_RUN_ID}/approve",
        headers={**api.administrator_headers, "Idempotency-Key": "approve-reason"},
        json={"expected_checksum": _CHECKSUM, "reason": reason},
    )

    assert approved.json()["run"]["approval"]["reason"] == reason
    assert approved.json()["run"]["approval"]["approved_by"] == _ADMINISTRATOR


@pytest.mark.parametrize("path", ["/version", "/health", "/configs", "/no-such-route"])
def test_an_answer_with_no_identifier_makes_no_account_read(api: _Api, path: str) -> None:
    _register(api)
    api.reads.clear()
    before = api.client.get(path, headers=api.owner_headers)

    assert before.status_code in {200, 404}
    assert _MARK not in before.text
    assert api.reads == []


def test_an_unreachable_store_during_the_account_read_answers_503(api: _Api, monkeypatch: pytest.MonkeyPatch) -> None:
    _planned_run(api)

    def unreachable(account_ids: Sequence[str]) -> dict[str, Account]:
        del account_ids
        raise ProductStoreProviderError(sqlstate="08006")

    monkeypatch.setattr(api.service, "read_accounts", unreachable)

    answer = api.client.get(f"/runs/{_RUN_ID}", headers=api.owner_headers)

    assert answer.status_code == 503
    assert answer.json()["error"]["code"] == "service-unavailable"
    assert _MARK not in answer.text
    assert int(answer.headers["content-length"]) == len(answer.content)


def test_an_artifact_is_downloaded_byte_for_byte_with_no_account_read(api: _Api) -> None:
    _planned_run(api)
    data = json.dumps({"actor": api.owner.account_id, "approved_by": api.owner.account_id}).encode()
    api.store.publish_artifact(
        _RUN_ID, artifact_id="account-shaped", kind="run-report", media_type="application/json", data=data
    )
    api.reads.clear()

    artifact = api.client.get(f"/runs/{_RUN_ID}/artifacts/account-shaped", headers=api.owner_headers)

    assert artifact.status_code == 200
    assert artifact.content == data
    assert artifact.headers["digest"] == f"sha-256={sha256(data).hexdigest()}"
    assert api.reads == []


# --- the reserved actor of the automatic resume (spec 030, T020) ------------------------------


def test_the_automatic_resume_is_shown_with_its_display_name() -> None:
    assert AUTOMATIC_RESUME_DISPLAY_NAME == "Sync (automatic resume)"


def test_present_accounts_shows_the_reserved_actor_without_an_account_read() -> None:
    """The reserved actor is no account: it is shown by name in `actor` and related resources, with no read."""
    reader = _Reader()
    body = {
        "resumes": [{"actor": AUTOMATIC_RESUME_ACTOR}],
        "related": [{"kind": "account", "id": AUTOMATIC_RESUME_ACTOR}, {"kind": "run", "id": AUTOMATIC_RESUME_ACTOR}],
        "reason": AUTOMATIC_RESUME_ACTOR,
    }

    presented = present_accounts(body, reader)

    assert presented == {
        "resumes": [{"actor": AUTOMATIC_RESUME_DISPLAY_NAME}],
        "related": [
            {"kind": "account", "id": AUTOMATIC_RESUME_DISPLAY_NAME},
            {"kind": "run", "id": AUTOMATIC_RESUME_ACTOR},
        ],
        "reason": AUTOMATIC_RESUME_ACTOR,
    }
    assert reader.calls == []
    assert account_identifiers(body) == set()


def test_present_accounts_reads_only_the_accounts_next_to_the_reserved_actor() -> None:
    reader = _Reader()
    body = {"items": [{"actor": AUTOMATIC_RESUME_ACTOR}, {"actor": _ID_A}]}

    presented = present_accounts(body, reader)

    assert presented == {"items": [{"actor": AUTOMATIC_RESUME_DISPLAY_NAME}, {"actor": "Alice"}]}
    assert reader.calls == [[_ID_A]]


def test_a_run_answer_shows_the_automatic_resume_by_name(api: _Api) -> None:
    """The answer holds no account identifier, so the middleware must still find the reserved actor."""
    _planned_run(api, actor=AUTOMATIC_RESUME_ACTOR)
    resume = ResumeResource(actor=AUTOMATIC_RESUME_ACTOR, requested_at=_T0, reason=None)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(service_module, "run_resumes", lambda *_args, **_kwargs: (resume,))
        answer = api.client.get(f"/runs/{_RUN_ID}", headers=api.owner_headers)

    assert answer.status_code == 200, answer.text
    assert _MARK not in answer.text
    assert AUTOMATIC_RESUME_ACTOR not in answer.text
    body = answer.json()
    assert body["run"]["actor"] == AUTOMATIC_RESUME_DISPLAY_NAME
    assert [entry["actor"] for entry in body["resumes"]] == [AUTOMATIC_RESUME_DISPLAY_NAME]
    assert api.reads == []


def test_a_run_answer_names_the_actor_of_each_automatic_resume_record(api: _Api) -> None:
    """The web page shows the actor of a record as the Sync API returns it (review F8)."""
    _planned_run(api)
    record = AutomaticResume(
        run_id=_RUN_ID,
        interrupted_flow_run_id="flow-interrupted",
        cause="rule-8",
        state="ended",
        decision="resume",
        start_after=5999,
        operations_not_checked=0,
        resume_flow_run_id="flow-resume",
        decided_at=_T0,
        updated_at=_T0,
    )
    view = automatic_resume_view((record,))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(service_module, "automatic_resume_view", lambda *_args, **_kwargs: view)
        answer = api.client.get(f"/runs/{_RUN_ID}", headers=api.owner_headers)

    assert answer.status_code == 200, answer.text
    assert AUTOMATIC_RESUME_ACTOR not in answer.text
    assert [entry["actor"] for entry in answer.json()["automatic_resumes"]] == [AUTOMATIC_RESUME_DISPLAY_NAME]


def test_an_event_of_the_automatic_resume_shows_it_by_name(api: _Api) -> None:
    _planned_run(api)
    api.store.record_audit(
        run_event(
            _RUN_ID,
            None,
            event_type=RUN_AUTOMATIC_RESUME_STARTED,
            actor=AUTOMATIC_RESUME_ACTOR,
            operation="automatic-resume",
            reason=None,
            outcome="resume",
            created_at=_T0,
        )
    )
    api.reads.clear()

    answer = api.client.get(
        "/events", headers=api.owner_headers, params={"run_id": _RUN_ID, "event_type": RUN_AUTOMATIC_RESUME_STARTED}
    )

    assert answer.status_code == 200, answer.text
    assert AUTOMATIC_RESUME_ACTOR not in answer.text
    [event] = answer.json()["items"]
    assert event["actor"] == AUTOMATIC_RESUME_DISPLAY_NAME
    assert _values(event, RELATED_KEY) == [AUTOMATIC_RESUME_DISPLAY_NAME]
    assert api.reads == []
