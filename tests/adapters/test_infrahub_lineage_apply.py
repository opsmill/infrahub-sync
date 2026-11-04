"""Apply compares its import account with the account the saved plan recorded.

An apply refuses, before any write, a plan made with another account's token, whether or not
the package names a source, and a saved plan that records no import account at all.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from infrahub_sdk.exceptions import NodeNotFoundError
from infrahub_sdk.schema import NodeSchemaAPI
from infrahub_sdk.schema.main import BranchSchema

from infrahub_sync.adapters.infrahub import NamedLineageNodeNotFoundError
from infrahub_sync.configuration.infrahub_lineage import InfrahubLineageSettings, NamedLineageNode
from infrahub_sync.plan.errors import ImportAccountMismatchError, PlanLineageMissingError
from infrahub_sync.plan.models import LineageAccountRecord, LineageNodeRecord, PlanLineageRecord
from infrahub_sync.plan.write_surface import destination_lineage_record
from tests.adapters.test_infrahub_planned_write import (
    IMPORT_ACCOUNT_ID,
    SCHEMAS,
    SITE_KIND,
    RecordingClient,
    apply_run_dir,
    engine_over,
    make_adapter,
)
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact
from tests.plan.ownership_fixtures import granted_ownership

if TYPE_CHECKING:
    from pathlib import Path

ADAPTER_LOGGER = "infrahub_sync.adapters.infrahub"
APPLY_RUN_ID = "20260727T1130-77e0b4c2"

OTHER_ACCOUNT = {"name": "planner-account", "id": "planner-account-1"}
NAMED_SOURCE = NamedLineageNode(kind="CoreAccountGroup", name="network-team")


def _lineage(
    *, account: dict[str, str], source: dict[str, str] | None = None, owner: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "import_account": account,
        "source": source or {"kind": "CoreAccount", "name": account["name"]},
        "owner": owner,
        "protect": False,
    }


class DeletedNamedNodeClient(RecordingClient):
    """A destination where the named node the plan resolved has since been deleted."""

    def __init__(self) -> None:
        super().__init__()
        group = NodeSchemaAPI(id="group-schema", name="AccountGroup", namespace="Core", attributes=[], relationships=[])
        self.live_schema = BranchSchema(hash="fixture", nodes={**SCHEMAS, NAMED_SOURCE.kind: group})
        self.schema.set_cache(self.live_schema)

    def get(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if kwargs.get("kind") == NAMED_SOURCE.kind:
            self.events.append(("get", kwargs))
            raise NodeNotFoundError(identifier={"name__value": [kwargs["name__value"]]}, node_type=kwargs["kind"])
        return super().get(*args, **kwargs)


def _plan(tmp_path: Path, **manifest_overrides: Any) -> Path:  # noqa: ANN401 — a manifest field's value is any JSON value
    directory = apply_run_dir(tmp_path)
    operation = operation_record(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    write_artifact(directory, [operation], run_id=APPLY_RUN_ID, source_snapshot=[], **manifest_overrides)
    return directory


def _apply(directory: Path, client: RecordingClient, settings: InfrahubLineageSettings | None = None) -> None:
    adapter = make_adapter(client)
    if settings is not None:
        adapter.lineage_settings = settings
    engine_over(directory, adapter, run_id=APPLY_RUN_ID).apply_plan(
        ownership=granted_ownership(), config_version=CONFIG_VERSION
    )


@pytest.mark.parametrize("settings", [None, InfrahubLineageSettings(source=NAMED_SOURCE)], ids=["no-source", "named"])
def test_an_apply_with_another_import_account_is_refused_before_any_write(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, settings: InfrahubLineageSettings | None
) -> None:
    """The cause names the account the plan recorded and the account the apply runs with."""
    source = None if settings is None else {"kind": NAMED_SOURCE.kind, "name": NAMED_SOURCE.name}
    directory = _plan(tmp_path, lineage=_lineage(account=OTHER_ACCOUNT, source=source))
    client = RecordingClient()

    with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER), pytest.raises(ImportAccountMismatchError) as raised:
        _apply(directory, client, settings)

    message = str(raised.value)
    assert "planner-account" in message
    assert "planner-account-1" in message
    assert "import-account" in message
    assert IMPORT_ACCOUNT_ID in message
    assert raised.value.wrote is False
    assert client.mutations == []
    assert [name for name, _ in client.events if name == "get"] == [], "no named node is read after the refusal"
    assert any("import account" in record.getMessage() for record in caplog.records)


def test_a_saved_plan_with_no_recorded_import_account_is_refused_before_any_write(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A plan made before the import account was recorded is refused, and the cause asks for a new plan."""
    directory = _plan(tmp_path)
    client = RecordingClient()

    with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER), pytest.raises(PlanLineageMissingError) as raised:
        _apply(directory, client)

    message = str(raised.value)
    assert "no import account" in message
    assert "new plan" in raised.value.next_action
    assert raised.value.wrote is False
    assert client.mutations == []
    assert client.events == [], "the refusal reads nothing from the destination"
    assert any("no import account" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("setting", ["source", "owner"])
def test_a_named_node_deleted_after_the_plan_stops_apply_before_any_write(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, setting: str
) -> None:
    """Apply reads the named node again, and its absence stops apply with the plan's cause."""
    named = {"kind": NAMED_SOURCE.kind, "name": NAMED_SOURCE.name}
    account = {"name": "import-account", "id": IMPORT_ACCOUNT_ID}
    directory = _plan(tmp_path, lineage=_lineage(account=account, **{setting: named}))
    settings = InfrahubLineageSettings.model_validate({setting: NAMED_SOURCE})
    client = DeletedNamedNodeClient()

    with (
        caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER),
        pytest.raises(NamedLineageNodeNotFoundError) as raised,
    ):
        _apply(directory, client, settings)

    plan_adapter = make_adapter(DeletedNamedNodeClient())
    plan_adapter.lineage_settings = settings
    with pytest.raises(NamedLineageNodeNotFoundError) as planned:
        plan_adapter.resolve_lineage()

    assert str(raised.value) == str(planned.value), "apply records the same cause as a plan"
    assert (
        str(raised.value) == f"The setting `{setting}` names `CoreAccountGroup` `network-team`, which does not exist."
    )
    assert raised.value.wrote is False
    assert client.mutations == []
    assert [name for name, _ in client.events] == ["account_profile", "get"], (
        "nothing is read or written after the stop"
    )
    assert any(getattr(record, "lineage_setting", None) == setting for record in caplog.records)


def test_an_apply_with_the_recorded_import_account_writes(tmp_path: Path) -> None:
    directory = _plan(tmp_path, lineage=_lineage(account={"name": "import-account", "id": IMPORT_ACCOUNT_ID}))
    client = RecordingClient()

    _apply(directory, client)

    assert len(client.mutations) == 1


def test_a_plan_records_the_lineage_the_destination_resolved() -> None:
    """The manifest's `lineage` is what `resolve_lineage` resolved at the start of the plan."""
    adapter = make_adapter(RecordingClient())
    adapter.lineage = None
    assert destination_lineage_record(adapter) is None, "nothing is recorded before resolution"

    adapter.resolve_lineage()

    assert destination_lineage_record(adapter) == PlanLineageRecord(
        import_account=LineageAccountRecord(name="import-account", id=IMPORT_ACCOUNT_ID),
        source=LineageNodeRecord(kind="CoreAccount", name="import-account"),
        owner=None,
        protect=False,
    )


@pytest.mark.parametrize("destination", [object(), MagicMock()], ids=["plain", "mock"])
def test_a_destination_that_records_no_lineage_writes_no_field(destination: object) -> None:
    assert destination_lineage_record(destination) is None
