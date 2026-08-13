"""Regression coverage for Infrahub convergence-identity validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from diffsync import Adapter, Diff

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


def test_covering_uniqueness_constraint_allows_destination_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A destination key covering `(name, site)` preserves the mapping identity."""
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

    destination.sync_from(Adapter())

    assert entered_write_path is True
