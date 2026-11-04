"""Resolution of the lineage an import writes: import account, source, owner and protection."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from infrahub_sdk.exceptions import NodeNotFoundError, SchemaNotFoundError

from infrahub_sync import SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import (
    IMPORT_ACCOUNT_QUERY,
    ImportAccount,
    ImportAccountUnreadableError,
    InfrahubAdapter,
    InfrahubLineageSettingsError,
    NamedLineageNodeNotFoundError,
    ResolvedLineage,
    ResolvedLineageNode,
    require_lineage_settings,
)
from infrahub_sync.configuration.capabilities import InfrahubLineageSettings, NamedLineageNode
from infrahub_sync.potenda import Potenda

IMPORT_ACCOUNT = {"id": "account-1", "name": {"value": "netbox-import"}}
ACCOUNT_PROFILE_CALL = ("account_profile", {})
# A plan reads the server version once, after the lineage is resolved.
VERSION_CALL = ("version", {})
ADAPTER_LOGGER = "infrahub_sync.adapters.infrahub"

# The relationships of each kind a setting may name, as the destination schema lists them. A
# lookup by name leaves every one of them out, so it needs `view` on the named kind only.
RELATIONSHIPS = {
    "CoreAccount": ("member_of_groups", "subscriber_of_groups", "profiles"),
    "CoreAccountGroup": ("roles", "members", "subscribers", "parent", "children"),
    "CoreRepository": ("credential", "tags", "transformations", "queries", "checks", "generators"),
    "CoreReadOnlyRepository": ("credential", "tags", "transformations", "queries", "checks", "generators"),
}


def name_only_lookup(kind: str, name: str) -> tuple[str, dict[str, Any]]:
    """The one `get` a named node is looked up with: by name, selecting the name and no relationship."""
    return ("get", {"kind": kind, "name__value": name, "include": ["name"], "exclude": list(RELATIONSHIPS[kind])})


class LineageSchema:
    """The cached destination schema, answering the relationships of each kind."""

    @staticmethod
    def get(*, kind: str, **_: Any) -> SimpleNamespace:  # noqa: ANN401
        return SimpleNamespace(relationships=[SimpleNamespace(name=name) for name in RELATIONSHIPS[kind]])


class LineageClient:
    """The destination reads lineage resolution makes, and the version read of a plan, recorded in order."""

    def __init__(self, *, profile: object = None, nodes: dict[tuple[str, str], str] | None = None) -> None:
        self.profile = {"AccountProfile": IMPORT_ACCOUNT} if profile is None else profile
        self.nodes = nodes or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.schema = LineageSchema()

    def execute_graphql(self, *, query: str) -> object:
        """Take only the arguments infrahub-sdk 1.18 accepts: `operation_name` arrived in 1.22."""
        assert query == IMPORT_ACCOUNT_QUERY, f"lineage resolution sends only the import account query, not {query!r}"
        self.calls.append(("account_profile", {}))
        return self.profile

    def get_version(self) -> str:
        self.calls.append(VERSION_CALL)
        return "1.11.9"

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
    assert client.calls == [ACCOUNT_PROFILE_CALL, VERSION_CALL]


def test_no_account_is_looked_up_by_the_source_systems_name() -> None:
    """An account named after the source system exists, and the import does not read it."""
    client = LineageClient(nodes={("CoreAccount", "netbox"): "account-netbox"})
    adapter = make_adapter(client)
    adapter.config = SimpleNamespace(source=SimpleNamespace(name="netbox", adapter="netbox"))  # ty: ignore[invalid-assignment]

    lineage = adapter.resolve_lineage()

    assert lineage.source.id == "account-1", "the source is the import account, not the account named `netbox`"
    assert [name for name, _ in client.calls] == ["account_profile", "version"], (
        "no `CoreAccount` lookup by name is made"
    )


def _loading_adapter(client: LineageClient, events: list[str]) -> InfrahubAdapter:
    """A real adapter whose `load` only records that it ran, so a plan can be run over it."""
    adapter = make_adapter(client)
    adapter.load = lambda: events.append("destination.load")  # ty: ignore[invalid-assignment]
    return adapter


@pytest.mark.parametrize("concurrent_load", [True, False])
def test_a_plan_reads_the_import_account_once(tmp_path: Path, *, concurrent_load: bool) -> None:
    """One import account query per plan run, before either side is loaded, and one server version read."""
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

    assert client.calls == [ACCOUNT_PROFILE_CALL, VERSION_CALL]
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
        ACCOUNT_PROFILE_CALL,
        name_only_lookup("CoreRepository", "inventory"),
        name_only_lookup("CoreAccountGroup", "network-team"),
        VERSION_CALL,
    ]


def test_a_node_named_as_both_source_and_owner_is_read_once() -> None:
    """When `source` and `owner` name the same node, one lookup resolves both."""
    client = LineageClient(nodes={("CoreRepository", "inventory"): "repo-1"})
    named = NamedLineageNode(kind="CoreRepository", name="inventory")
    adapter = make_adapter(client, InfrahubLineageSettings(source=named, owner=named))

    lineage = adapter.resolve_lineage()

    expected = ResolvedLineageNode(kind="CoreRepository", name="inventory", id="repo-1")
    assert lineage.source == expected
    assert lineage.owner == expected
    assert client.calls == [ACCOUNT_PROFILE_CALL, name_only_lookup("CoreRepository", "inventory"), VERSION_CALL]


@pytest.mark.parametrize("kind", sorted(RELATIONSHIPS))
def test_a_named_node_is_read_with_its_name_only(kind: str) -> None:
    """The lookup selects `name` and excludes every relationship, so it needs `view` on the kind only.

    With the SDK's default fields, a `CoreAccountGroup` lookup also needs `view` on
    `CoreAccountRole` and `CoreGroup`, and a repository lookup `view` on credentials and tags.
    """
    client = LineageClient(nodes={(kind, "named"): "node-1"})
    adapter = make_adapter(client, InfrahubLineageSettings(owner=NamedLineageNode(kind=kind, name="named")))  # ty: ignore[invalid-argument-type]

    adapter.resolve_lineage()

    (_, lookup) = client.calls[1]
    assert lookup["include"] == ["name"]
    assert sorted(lookup["exclude"]) == sorted(RELATIONSHIPS[kind])
    assert set(lookup) == {"kind", "name__value", "include", "exclude"}


def test_a_named_account_is_looked_up_and_a_named_group_needs_no_account_read() -> None:
    """A named `CoreAccount` source is read by name; a named group owner reads no account."""
    client = LineageClient(
        nodes={("CoreAccount", "netbox"): "account-netbox", ("CoreAccountGroup", "network-team"): "group-1"}
    )
    adapter = make_adapter(client, InfrahubLineageSettings(source=NamedLineageNode(kind="CoreAccount", name="netbox")))

    assert adapter.resolve_lineage().source == ResolvedLineageNode(
        kind="CoreAccount", name="netbox", id="account-netbox"
    )
    assert client.calls == [ACCOUNT_PROFILE_CALL, name_only_lookup("CoreAccount", "netbox"), VERSION_CALL]

    client.calls.clear()
    adapter.lineage_settings = InfrahubLineageSettings(
        owner=NamedLineageNode(kind="CoreAccountGroup", name="network-team")
    )

    adapter.resolve_lineage()

    assert [call for call in client.calls if call[0] == "get"] == [name_only_lookup("CoreAccountGroup", "network-team")]
    assert all(call[1].get("kind") != "CoreAccount" for call in client.calls), "a named group needs no account read"


@pytest.mark.parametrize("setting", ["source", "owner"])
def test_a_named_node_that_does_not_exist_stops_resolution_without_a_fallback(
    setting: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The cause names the setting, the kind and the name; no other node is used in its place."""
    client = LineageClient(nodes={("CoreAccountGroup", "fallback"): "group-fallback"})
    named = NamedLineageNode(kind="CoreAccountGroup", name="missing")
    adapter = make_adapter(client, InfrahubLineageSettings.model_validate({setting: named}))

    with (
        caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER),
        pytest.raises(NamedLineageNodeNotFoundError) as raised,
    ):
        adapter.resolve_lineage()

    assert str(raised.value) == f"The setting `{setting}` names `CoreAccountGroup` `missing`, which does not exist."
    assert (raised.value.setting, raised.value.kind, raised.value.name) == (setting, "CoreAccountGroup", "missing")
    assert isinstance(raised.value.__cause__, NodeNotFoundError)
    assert adapter.lineage is None, "no lineage is kept, so nothing can be written with a fallback node"
    assert [call for call in client.calls if call[0] == "get"] == [name_only_lookup("CoreAccountGroup", "missing")]
    (record,) = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert record.__dict__["lineage_setting"] == setting
    assert record.__dict__["lineage_kind"] == "CoreAccountGroup"
    assert record.__dict__["lineage_name"] == "missing"


@pytest.mark.parametrize("setting", ["source", "owner"])
def test_a_named_kind_missing_from_the_destination_schema_stops_resolution_naming_the_setting(setting: str) -> None:
    """A kind the branch's schema does not have stops the run with the setting's cause, not an SDK error."""

    class MissingKindSchema:
        @staticmethod
        def get(*, kind: str, **_: Any) -> SimpleNamespace:  # noqa: ANN401
            raise SchemaNotFoundError(identifier=kind)

    client = LineageClient()
    client.schema = MissingKindSchema()  # ty: ignore[invalid-assignment]
    named = NamedLineageNode(kind="CoreAccountGroup", name="network-team")
    adapter = make_adapter(client, InfrahubLineageSettings.model_validate({setting: named}))

    with pytest.raises(NamedLineageNodeNotFoundError) as raised:
        adapter.resolve_lineage()

    assert str(raised.value) == (
        f"The setting `{setting}` names `CoreAccountGroup` `network-team`, which does not exist. "
        "The destination schema has no kind `CoreAccountGroup`."
    )
    assert isinstance(raised.value.__cause__, SchemaNotFoundError)
    assert f"the setting `{setting}`" in raised.value.next_action
    assert adapter.lineage is None
    assert [call for call in client.calls if call[0] == "get"] == [], "no node is read for a kind that is missing"


@pytest.mark.parametrize(
    "profile",
    [{}, {"AccountProfile": None}, {"AccountProfile": {"name": {"value": "x"}}}, {"AccountProfile": {"id": ""}}],
)
def test_an_account_profile_without_an_id_is_refused(profile: object) -> None:
    """An answer to the import account query that carries no account id stops resolution."""
    adapter = make_adapter(LineageClient(profile=profile))

    with pytest.raises(ImportAccountUnreadableError):
        adapter.resolve_lineage()


def test_an_account_profile_without_a_name_is_named_by_its_id() -> None:
    """The account name falls back to the id when Infrahub returns no name."""
    adapter = make_adapter(LineageClient(profile={"AccountProfile": {"id": "account-9"}}))

    assert adapter.resolve_lineage().import_account == ImportAccount(id="account-9", name="account-9")


def test_the_import_account_is_named_by_its_name_and_not_its_display_label() -> None:
    """Infrahub labels the account `netbox` as `Netbox`; plans and refusals name the account `netbox`."""
    profile = {"AccountProfile": {"id": "account-1", "display_label": "Netbox", "name": {"value": "netbox"}}}
    adapter = make_adapter(LineageClient(profile=profile))

    lineage = adapter.resolve_lineage()

    assert lineage.import_account == ImportAccount(id="account-1", name="netbox")
    assert lineage.source == ResolvedLineageNode(kind="CoreAccount", name="netbox", id="account-1")


def test_the_import_account_query_selects_the_account_name() -> None:
    """The query reads `AccountProfile`, which needs no permission on accounts, and selects `name`."""
    assert "AccountProfile" in IMPORT_ACCOUNT_QUERY
    assert "name { value }" in IMPORT_ACCOUNT_QUERY


def test_refused_lineage_settings_stop_the_adapter_with_each_refusal_named() -> None:
    """The adapter refuses settings the parser refuses, and names every refused setting."""
    with pytest.raises(InfrahubLineageSettingsError) as caught:
        require_lineage_settings({"owner": "network-team", "protect": "yes"})

    assert "`owner`" in str(caught.value)
    assert "`protect`" in str(caught.value)


class _SchemaOnlyClient:
    """The client `InfrahubAdapter.__init__` builds: it reads the schema and nothing else."""

    def __init__(self, **_kwargs: Any) -> None:  # noqa: ANN401
        self.schema = SimpleNamespace(all=lambda **_: {})


@pytest.mark.parametrize(
    ("target", "refused"),
    [pytest.param("destination", True, id="destination"), pytest.param("source", False, id="source")],
)
def test_only_a_destination_adapter_refuses_lineage_settings(
    monkeypatch: pytest.MonkeyPatch, target: str, *, refused: bool
) -> None:
    """Registration checks `source`, `owner` and `protect` for the destination role only, so a source adapter keeps the defaults."""
    monkeypatch.setattr("infrahub_sync.adapters.infrahub.InfrahubClientSync", _SchemaOnlyClient)
    settings = {"url": "http://infrahub.invalid", "token": "test-token", "owner": "network-team", "protect": "yes"}
    adapter = SyncAdapter(name="infrahub", settings=settings)
    config = SyncConfig(name="lineage-roles", source=adapter, destination=adapter, schema_mapping=[])

    if refused:
        with pytest.raises(InfrahubLineageSettingsError):
            InfrahubAdapter(target=target, adapter=adapter, config=config)
    else:
        assert (
            InfrahubAdapter(target=target, adapter=adapter, config=config).lineage_settings == InfrahubLineageSettings()
        )


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
    # Assigned, not configured through attribute access: on Python 3.12 and later, the
    # `runtime_checkable` protocol check reads attributes statically and does not find a
    # child mock that `MagicMock` creates on first access.
    destination.resolve_lineage = MagicMock(side_effect=lambda **_: events.append("resolve_lineage"))
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
    destination.resolve_lineage = MagicMock(
        side_effect=NodeNotFoundError(identifier={"name__value": ["missing"]}, node_type="CoreAccountGroup")
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


@pytest.mark.parametrize("setting", ["source", "owner"])
@pytest.mark.parametrize("concurrent_load", [True, False])
def test_a_missing_named_node_stops_the_plan_before_the_source_adapter_loads(
    tmp_path: Path, setting: str, *, concurrent_load: bool
) -> None:
    """With the real adapter as destination, a missing named node stops the plan before either load."""
    events: list[str] = []
    destination = _loading_adapter(LineageClient(), events)
    destination.lineage_settings = InfrahubLineageSettings.model_validate(
        {setting: NamedLineageNode(kind="CoreAccountGroup", name="missing")}
    )
    source = _ordered_side("source", events)
    engine = Potenda(
        source=source,
        destination=destination,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
        concurrent_load=concurrent_load,
    )

    with pytest.raises(NamedLineageNodeNotFoundError, match=f"`{setting}` names `CoreAccountGroup` `missing`"):
        engine.load_both_sides()

    assert events == []
    source.load.assert_not_called()


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
