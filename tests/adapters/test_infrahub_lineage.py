"""Resolution of the lineage an import writes: import account, source, owner and protection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from infrahub_sdk.exceptions import NodeNotFoundError

from infrahub_sync.adapters.infrahub import (
    ImportAccount,
    ImportAccountUnreadableError,
    InfrahubAdapter,
    InfrahubLineageSettingsError,
    ResolvedLineage,
    ResolvedLineageNode,
    require_lineage_settings,
)
from infrahub_sync.configuration.capabilities import InfrahubLineageSettings, NamedLineageNode
from infrahub_sync.potenda import Potenda

IMPORT_ACCOUNT = {"id": "account-1", "display_label": "netbox-import"}


class LineageClient:
    """The two destination reads lineage resolution makes, recorded in order."""

    def __init__(self, *, profile: object = None, nodes: dict[tuple[str, str], str] | None = None) -> None:
        self.profile = {"AccountProfile": IMPORT_ACCOUNT} if profile is None else profile
        self.nodes = nodes or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_user(self) -> object:
        self.calls.append(("get_user", {}))
        return self.profile

    def get(self, **kwargs: Any) -> Any:  # noqa: ANN401
        self.calls.append(("get", kwargs))
        key = (kwargs["kind"], kwargs["name__value"])
        if key not in self.nodes:
            raise NodeNotFoundError(identifier={"name__value": [key[1]]}, node_type=key[0])
        return SimpleNamespace(id=self.nodes[key])


def make_adapter(client: LineageClient, settings: InfrahubLineageSettings | None = None) -> InfrahubAdapter:
    """The adapter with only the state lineage resolution reads, and no network setup."""
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.client = client
    if settings is not None:
        adapter.lineage_settings = settings
    return adapter


def test_without_settings_the_import_account_is_the_source_and_there_is_no_owner() -> None:
    """The source defaults to the account behind the token; no account is looked up by name."""
    client = LineageClient()
    adapter = make_adapter(client)

    lineage = adapter.resolve_lineage()

    assert lineage == ResolvedLineage(
        import_account=ImportAccount(id="account-1", name="netbox-import"),
        source=ResolvedLineageNode(kind="CoreAccount", name="netbox-import", id="account-1"),
        owner=None,
        protect=False,
    )
    assert adapter.lineage is lineage
    assert client.calls == [("get_user", {})]


def test_no_account_is_looked_up_by_the_source_systems_name() -> None:
    """FR-005: an account named after the source system exists, and the import does not read it."""
    client = LineageClient(nodes={("CoreAccount", "netbox"): "account-netbox"})
    adapter = make_adapter(client)
    adapter.config = SimpleNamespace(source=SimpleNamespace(name="netbox", adapter="netbox"))  # ty: ignore[invalid-assignment]

    lineage = adapter.resolve_lineage()

    assert lineage.source.id == "account-1", "the source is the import account, not the account named `netbox`"
    assert [name for name, _ in client.calls] == ["get_user"], "no `CoreAccount` lookup by name is made"


def _loading_adapter(client: LineageClient, events: list[str]) -> InfrahubAdapter:
    """A real adapter whose `load` only records that it ran, so a plan can be run over it."""
    adapter = make_adapter(client)
    adapter.load = lambda: events.append("destination.load")  # ty: ignore[invalid-assignment]
    return adapter


@pytest.mark.parametrize("concurrent_load", [True, False])
def test_a_plan_reads_the_import_account_once(tmp_path: Path, *, concurrent_load: bool) -> None:
    """FR-001: one `get_user()` per plan run, before either side is loaded, and no other read."""
    events: list[str] = []
    client = LineageClient()
    destination = _loading_adapter(client, events)
    engine = Potenda(
        source=_ordered_side("source", events),
        destination=destination,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
        concurrent_load=concurrent_load,
    )

    engine.load_both_sides()

    assert client.calls == [("get_user", {})]
    assert destination.lineage is not None
    assert destination.lineage.source.id == "account-1"
    assert sorted(events) == ["destination.load", "source.load"]


def test_named_source_and_owner_are_looked_up_by_kind_and_name() -> None:
    """Each named node is read with one `get(kind=..., name__value=...)` and its id is kept."""
    client = LineageClient(
        nodes={("CoreRepository", "inventory"): "repo-1", ("CoreAccountGroup", "network-team"): "group-1"}
    )
    settings = InfrahubLineageSettings(
        source=NamedLineageNode(kind="CoreRepository", name="inventory"),
        owner=NamedLineageNode(kind="CoreAccountGroup", name="network-team"),
        protect=True,
    )
    adapter = make_adapter(client, settings)

    lineage = adapter.resolve_lineage()

    assert lineage.import_account.id == "account-1"
    assert lineage.source == ResolvedLineageNode(kind="CoreRepository", name="inventory", id="repo-1")
    assert lineage.owner == ResolvedLineageNode(kind="CoreAccountGroup", name="network-team", id="group-1")
    assert lineage.protect is True
    assert client.calls == [
        ("get_user", {}),
        ("get", {"kind": "CoreRepository", "name__value": "inventory"}),
        ("get", {"kind": "CoreAccountGroup", "name__value": "network-team"}),
    ]


@pytest.mark.parametrize("setting", ["source", "owner"])
def test_a_named_node_that_does_not_exist_stops_resolution_without_a_fallback(setting: str) -> None:
    """A missing named node raises; the import account is not used in its place."""
    client = LineageClient()
    named = NamedLineageNode(kind="CoreAccountGroup", name="missing")
    adapter = make_adapter(client, InfrahubLineageSettings.model_validate({setting: named}))

    with pytest.raises(NodeNotFoundError):
        adapter.resolve_lineage()

    assert adapter.lineage is None


@pytest.mark.parametrize(
    "profile",
    [{}, {"AccountProfile": None}, {"AccountProfile": {"display_label": "x"}}, {"AccountProfile": {"id": ""}}],
)
def test_an_account_profile_without_an_id_is_refused(profile: object) -> None:
    """An answer to `get_user()` that carries no account id stops resolution."""
    adapter = make_adapter(LineageClient(profile=profile))

    with pytest.raises(ImportAccountUnreadableError):
        adapter.resolve_lineage()


def test_an_account_profile_without_a_label_is_named_by_its_id() -> None:
    """The account name falls back to the id when Infrahub returns no display label."""
    adapter = make_adapter(LineageClient(profile={"AccountProfile": {"id": "account-9"}}))

    assert adapter.resolve_lineage().import_account == ImportAccount(id="account-9", name="account-9")


def test_refused_lineage_settings_stop_the_adapter_with_each_refusal_named() -> None:
    """The adapter refuses settings the parser refuses, and names every refused setting."""
    with pytest.raises(InfrahubLineageSettingsError) as caught:
        require_lineage_settings({"owner": "network-team", "protect": "yes"})

    assert "`owner`" in str(caught.value)
    assert "`protect`" in str(caught.value)


def _ordered_side(label: str, events: list[str]) -> MagicMock:
    side = MagicMock()
    side.top_level = []
    side.load.side_effect = lambda: events.append(f"{label}.load")
    return side


@pytest.mark.parametrize("concurrent_load", [True, False])
def test_a_plan_resolves_lineage_before_either_side_is_loaded(tmp_path: Path, *, concurrent_load: bool) -> None:
    """Lineage is resolved before the two loads are submitted, in both load modes."""
    events: list[str] = []
    source = _ordered_side("source", events)
    destination = _ordered_side("destination", events)
    destination.resolve_lineage.side_effect = lambda: events.append("resolve_lineage")
    engine = Potenda(
        source=source,
        destination=destination,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
        concurrent_load=concurrent_load,
    )

    engine.load_both_sides()

    assert events[0] == "resolve_lineage"
    assert sorted(events[1:]) == ["destination.load", "source.load"]


@pytest.mark.parametrize("concurrent_load", [True, False])
def test_a_lineage_refusal_stops_the_plan_before_the_source_is_read(tmp_path: Path, *, concurrent_load: bool) -> None:
    """When lineage resolution raises, neither side is loaded."""
    events: list[str] = []
    source = _ordered_side("source", events)
    destination = _ordered_side("destination", events)
    destination.resolve_lineage.side_effect = NodeNotFoundError(
        identifier={"name__value": ["missing"]}, node_type="CoreAccountGroup"
    )
    engine = Potenda(
        source=source,
        destination=destination,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
        concurrent_load=concurrent_load,
    )

    with pytest.raises(NodeNotFoundError):
        engine.load_both_sides()

    assert events == []


def test_a_destination_without_lineage_is_loaded_without_resolution(tmp_path: Path) -> None:
    """A destination that has no `resolve_lineage` is loaded as before."""
    events: list[str] = []

    class PlainSide:
        def __init__(self, label: str) -> None:
            self.label = label
            self.top_level: list[str] = []

        def load(self) -> None:
            events.append(f"{self.label}.load")

    engine = Potenda(
        source=PlainSide("source"),  # ty: ignore[invalid-argument-type]
        destination=PlainSide("destination"),  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
        concurrent_load=False,
    )

    engine.load_both_sides()

    assert events == ["source.load", "destination.load"]
