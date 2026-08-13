"""Regression coverage for Infrahub convergence-identity validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from diffsync import Adapter, Diff
from diffsync.diff import DiffElement

from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import InfrahubAdapter


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
    entered_write_path = False

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    with pytest.raises(ValueError) as exc_info:
        destination.sync_from(Adapter())

    message = str(exc_info.value)
    assert "LocationRack" in message
    assert "uncovered mapping identifier(s): site" in message
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
        destination.sync_from(Adapter())

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

    def _enter_write_path(*_args: object, **_kwargs: object) -> Diff:
        nonlocal entered_write_path
        entered_write_path = True
        return Diff()

    monkeypatch.setattr(Adapter, "sync_from", _enter_write_path)

    destination.sync_from(Adapter())

    assert entered_write_path is True


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
        destination.sync_from(Adapter())

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

    destination.sync_from(Adapter())

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
        destination.sync_from(Adapter())

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
        destination.sync_from(Adapter())

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
        destination.sync_from(Adapter())

    assert "uncovered mapping identifier(s): site" in str(exc_info.value)
    assert entered_write_path is False


def test_inactive_unsafe_mapping_does_not_block_unrelated_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only kinds with create or update actions in the supplied diff are validated."""
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
