"""`configs convert --live-schema`: fill an unmapped IP namespace from the destination schema."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.configuration import conversion_live_schema
from infrahub_sync.configuration.capabilities import DestinationSchemaReadError
from infrahub_sync.configuration.conversion import ConversionResult
from infrahub_sync.configuration.conversion_live_schema import (
    fill_default_ip_namespaces,
    read_live_destination_schema,
    with_live_schema,
)
from infrahub_sync.runtime_schema.domain import normalize_destination_schema

if TYPE_CHECKING:
    from infrahub_sync.configuration.models import ConfigurationPackage

_TEXT = {"kind": "Text", "optional": False, "default_value": None, "unique": False}
_NAMESPACE = {"peer": "BuiltinIPNamespace", "cardinality": "one", "optional": True, "kind": "Generic"}
_SNAPSHOT: dict[str, Any] = {
    "IpamPrefix": {
        "human_friendly_id": [],
        "uniqueness_constraints": [["prefix__value", "ip_namespace"]],
        "attributes": {"prefix": {**_TEXT, "kind": "IPNetwork"}},
        "relationships": {
            "ip_namespace": _NAMESPACE,
            "vrf": {"peer": "IpamVRF", "cardinality": "one", "optional": True, "kind": "Attribute"},
        },
    },
    "IpamVRF": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": _TEXT},
        "relationships": {"namespace": _NAMESPACE},
    },
    "IpamNamespace": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": _TEXT},
        "relationships": {},
    },
    "BuiltinIPNamespace": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": _TEXT},
        "relationships": {},
        "used_by": ["IpamNamespace"],
    },
}


def _configuration() -> dict[str, Any]:
    return {
        "name": "from-netbox",
        "schema_mapping": [
            {
                "name": "IpamPrefix",
                "mapping": "ipam.prefixes",
                "identifiers": ["prefix", "vrf"],
                "fields": [{"name": "prefix", "mapping": "prefix"}, {"name": "vrf", "mapping": "vrf"}],
            },
            {
                "name": "IpamVRF",
                "mapping": "ipam.vrfs",
                "identifiers": ["name"],
                "fields": [{"name": "name", "mapping": "name"}],
            },
        ],
    }


def test_unmapped_namespace_of_a_uniqueness_constraint_becomes_default() -> None:
    configuration = _configuration()
    filled = fill_default_ip_namespaces(configuration, normalize_destination_schema(_SNAPSHOT))
    namespace, prefix = configuration["schema_mapping"][0], configuration["schema_mapping"][1]
    assert {"name": "ip_namespace", "static": "default", "reference": "IpamNamespace"} in prefix["fields"]
    assert prefix["identifiers"] == ["prefix", "ip_namespace"]
    assert namespace == {"name": "IpamNamespace", "identifiers": ["name"], "fields": [{"name": "name"}]}
    assert [(entry.kind, entry.field, entry.skipped) for entry in filled] == [("IpamPrefix", "ip_namespace", None)]


def test_constraint_with_another_unmapped_field_is_reported_incomplete() -> None:
    snapshot = copy.deepcopy(_SNAPSHOT)
    snapshot["IpamPrefix"]["uniqueness_constraints"] = [["prefix__value", "ip_namespace", "description__value"]]
    snapshot["IpamPrefix"]["attributes"]["description"] = _TEXT
    configuration = _configuration()
    filled = fill_default_ip_namespaces(configuration, normalize_destination_schema(snapshot))
    prefix = configuration["schema_mapping"][1]
    assert {"name": "ip_namespace", "static": "default", "reference": "IpamNamespace"} in prefix["fields"]
    assert prefix["identifiers"] == ["prefix", "vrf"]
    assert [(entry.skipped, entry.unmapped) for entry in filled] == [(None, ("description",))]
    note = conversion_live_schema._fill_note(filled[0]).text
    assert "needs description mapped" in note
    assert "IpamPrefix will not plan" in note
    assert "identifiers are now" not in note


def test_namespace_kind_that_is_not_one_concrete_kind_is_skipped() -> None:
    snapshot = copy.deepcopy(_SNAPSHOT)
    snapshot["BuiltinIPNamespace"]["used_by"] = ["IpamNamespace", "OtherNamespace"]
    configuration = _configuration()
    filled = fill_default_ip_namespaces(configuration, normalize_destination_schema(snapshot))
    assert filled[0].skipped is not None
    assert all(field["name"] != "ip_namespace" for field in configuration["schema_mapping"][0]["fields"])


def test_namespace_outside_a_uniqueness_constraint_is_left_unmapped() -> None:
    configuration = _configuration()
    fill_default_ip_namespaces(configuration, normalize_destination_schema(_SNAPSHOT))
    vrf = configuration["schema_mapping"][2]
    assert all(field["name"] != "namespace" for field in vrf["fields"])


def test_mapped_namespace_is_kept() -> None:
    configuration = _configuration()
    mapped = {"name": "ip_namespace", "mapping": "vrf.name", "reference": "IpamNamespace"}
    configuration["schema_mapping"][0]["fields"].append(copy.deepcopy(mapped))
    filled = fill_default_ip_namespaces(configuration, normalize_destination_schema(_SNAPSHOT))
    assert filled == ()
    assert configuration["schema_mapping"][0]["fields"][-1] == mapped


def test_relationship_to_another_peer_is_not_filled() -> None:
    snapshot = copy.deepcopy(_SNAPSHOT)
    snapshot["IpamPrefix"]["relationships"]["ip_namespace"] = {**_NAMESPACE, "peer": "LocationSite"}
    assert fill_default_ip_namespaces(_configuration(), normalize_destination_schema(snapshot)) == ()


def _result() -> ConversionResult:
    package = {
        "format_version": 1,
        "configuration": {
            **_configuration(),
            "source": {"name": "netbox", "settings": {"url": "https://netbox.example.com"}},
            "destination": {
                "name": "infrahub",
                "settings": {"url": "https://infrahub.example.com", "token": {"$credential": "infrahub-token"}},
            },
        },
        "credentials": {"infrahub-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_X"}},
    }
    return ConversionResult(package=package)


def test_live_schema_fills_and_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conversion_live_schema, "read_live_destination_schema", lambda _p: normalize_destination_schema(_SNAPSHOT)
    )
    result = with_live_schema(_result())
    assert result.package is not None
    fields = result.package["configuration"]["schema_mapping"][1]["fields"]
    assert {"name": "ip_namespace", "static": "default", "reference": "IpamNamespace"} in fields
    assert any("IpamPrefix.ip_namespace" in note.text and "'default'" in note.text for note in result.notes)


def test_failed_read_is_noted_and_package_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_package: object) -> None:
        msg = "refused"
        raise DestinationSchemaReadError(msg, reason="unauthorized")

    monkeypatch.setattr(conversion_live_schema, "read_live_destination_schema", refuse)
    original = _result()
    result = with_live_schema(original)
    assert result.package == original.package
    assert any("not read (unauthorized)" in note.text for note in result.notes)


def test_schema_read_sends_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def accessor(package: ConfigurationPackage, branch: str) -> dict[str, Any]:
        seen["settings"] = dict(package.configuration.destination.settings or {})
        seen["branch"] = branch
        return _SNAPSHOT

    class _Capabilities:
        destination_schema_accessor = staticmethod(accessor)

    monkeypatch.setattr(conversion_live_schema, "get_adapter_capabilities", lambda _name: _Capabilities())
    original = _result()
    read_live_destination_schema(original.package or {})
    assert "token" not in seen["settings"]
    assert seen["branch"] == "main"
    assert original.package is not None
    assert "token" in original.package["configuration"]["destination"]["settings"]


@pytest.mark.parametrize(("declared", "expected"), [("feature-x", "feature-x"), (5, "main"), ("", "main")])
def test_schema_read_resolves_the_branch_like_the_runtime(
    monkeypatch: pytest.MonkeyPatch, declared: object, expected: str
) -> None:
    seen: dict[str, str] = {}

    def accessor(_package: ConfigurationPackage, branch: str) -> dict[str, Any]:
        seen["branch"] = branch
        return _SNAPSHOT

    class _Capabilities:
        destination_schema_accessor = staticmethod(accessor)

    monkeypatch.setattr(conversion_live_schema, "get_adapter_capabilities", lambda _name: _Capabilities())
    package = _result().package or {}
    package["configuration"]["destination"]["settings"]["branch"] = declared
    read_live_destination_schema(package)
    assert seen["branch"] == expected


def test_filled_package_that_fails_a_registration_check_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        conversion_live_schema, "read_live_destination_schema", lambda _p: normalize_destination_schema(_SNAPSHOT)
    )
    monkeypatch.setattr(
        conversion_live_schema, "_check_filled", lambda _package: (conversion_live_schema.RefusalEntry("/", "x"),)
    )
    result = with_live_schema(_result())
    assert result.package is None
    assert result.refusals
