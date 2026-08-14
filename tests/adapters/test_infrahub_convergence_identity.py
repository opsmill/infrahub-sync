"""Regression coverage for Infrahub convergence-identity validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from diffsync import Adapter, Diff
from diffsync.diff import DiffElement

from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import ConvergenceIdentityError, InfrahubAdapter


def _two_rack_create_diff() -> Diff:
    """Build the production-shaped diff for two same-named racks in different sites."""
    diff = Diff()
    for site in ("atlanta", "boston"):
        rack = DiffElement(
            obj_type="LocationRack",
            name=f"rack-a__{site}",
            keys={"name": "rack-a", "site": site},
        )
        rack.add_attrs(source={"description": f"Rack in {site}"})
        diff.add(rack)
    return diff


def test_finer_mapping_identity_is_refused_before_destination_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `(name, site)` rack identity must not converge onto destination key `(name)`."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="LocationRack",
                identifiers=["name", "site"],
            )
        ],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(
            human_friendly_id=["name__value"],
            uniqueness_constraints=[["name__value"]],
        )
    }
    destination.client = SimpleNamespace(create=pytest.fail)
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    with pytest.raises(ConvergenceIdentityError) as exc_info:
        destination.sync_from(Adapter(), diff=_two_rack_create_diff())

    message = str(exc_info.value)
    assert "LocationRack" in message
    assert "uncovered mapping identifier(s): site" in message
    assert entered_write_path is False


def test_empty_generated_identity_is_refused_before_destination_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A create model with no identity cannot enter the id-less upsert path."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="unkeyed-sync",
        source=SyncAdapter(name="source"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="UnkeyedThing", identifiers=[])],
    )
    destination.schema = {"UnkeyedThing": SimpleNamespace(human_friendly_id=[], default_filter=None)}
    destination.UnkeyedThing = type("UnkeyedThing", (), {"_identifiers": ()})
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)
    diff = Diff()
    element = DiffElement(obj_type="UnkeyedThing", name="unkeyed", keys={})
    element.add_attrs(source={"description": "unsafe"})
    diff.add(element)

    with pytest.raises(ConvergenceIdentityError, match="has no identity"):
        destination.sync_from(Adapter(), diff=diff)

    assert entered_write_path is False


def test_null_generated_identity_value_is_refused_before_destination_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured identifier still cannot key an upsert when its create value is null."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="null-key-sync",
        source=SyncAdapter(name="source"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="Thing", identifiers=["name"])],
    )
    destination.schema = {"Thing": SimpleNamespace(human_friendly_id=["name__value"])}
    destination.Thing = type("Thing", (), {"_identifiers": ("name",)})
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)
    diff = Diff()
    element = DiffElement(obj_type="Thing", name="null-key", keys={"name": None})
    element.add_attrs(source={"description": "unsafe"})
    diff.add(element)

    with pytest.raises(ConvergenceIdentityError, match="missing or null"):
        destination.sync_from(Adapter(), diff=diff)

    assert entered_write_path is False


def test_omitted_diff_is_computed_then_validated_before_destination_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public `diff=None` path cannot let DiffSync compute and immediately write a null key."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="null-key-sync",
        source=SyncAdapter(name="source"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="Thing", identifiers=["name"])],
    )
    destination.schema = {"Thing": SimpleNamespace(human_friendly_id=["name__value"])}
    destination.Thing = type("Thing", (), {"_identifiers": ("name",)})
    computed = Diff()
    element = DiffElement(obj_type="Thing", name="null-key", keys={"name": None})
    element.add_attrs(source={"description": "unsafe"})
    computed.add(element)
    entered_write_path = False

    monkeypatch.setattr(InfrahubAdapter, "diff_from", lambda *_args, **_kwargs: computed)

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    with pytest.raises(ConvergenceIdentityError, match="missing or null"):
        destination.sync_from(Adapter())

    assert entered_write_path is False


def test_covering_uniqueness_constraint_does_not_override_coarser_hfid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upsert matches by HFID, so a covering constraint cannot make a coarse HFID safe."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="LocationRack",
                identifiers=["name", "site"],
            )
        ],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(
            human_friendly_id=["name__value"],
            uniqueness_constraints=[["name__value", "site__name__value"]],
        )
    }
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    with pytest.raises(ValueError):
        destination.sync_from(Adapter(), diff=_two_rack_create_diff())

    assert entered_write_path is False


def test_covering_hfid_allows_destination_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HFID covering `(name, site)` preserves the identity used by upsert."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="LocationRack", identifiers=["name", "site"])],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(
            human_friendly_id=["name__value", "site__name__value"],
            uniqueness_constraints=[["name__value"]],
        )
    }
    entered_write_path = False

    observed_keys: list[dict[str, str]] = []

    def _enter_write_path(*_args: object, **kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        supplied_diff = kwargs["diff"]
        assert isinstance(supplied_diff, Diff)
        observed_keys.extend(child.keys for child in supplied_diff.get_children())
        return supplied_diff

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    destination.sync_from(Adapter(), diff=_two_rack_create_diff())

    assert entered_write_path is True
    assert observed_keys == [
        {"name": "rack-a", "site": "atlanta"},
        {"name": "rack-a", "site": "boston"},
    ]


def test_generated_model_identity_is_validated_when_config_identifiers_are_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generated model's actual identity must drive the pre-write check."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="LocationRack")],
    )
    destination.LocationRack = SimpleNamespace(_identifiers=("name", "site"))
    destination.schema = {
        "LocationRack": SimpleNamespace(
            human_friendly_id=["name__value"],
            uniqueness_constraints=[["name__value"]],
        )
    }
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    with pytest.raises(ValueError):
        destination.sync_from(Adapter(), diff=_two_rack_create_diff())

    assert entered_write_path is False


def test_generated_model_identity_prevents_a_stale_config_false_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checked-in model keyed by `name` must not be judged by stale composite config."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="LocationRack", identifiers=["name", "site"])],
    )
    destination.LocationRack = SimpleNamespace(_identifiers=("name",))
    destination.schema = {
        "LocationRack": SimpleNamespace(
            human_friendly_id=["name__value"],
            uniqueness_constraints=[["name__value"]],
        )
    }
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    destination.sync_from(Adapter(), diff=_two_rack_create_diff())

    assert entered_write_path is True


def test_default_filter_is_validated_when_destination_has_no_hfid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an HFID, Infrahub upsert matches on the schema's default filter."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="LocationRack", identifiers=["name", "site"])],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(
            human_friendly_id=None,
            default_filter="name__value",
            uniqueness_constraints=[],
        )
    }
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    with pytest.raises(ValueError):
        destination.sync_from(Adapter(), diff=_two_rack_create_diff())

    assert entered_write_path is False


def test_missing_upsert_key_is_refused_before_destination_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A destination without any match key cannot prove a composite identity is safe."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="LocationRack", identifiers=["name", "site"])],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(
            human_friendly_id=None,
            default_filter=None,
            uniqueness_constraints=[["name__value"]],
        )
    }
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    with pytest.raises(ValueError) as exc_info:
        destination.sync_from(Adapter(), diff=_two_rack_create_diff())

    assert "destination upsert key (none)" in str(exc_info.value)
    assert "uncovered mapping identifier(s): name, site" in str(exc_info.value)
    assert entered_write_path is False


def test_relationship_hfid_must_cover_full_peer_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A relationship key by peer name is unsafe when the peer also needs tenant."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="LocationRack", identifiers=["name", "site"])],
    )
    destination.LocationRack = SimpleNamespace(_identifiers=("name", "site"))
    destination.LocationSite = SimpleNamespace(_identifiers=("name", "tenant"))
    destination.schema = {
        "LocationRack": SimpleNamespace(
            human_friendly_id=["name__value", "site__name__value"],
            relationships=[SimpleNamespace(name="site", peer="LocationSite")],
        ),
        "LocationSite": SimpleNamespace(relationships=[]),
    }
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    with pytest.raises(ValueError) as exc_info:
        destination.sync_from(Adapter(), diff=_two_rack_create_diff())

    assert "uncovered mapping identifier(s): site" in str(exc_info.value)
    assert entered_write_path is False


def test_inactive_unsafe_mapping_does_not_block_unrelated_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only kinds with create actions in the supplied diff are validated."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="mixed-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(name="LocationRack", identifiers=["name", "site"]),
            SchemaMappingModel(name="BuiltinTag", identifiers=["name"]),
        ],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(human_friendly_id=["name__value"], relationships=[]),
        "BuiltinTag": SimpleNamespace(human_friendly_id=["name__value"], relationships=[]),
    }
    diff = Diff()
    tag = DiffElement(obj_type="BuiltinTag", name="red", keys={"name": "red"})
    tag.add_attrs(source={"description": "Red"})
    diff.add(tag)
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    destination.sync_from(Adapter(), diff=diff)

    assert entered_write_path is True


def test_omitted_diff_does_not_activate_an_unrelated_unsafe_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validation follows create actions in the computed diff, not every configured kind."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="mixed-sync",
        source=SyncAdapter(name="source"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(name="LocationRack", identifiers=["name", "site"]),
            SchemaMappingModel(name="BuiltinTag", identifiers=["name"]),
        ],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(human_friendly_id=["name__value"], relationships=[]),
        "BuiltinTag": SimpleNamespace(human_friendly_id=["name__value"], relationships=[]),
    }
    computed = Diff()
    tag = DiffElement(obj_type="BuiltinTag", name="red", keys={"name": "red"})
    tag.add_attrs(source={"description": "Red"})
    computed.add(tag)
    observed_diff: Diff | None = None

    monkeypatch.setattr(InfrahubAdapter, "diff_from", lambda *_args, **_kwargs: computed)

    def _enter_write_path(*_args: object, **kwargs: object) -> Diff:
        nonlocal observed_diff
        supplied_diff = kwargs["diff"]
        assert isinstance(supplied_diff, Diff)
        observed_diff = supplied_diff
        return observed_diff

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    destination.sync_from(Adapter())

    assert observed_diff is computed


def test_update_only_diff_does_not_enter_upsert_identity_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Updates address an existing Infrahub node by ID and do not use the upsert key."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="LocationRack", identifiers=["name", "site"])],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(human_friendly_id=["name__value"], relationships=[]),
    }
    diff = Diff()
    rack = DiffElement(
        obj_type="LocationRack",
        name="rack-a__atlanta",
        keys={"name": "rack-a", "site": "atlanta"},
    )
    rack.add_attrs(source={"description": "New"}, dest={"description": "Old"})
    diff.add(rack)
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return diff

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    destination.sync_from(Adapter(), diff=diff)

    assert entered_write_path is True


def test_omitted_diff_with_only_updates_does_not_enter_upsert_identity_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A computed update still addresses the existing node by ID and remains allowed."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="source"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="LocationRack", identifiers=["name", "site"])],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(human_friendly_id=["name__value"], relationships=[]),
    }
    computed = Diff()
    rack = DiffElement(
        obj_type="LocationRack",
        name="rack-a__atlanta",
        keys={"name": "rack-a", "site": "atlanta"},
    )
    rack.add_attrs(source={"description": "New"}, dest={"description": "Old"})
    computed.add(rack)
    observed_diff: Diff | None = None

    monkeypatch.setattr(InfrahubAdapter, "diff_from", lambda *_args, **_kwargs: computed)

    def _enter_write_path(*_args: object, **kwargs: object) -> Diff:
        nonlocal observed_diff
        supplied_diff = kwargs["diff"]
        assert isinstance(supplied_diff, Diff)
        observed_diff = supplied_diff
        return observed_diff

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    destination.sync_from(Adapter())

    assert observed_diff is computed


def test_read_only_diff_remains_available_for_an_unsafe_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity validation belongs to the mutation boundary, not `diff_from`."""
    destination = InfrahubAdapter.__new__(InfrahubAdapter)
    destination.config = SyncConfig(
        name="rack-sync",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="LocationRack", identifiers=["name", "site"])],
    )
    destination.schema = {
        "LocationRack": SimpleNamespace(
            human_friendly_id=["name__value"],
            uniqueness_constraints=[["name__value"]],
        )
    }
    entered_diff_path = False

    def _enter_diff_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_diff_path
        entered_diff_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "diff_from", _enter_diff_path)

    destination.diff_from(Adapter())

    assert entered_diff_path is True
