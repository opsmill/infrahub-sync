"""How a stage of the worker gets the stored credential values of its run (spec 027, research R6).

- A stage asks the Sync API once for its execution, however often it resolves its configuration.
- A configuration that names no stored credential makes no request and reads no worker variable.
- An apply, a resume and a verify ask only for the destination.
- The two reads of a scheduled start that come before its run exists get no value: the worker
  route that releases values answers a claimed execution of a run only (FR-004). The readiness
  read goes without a token. The read of the earlier proposed changes asks the Sync API for
  their states, and a failure of that read fails the start; it never goes on without the read.

Every value is a marker text. A check compares digests or a boolean, so a failed check prints no
value.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.configuration import ConfigurationPackage, parse_configuration_package
from infrahub_sync.configuration import capabilities as capabilities_module
from infrahub_sync.configuration.capabilities import DestinationSchemaReadError
from infrahub_sync.product_store.models import ConfigurationVersion, LookupResult, ProductRun
from infrahub_sync.runtime_schema import worker as worker_module
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.credential_client import (
    CredentialUnavailableError,
    ProposedChangeStatesUnreadableError,
    ReleasedBlock,
)
from infrahub_sync.service.schedule_admission import EarlierProposedChangesUnreadableError
from infrahub_sync.service.settings import WORKER_API_URL_ENV, WORKER_TOKEN_ENV, CredentialSettingsError
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from infrahub_sync.product_store import ProductProjection
    from infrahub_sync.product_store.models import ScheduleBranch, ScheduleRecord

SOURCE_TOKEN = "stage-marker-source-token-18be"  # noqa: S105 - a test value, not a secret.
DESTINATION_TOKEN = "stage-marker-destination-token-6f03"  # noqa: S105 - a test value, not a secret.
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


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def _package(*, blocks: tuple[str, ...] = ("source", "destination")) -> ConfigurationPackage:
    """Return a package whose ``blocks`` each name a stored credential; the others keep ``$credential``."""
    data = package_data()
    data["configuration"]["schema_mapping"] = MAPPING
    names = {"source": SOURCE_CREDENTIAL, "destination": DESTINATION_CREDENTIAL}
    references = {"source": "netbox-token", "destination": "infrahub-token"}
    for block in blocks:
        data["configuration"][block]["settings"].pop("token", None)
        data["configuration"][block]["credential"] = names[block]
        data["credentials"].pop(references[block])
    if not data["credentials"]:
        data.pop("credentials")
    return parse_configuration_package(data)


class _Projection:
    """The two durable reads that the resolution of a stage performs, without a store."""

    def __init__(self, package: ConfigurationPackage) -> None:
        self.package = package
        self.binding = ("cfg-stored-credentials", 1, package.checksum())

    def lookup_run(self, run_id: str) -> LookupResult[ProductRun]:
        return LookupResult(
            value=ProductRun(
                run_id=run_id,
                operation="plan",
                configuration_reference=f"{self.binding[0]}@{self.binding[1]}",
                config_id=self.binding[0],
                registry_version=self.binding[1],
                package_checksum=self.binding[2],
                actor="owner",
                started_at=datetime.now(timezone.utc),
                phase="reserved",
            )
        )

    def lookup_configuration_version(self, config_id: str, registry_version: int) -> LookupResult[ConfigurationVersion]:
        return LookupResult(
            value=ConfigurationVersion(
                config_id=config_id,
                registry_version=registry_version,
                package_checksum=self.binding[2],
                declared_content=self.package.model_dump(mode="json"),
                created_at=datetime.now(timezone.utc),
            )
        )


class _SyncApi:
    """Stand in for the worker's client: count the requests and answer the blocks of the stage."""

    def __init__(self, blocks: tuple[str, ...] = ("source", "destination")) -> None:
        self.requests: list[tuple[str, str]] = []
        self._blocks = blocks

    def __call__(self, run_id: str, flow_run_id: str) -> dict[str, ReleasedBlock]:
        self.requests.append((run_id, flow_run_id))
        released = {
            "source": ReleasedBlock(name=SOURCE_CREDENTIAL, revision=1, fields={"token": SOURCE_TOKEN}),
            "destination": ReleasedBlock(name=DESTINATION_CREDENTIAL, revision=1, fields={"token": DESTINATION_TOKEN}),
        }
        return {block: released[block] for block in self._blocks}


@pytest.fixture(autouse=True)
def _no_destination_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", lambda _package, _branch: SNAPSHOT)
    # A worker of these tests holds no variable to reach the Sync API: a stage that asks fails.
    monkeypatch.delenv(WORKER_API_URL_ENV, raising=False)
    monkeypatch.delenv(WORKER_TOKEN_ENV, raising=False)


def _resolve(
    projection: _Projection,
    stage: str,
    credentials: Any = None,  # noqa: ANN401 - the holder of the flow.
) -> Any:  # noqa: ANN401 - the runtime instance.
    _, instance, _ = service_flow._worker_execution_context(
        "run-stored-credentials",
        projection.binding,
        projection=cast("ProductProjection", projection),
        run_branch=None,
        stage=stage,
        credentials=credentials,
    )
    return instance


# --- one request for a stage execution ----------------------------------------------------


def test_a_stage_asks_once_however_often_it_resolves_its_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    api = _SyncApi()
    monkeypatch.setattr(service_flow, "release_run_credentials", api)
    projection = _Projection(_package())
    credentials = service_flow._RunCredentials("run-stored-credentials", "flow-run-1")

    # A `sync` resolves its configuration before it waits for the write guard, and again under it.
    first = _resolve(projection, "sync", credentials)
    second = _resolve(projection, "sync", credentials)

    assert api.requests == [("run-stored-credentials", "flow-run-1")]
    for instance in (first, second):
        assert _digest(instance.source.settings["token"]) == _digest(SOURCE_TOKEN)
        assert _digest(instance.destination.settings["token"]) == _digest(DESTINATION_TOKEN)


def test_the_holder_shows_no_value() -> None:
    credentials = service_flow._RunCredentials("run-stored-credentials", "flow-run-1")
    credentials._released = _SyncApi()("run-stored-credentials", "flow-run-1")

    shown = SOURCE_TOKEN in repr(credentials) or DESTINATION_TOKEN in repr(credentials)

    assert not shown


@pytest.mark.parametrize("stage", ["apply", "resume", "verify"])
def test_a_stage_that_builds_the_destination_only_fills_no_source_setting(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    api = _SyncApi(blocks=("destination",))
    monkeypatch.setattr(service_flow, "release_run_credentials", api)
    credentials = service_flow._RunCredentials("run-stored-credentials", "flow-run-1")

    instance = _resolve(_Projection(_package()), stage, credentials)

    assert len(api.requests) == 1
    assert "token" not in instance.source.settings
    assert _digest(instance.destination.settings["token"]) == _digest(DESTINATION_TOKEN)


# --- no request when no block of the stage names a credential -----------------------------


def _refuse_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(run_id: str, flow_run_id: str) -> dict[str, ReleasedBlock]:
        msg = f"the stage of {run_id} asked the Sync API for values ({flow_run_id})"
        raise AssertionError(msg)

    monkeypatch.setattr(service_flow, "release_run_credentials", refuse)


def test_a_configuration_that_names_no_stored_credential_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN", "old-path-netbox-value-0001")
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", "old-path-infrahub-value-0001")
    _refuse_requests(monkeypatch)
    credentials = service_flow._RunCredentials("run-stored-credentials", "flow-run-1")

    instance = _resolve(_Projection(_package(blocks=())), "plan", credentials)

    assert _digest(instance.source.settings["token"]) == _digest("old-path-netbox-value-0001")


@pytest.mark.parametrize("stage", ["apply", "resume", "verify"])
def test_a_stage_makes_no_request_for_a_credential_of_the_source_it_does_not_build(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", "old-path-infrahub-value-0001")
    _refuse_requests(monkeypatch)
    credentials = service_flow._RunCredentials("run-stored-credentials", "flow-run-1")

    instance = _resolve(_Projection(_package(blocks=("source",))), stage, credentials)

    assert "token" not in instance.source.settings


def test_a_stage_without_its_execution_does_not_resolve_a_named_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse_requests(monkeypatch)

    with pytest.raises(ValueError, match="needs its execution"):
        _resolve(_Projection(_package()), "plan")


# --- the reads of a scheduled start that come before its run ------------------------------


def _schedule(projection: _Projection) -> ScheduleRecord:
    record = SimpleNamespace(
        schedule_id="schedule-stored-credentials",
        config_id=projection.binding[0],
        registry_version=projection.binding[1],
        state="active",
        write_target="branch-review",
        start_late_by_at_most_seconds=3600,
    )
    return cast("ScheduleRecord", record)


def _branches(*run_ids: str, proposed_change: bool = True) -> tuple[ScheduleBranch, ...]:
    return tuple(
        cast("ScheduleBranch", SimpleNamespace(run_id=run_id, proposed_change_id="pc" if proposed_change else None))
        for run_id in run_ids
    )


def _sync_api_states(monkeypatch: pytest.MonkeyPatch, answer: dict[str, str] | Exception) -> list[str]:
    """Stand in for the worker's client of the states route; return the schedules it was asked for."""
    asked: list[str] = []

    def read(schedule_id: str) -> dict[str, str]:
        asked.append(schedule_id)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(service_flow, "read_schedule_proposed_change_states", read)
    return asked


def test_the_worker_holds_no_destination_setting_of_a_schedule_before_its_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse_requests(monkeypatch)
    projection = _Projection(_package())

    with pytest.raises(EarlierProposedChangesUnreadableError) as unreadable:
        service_flow._destination_settings(cast("ProductProjection", projection), _schedule(projection))

    assert unreadable.value.cause == "credential_unavailable"


def test_the_reader_of_a_schedule_asks_the_sync_api_for_the_states(monkeypatch: pytest.MonkeyPatch) -> None:
    _refuse_requests(monkeypatch)
    asked = _sync_api_states(monkeypatch, {"run-1": "approved", "run-2": "merged", "run-of-another-read": "open"})
    built: list[object] = []
    monkeypatch.setattr(service_flow.review_ops, "build_client", lambda *_args: built.append(_args))
    projection = _Projection(_package())
    read = service_flow._earlier_proposed_changes_reader(cast("ProductProjection", projection))

    states = read(_schedule(projection), _branches("run-1", "run-2"))

    assert asked == ["schedule-stored-credentials"]
    # Only the rows the admission passed; the worker builds no client of the destination.
    assert states == {"run-1": "approved", "run-2": "merged"}
    assert built == []


def test_a_destination_without_a_stored_credential_is_read_by_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", "old-path-infrahub-value-0001")
    asked = _sync_api_states(monkeypatch, AssertionError("the worker asked the Sync API for the states"))
    monkeypatch.setattr(service_flow.review_ops, "build_client", lambda *_args: object())
    monkeypatch.setattr(service_flow.review_ops, "read_proposed_change", lambda *_args, **_kwargs: "open")
    projection = _Projection(_package(blocks=("source",)))
    read = service_flow._earlier_proposed_changes_reader(cast("ProductProjection", projection))

    states = read(_schedule(projection), _branches("run-1"))

    assert (states, asked) == ({"run-1": "open"}, [])


def test_an_answer_without_the_state_of_a_row_fails_the_start(monkeypatch: pytest.MonkeyPatch) -> None:
    _sync_api_states(monkeypatch, {"run-1": "merged"})
    projection = _Projection(_package())
    read = service_flow._earlier_proposed_changes_reader(cast("ProductProjection", projection))

    # A row with no state would read as "not approved", and a new run could replace an approved change.
    with pytest.raises(EarlierProposedChangesUnreadableError) as unreadable:
        read(_schedule(projection), _branches("run-1", "run-2"))

    assert unreadable.value.cause == "proposed-change-states-incomplete"


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (ProposedChangeStatesUnreadableError("system_unreachable"), "system_unreachable"),
        (ProposedChangeStatesUnreadableError("token_rejected"), "token_rejected"),
        (CredentialUnavailableError("credential-missing", code="credential-not-found"), "credential_unavailable"),
        (CredentialUnavailableError("api-unreachable"), "credential_unavailable"),
        (CredentialSettingsError(f"{WORKER_TOKEN_ENV} is not set"), "credential_unavailable"),
    ],
    ids=("infrahub-unreachable", "infrahub-refused", "credential-missing", "api-unreachable", "worker-variables"),
)
def test_a_failed_read_of_the_states_fails_the_start_with_its_cause(
    monkeypatch: pytest.MonkeyPatch, error: Exception, cause: str
) -> None:
    _sync_api_states(monkeypatch, error)
    projection = _Projection(_package())
    read = service_flow._earlier_proposed_changes_reader(cast("ProductProjection", projection))

    with pytest.raises(EarlierProposedChangesUnreadableError) as unreadable:
        read(_schedule(projection), _branches("run-1"))

    assert unreadable.value.cause == cause


@pytest.mark.parametrize(("reason", "waits_for"), [("unreachable", "unreachable"), ("unauthorized", None)])
def test_the_destination_of_a_scheduled_start_is_read_without_a_token(
    monkeypatch: pytest.MonkeyPatch, reason: str, waits_for: str | None
) -> None:
    _refuse_requests(monkeypatch)
    tokens: list[object] = []

    def read(package: ConfigurationPackage, branch: str) -> dict[str, Any]:
        _, sdk_config = capabilities_module._resolved_client_settings(package, branch)
        tokens.append(sdk_config["api_token"])
        message = "the destination schema read failed"
        raise DestinationSchemaReadError(message, reason=reason)

    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", read)
    projection = _Projection(_package())
    start = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)

    not_ready = service_flow._destination_not_ready(
        cast("ProductProjection", projection), _schedule(projection), start, start + timedelta(seconds=5)
    )

    # A destination that does not answer still makes the start wait; a refusal for the missing
    # token does not.
    assert tokens == [None]
    assert not_ready == waits_for
