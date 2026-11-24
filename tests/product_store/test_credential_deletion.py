"""The deletion of a stored credential in the product store (spec 027, User Story 4, FR-010, research R8).

A credential is in use when the latest version of a configuration names it, or when the version
pinned by a schedule that is not deleted names it. The store refuses the deletion and lists those
configurations and schedules. An older version does not block the deletion.

The last cases run a deletion and a registration that names the same credential at the same time
(critique E4). Both take the lock of the credential row as their first statement, so the second
one waits for the first one to commit and then reads what it wrote. Each case holds one side
inside its transaction, checks that the other side waits, and lets the first side go.

The cases run on the three profiles of ``test_stored_credentials``: SQLite, the PostgreSQL
statements on SQLite, and a PostgreSQL server when ``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` is set.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from infrahub_sync.product_store import (
    AuditEvent,
    CredentialInUseError,
    ProductProjection,
    RelatedResource,
    ScheduleFilters,
    ScheduleRecord,
    StoredCredentialNotFoundError,
    configs,
)
from infrahub_sync.product_store import stored_credentials as credential_statements
from infrahub_sync.product_store.stored_credentials import VersionCredentialsMissingError
from tests.configuration.validation_packages import package_data
from tests.product_store.test_contract import _PROFILES_WITH_POSTGRESQL, _projection_profile

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_T0 = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
_ADMIN = "acct_" + "a" * 32
CREDENTIAL = "netbox-lab"
EVENT_TYPE = "infrahub_sync.credential.deleted"
# Long enough to see that a transaction waits, and far below the 5 seconds SQLite waits for a lock.
WAITS_FOR = 0.5
ENDS_WITHIN = 20.0


def _fake_encrypt(credential_id: str, revision: int) -> tuple[str, bytes, bytes]:
    """Return fixed bytes: the store holds no value, and these cases read none."""
    del credential_id, revision
    return "0123456789abcdef", bytes(range(12)), b"\x00" * 32


def _store(projection: ProductProjection, name: str = CREDENTIAL) -> None:
    projection.create_stored_credential(
        name=name, description="", fields=("token",), encrypt=_fake_encrypt, actor=_ADMIN, now=_T0
    )


def _package(name: str, *, source: str | None, destination: str | None = None) -> dict[str, Any]:
    """Return a package with this configuration name, whose blocks name these credentials or none."""
    data = package_data()
    data["configuration"]["name"] = name
    for block, credential in (("source", source), ("destination", destination)):
        if credential is not None:
            data["configuration"][block]["credential"] = credential
    return data


def _register(projection: ProductProjection, name: str = "from-netbox", *, source: str | None = CREDENTIAL) -> str:
    """Register a configuration whose first version names the credential, and return its identifier."""
    return configs.register(package=_package(name, source=source), projection=projection).configuration.config_id


def _new_version(projection: ProductProjection, config_id: str, name: str = "from-netbox") -> None:
    """Register a second version that names no credential."""
    registered = configs.create_version(config_id=config_id, package=_package(name, source=None), projection=projection)
    assert registered.version.registry_version == 2


def _schedule_record(config_id: str, registry_version: int = 1) -> ScheduleRecord:
    """Return an active schedule that pins this version of the configuration."""
    return ScheduleRecord.model_validate(
        {
            "schedule_id": f"s-{uuid4().hex}",
            "name": f"nightly-{uuid4().hex[:8]}",
            "config_id": config_id,
            "registry_version": registry_version,
            "cron": "0 2 * * *",
            "write_target": "default-branch",
            "state": "active",
            "owner": _ADMIN,
            "created_at": _T0,
            "updated_at": _T0,
        }
    )


def _schedule(projection: ProductProjection, config_id: str, registry_version: int = 1) -> ScheduleRecord:
    """Store an active schedule that pins this version of the configuration."""
    schedule = _schedule_record(config_id, registry_version)
    projection.create_schedule(schedule)
    return schedule


def _event(name: str = CREDENTIAL) -> Callable[[Any], AuditEvent]:
    def build(credential: Any) -> AuditEvent:  # noqa: ANN401 - the stored credential record.
        assert credential.name == name
        return AuditEvent(
            event_id=f"a-{uuid4().hex}",
            run_id=None,
            actor=_ADMIN,
            operation="credential-delete",
            reason=None,
            outcome="accepted",
            created_at=_T0,
            event_type=EVENT_TYPE,
            related=(RelatedResource(kind="credential", id=credential.name),),
        )

    return build


def _deleted_events(projection: ProductProjection) -> int:
    return sum(1 for event in projection.audit_events() if event.event_type == EVENT_TYPE)


@pytest.fixture(params=_PROFILES_WITH_POSTGRESQL)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the credential deletion's PostgreSQL parameter")


# --- the in-use check ---------------------------------------------------------------------


def test_the_latest_version_of_a_configuration_blocks_the_deletion(projection: ProductProjection) -> None:
    _store(projection)
    config_id = _register(projection)

    with pytest.raises(CredentialInUseError) as refused:
        projection.delete_stored_credential(name=CREDENTIAL, event_for=_event())

    assert refused.value.configurations == (config_id,)
    assert refused.value.schedules == ()
    assert projection.lookup_stored_credential(CREDENTIAL) is not None
    assert _deleted_events(projection) == 0


def test_a_configuration_whose_two_blocks_name_the_credential_is_listed_once(projection: ProductProjection) -> None:
    _store(projection)
    registered = configs.register(
        package=_package("from-netbox", source=CREDENTIAL, destination=CREDENTIAL), projection=projection
    )

    with pytest.raises(CredentialInUseError) as refused:
        projection.delete_stored_credential(name=CREDENTIAL)

    assert refused.value.configurations == (registered.configuration.config_id,)


def test_every_configuration_whose_latest_version_names_the_credential_is_listed(
    projection: ProductProjection,
) -> None:
    _store(projection)
    first = _register(projection, "from-netbox-1")
    second = _register(projection, "from-netbox-2")
    _register(projection, "from-netbox-3", source=None)

    with pytest.raises(CredentialInUseError) as refused:
        projection.delete_stored_credential(name=CREDENTIAL)

    assert refused.value.configurations == tuple(sorted((first, second)))


def test_a_schedule_that_pins_an_older_version_blocks_the_deletion(projection: ProductProjection) -> None:
    _store(projection)
    config_id = _register(projection)
    _new_version(projection, config_id)
    schedule = _schedule(projection, config_id)

    with pytest.raises(CredentialInUseError) as refused:
        projection.delete_stored_credential(name=CREDENTIAL)

    # The latest version names no credential, so only the schedule blocks.
    assert refused.value.configurations == ()
    assert refused.value.schedules == (schedule.name,)
    assert projection.lookup_stored_credential(CREDENTIAL) is not None


def test_a_deleted_schedule_does_not_block_the_deletion(projection: ProductProjection) -> None:
    _store(projection)
    config_id = _register(projection)
    _new_version(projection, config_id)
    schedule = _schedule(projection, config_id)
    projection.update_schedule(ScheduleRecord.model_validate({**schedule.model_dump(), "state": "deleted"}))

    projection.delete_stored_credential(name=CREDENTIAL)

    assert projection.lookup_stored_credential(CREDENTIAL) is None


def test_an_older_version_does_not_block_the_deletion(projection: ProductProjection) -> None:
    _store(projection)
    before = projection.lookup_stored_credential(CREDENTIAL)
    assert before is not None
    config_id = _register(projection)
    _new_version(projection, config_id)

    deleted = projection.delete_stored_credential(name=CREDENTIAL, event_for=_event())

    assert (deleted.name, deleted.current_revision) == (CREDENTIAL, 1)
    assert projection.lookup_stored_credential(CREDENTIAL) is None
    assert projection.lookup_stored_credential_revision(before.credential_id, 1) is None
    assert projection.list_stored_credentials().items == ()
    # The older version still names the credential: a run of it is refused before it starts.
    assert projection.version_credentials(config_id, 1) == {"source": CREDENTIAL}
    assert _deleted_events(projection) == 1


def test_a_credential_that_nothing_names_is_deleted_with_every_revision(projection: ProductProjection) -> None:
    _store(projection)
    projection.change_stored_credential_value(name=CREDENTIAL, fields=("token",), encrypt=_fake_encrypt, actor=_ADMIN)
    before = projection.lookup_stored_credential(CREDENTIAL)
    assert before is not None

    deleted = projection.delete_stored_credential(name=CREDENTIAL)

    assert deleted.current_revision == 2
    assert [projection.lookup_stored_credential_revision(before.credential_id, revision) for revision in (1, 2)] == [
        None,
        None,
    ]


def test_another_credential_stays(projection: ProductProjection) -> None:
    _store(projection)
    _store(projection, "infrahub-main")

    projection.delete_stored_credential(name=CREDENTIAL)

    assert [credential.name for credential in projection.list_stored_credentials().items] == ["infrahub-main"]


def test_the_deletion_of_a_credential_that_does_not_exist_is_refused(projection: ProductProjection) -> None:
    with pytest.raises(StoredCredentialNotFoundError):
        projection.delete_stored_credential(name=CREDENTIAL, event_for=_event())

    assert _deleted_events(projection) == 0


def test_a_credential_created_again_with_the_name_is_a_new_credential(projection: ProductProjection) -> None:
    _store(projection)
    first = projection.lookup_stored_credential(CREDENTIAL)
    projection.delete_stored_credential(name=CREDENTIAL)

    _store(projection)

    second = projection.lookup_stored_credential(CREDENTIAL)
    assert first is not None
    assert second is not None
    assert second.credential_id != first.credential_id
    assert second.current_revision == 1


# --- a deletion and a registration at the same time (critique E4) -------------------------


class _Held:
    """Hold one statement function of the store inside its transaction until the test lets it go."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, function: str) -> None:
        self.reached = threading.Event()
        self.go = threading.Event()
        original = getattr(credential_statements, function)

        def held(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - the arguments of the held function.
            self.reached.set()
            assert self.go.wait(ENDS_WITHIN)
            return original(*args, **kwargs)

        monkeypatch.setattr(credential_statements, function, held)


class _Call:
    """Run one call in a thread and keep what it returned or raised."""

    def __init__(self, call: Callable[[], object]) -> None:
        self.result: object = None
        self.error: BaseException | None = None
        self.done = threading.Event()
        self._call = call
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self.result = self._call()
        except BaseException as error:  # noqa: BLE001 - the test reads the error of the thread.
            self.error = error
        finally:
            self.done.set()

    def join(self) -> None:
        self._thread.join(ENDS_WITHIN)
        assert self.done.is_set()


def _versions_that_name(projection: ProductProjection, name: str) -> list[str]:
    """Return the configurations whose latest version names the credential."""
    named = []
    for summary in configs.list_configs(projection=projection).items:
        latest = summary.latest_version
        assert latest is not None
        if name in projection.version_credentials(summary.config_id, latest).values():
            named.append(summary.config_id)
    return named


def test_a_deletion_waits_for_a_registration_that_names_the_credential_and_is_then_refused(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(projection)
    # The registration holds the lock of the credential row: it checked the credential and has
    # not yet committed its version.
    registration_holds = _Held(monkeypatch, "write_version_credentials")
    registration = _Call(lambda: _register(projection))
    assert registration_holds.reached.wait(ENDS_WITHIN)

    deletion = _Call(lambda: projection.delete_stored_credential(name=CREDENTIAL, event_for=_event()))

    waits = not deletion.done.wait(WAITS_FOR)
    registration_holds.go.set()
    registration.join()
    deletion.join()
    assert waits
    assert registration.error is None
    assert isinstance(deletion.error, CredentialInUseError)
    assert deletion.error.configurations == (registration.result,)
    assert projection.lookup_stored_credential(CREDENTIAL) is not None
    assert _deleted_events(projection) == 0


def test_a_registration_waits_for_a_deletion_of_the_credential_it_names_and_is_then_refused(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(projection)
    # The deletion holds the lock of the credential row: it has not yet removed the credential.
    deletion_holds = _Held(monkeypatch, "read_in_use")
    deletion = _Call(lambda: projection.delete_stored_credential(name=CREDENTIAL, event_for=_event()))
    assert deletion_holds.reached.wait(ENDS_WITHIN)

    registration = _Call(lambda: _register(projection))

    waits = not registration.done.wait(WAITS_FOR)
    deletion_holds.go.set()
    deletion.join()
    registration.join()
    assert waits
    assert deletion.error is None
    assert isinstance(registration.error, configs.ConfigsValidationError)
    assert registration.error.reason == "credential-not-found"
    assert projection.lookup_stored_credential(CREDENTIAL) is None
    # No current version names the deleted credential.
    assert _versions_that_name(projection, CREDENTIAL) == []
    assert configs.list_configs(projection=projection).items == ()


# --- a schedule that pins a version whose credential does not exist -------------------------


def _config_with_deleted_credential(projection: ProductProjection) -> str:
    """Register version 1 that names the credential and version 2 that names none, then delete it."""
    _store(projection)
    config_id = _register(projection)
    _new_version(projection, config_id)
    projection.delete_stored_credential(name=CREDENTIAL)
    return config_id


def test_a_schedule_that_pins_a_version_whose_credential_was_deleted_is_refused(
    projection: ProductProjection,
) -> None:
    config_id = _config_with_deleted_credential(projection)

    with pytest.raises(VersionCredentialsMissingError) as refused:
        _schedule(projection, config_id, registry_version=1)

    assert refused.value.names == (CREDENTIAL,)
    assert CREDENTIAL in str(refused.value)
    assert projection.list_schedules(ScheduleFilters()).items == ()


def test_a_schedule_of_a_version_that_names_no_credential_is_stored(projection: ProductProjection) -> None:
    config_id = _config_with_deleted_credential(projection)

    schedule = _schedule(projection, config_id, registry_version=2)

    assert projection.lookup_schedule(schedule.schedule_id).value is not None


def test_a_change_to_a_version_whose_credential_was_deleted_is_refused(projection: ProductProjection) -> None:
    config_id = _config_with_deleted_credential(projection)
    schedule = _schedule(projection, config_id, registry_version=2)

    with pytest.raises(VersionCredentialsMissingError):
        projection.update_schedule(schedule.model_copy(update={"registry_version": 1}))

    stored = projection.lookup_schedule(schedule.schedule_id).value
    assert stored is not None
    assert stored.registry_version == 2


def test_a_change_that_keeps_the_version_does_not_read_the_credentials(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_id = _config_with_deleted_credential(projection)
    schedule = _schedule(projection, config_id, registry_version=2)

    def refuse(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - the arguments of the replaced function.
        del args, kwargs
        pytest.fail("a change that keeps the version must not lock credentials")

    monkeypatch.setattr(credential_statements, "lock_version_credentials", refuse)
    paused = projection.update_schedule(schedule.model_copy(update={"state": "paused"}))

    assert paused.state == "paused"


def test_a_deletion_waits_for_a_schedule_that_pins_the_credential_and_is_then_refused(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(projection)
    config_id = _register(projection)
    _new_version(projection, config_id)
    # The creation of the schedule holds the lock of the credential row: it checked the
    # credential and has not yet stored the schedule.
    reached = threading.Event()
    go = threading.Event()
    original = credential_statements.lock_version_credentials

    def held(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - the arguments of the held function.
        original(*args, **kwargs)
        reached.set()
        assert go.wait(ENDS_WITHIN)

    monkeypatch.setattr(credential_statements, "lock_version_credentials", held)
    record = _schedule_record(config_id, 1)
    creation = _Call(lambda: projection.create_schedule(record))
    assert reached.wait(ENDS_WITHIN)

    deletion = _Call(lambda: projection.delete_stored_credential(name=CREDENTIAL, event_for=_event()))

    waits = not deletion.done.wait(WAITS_FOR)
    go.set()
    creation.join()
    deletion.join()
    assert waits
    assert creation.error is None
    assert isinstance(deletion.error, CredentialInUseError)
    assert deletion.error.schedules == (record.name,)
    assert projection.lookup_stored_credential(CREDENTIAL) is not None


def test_a_schedule_waits_for_a_deletion_of_the_credential_it_pins_and_is_then_refused(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(projection)
    config_id = _register(projection)
    _new_version(projection, config_id)
    deletion_holds = _Held(monkeypatch, "read_in_use")
    deletion = _Call(lambda: projection.delete_stored_credential(name=CREDENTIAL, event_for=_event()))
    assert deletion_holds.reached.wait(ENDS_WITHIN)

    creation = _Call(lambda: _schedule(projection, config_id, registry_version=1))

    waits = not creation.done.wait(WAITS_FOR)
    deletion_holds.go.set()
    deletion.join()
    creation.join()
    assert waits
    assert deletion.error is None
    assert isinstance(creation.error, VersionCredentialsMissingError)
    assert projection.list_schedules(ScheduleFilters()).items == ()
