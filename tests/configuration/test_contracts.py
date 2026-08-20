"""Behavioral tests for declared configuration identity and adapter capabilities."""

from __future__ import annotations

import json
import math
import os
import subprocess  # noqa: S404 - fixed interpreter runs an in-repository determinism probe.
import sys
import textwrap
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from pydantic import ValidationError

from infrahub_sync import SyncConfig
from infrahub_sync.configuration import (
    BUILTIN_ADAPTER_CAPABILITIES,
    AdapterConfigurationCapabilities,
    ConfigurationPackage,
    ConfigurationPackageParseError,
    CredentialConfigurationError,
    EnvironmentCredentialProvider,
    UnknownAdapterCapabilitiesError,
    ValidationFinding,
    get_adapter_capabilities,
    parse_configuration_package,
    resolve_reference,
    sort_findings,
    validate_package_credentials,
)
from infrahub_sync.configuration.models import safe_pointer_component


def _package(**updates: object) -> ConfigurationPackage:
    data: dict[str, object] = {
        "format_version": 1,
        "configuration": {
            "name": "from-netbox",
            "source": {
                "name": "netbox",
                "settings": {
                    "url": "https://demo.netbox.dev",
                    "token": {"$credential": "netbox-token"},
                },
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": "http://localhost:8000",
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "order": [],
            "schema_mapping": [],
            "diffsync_flags": [],
            "incremental": None,
        },
        "package_metadata": {"adapter_api_version": 1},
        "credentials": {
            "netbox-token": {"provider": "env", "identifier": "NETBOX_TOKEN"},
            "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"},
        },
    }
    data.update(updates)
    return ConfigurationPackage.model_validate(data)


def _package_with_nested_declared_content() -> ConfigurationPackage:
    data = _package().model_dump(mode="json")
    data["configuration"]["store"] = {
        "type": "redis",
        "settings": {
            "host": "localhost",
            "password": {"$credential": "redis-password"},
        },
    }
    data["configuration"]["order"] = ["Device"]
    data["configuration"]["diffsync_flags"] = ["SKIP_UNMATCHED_DST"]
    data["configuration"]["schema_mapping"] = [
        {
            "name": "Device",
            "identifiers": ["name"],
            "filters": [{"field": "enabled", "operation": "==", "value": {"expected": [True]}}],
            "transforms": [{"field": "name", "expression": "value"}],
            "fields": [{"name": "metadata", "static": {"labels": ["edge"]}}],
        }
    ]
    data["credentials"]["redis-password"] = {"provider": "env", "identifier": "REDIS_PASSWORD"}
    return ConfigurationPackage.model_validate(data)


def _set_source_setting(package: ConfigurationPackage) -> None:
    inline_value = "inline-secret"
    cast("Any", package.configuration.source.settings)["token"] = inline_value


def _set_destination_setting(package: ConfigurationPackage) -> None:
    cast("Any", package.configuration.destination.settings)["url"] = "https://changed.example"


def _set_store_setting(package: ConfigurationPackage) -> None:
    store = package.configuration.store
    assert store is not None
    cast("Any", store.settings)["host"] = "changed.example"


def _append_order(package: ConfigurationPackage) -> None:
    cast("Any", package.configuration.order).append("Other")


def _append_diffsync_flag(package: ConfigurationPackage) -> None:
    cast("Any", package.configuration.diffsync_flags).append("SKIP_UNMATCHED_DST")


def _append_schema_mapping(package: ConfigurationPackage) -> None:
    cast("Any", package.configuration.schema_mapping).append(package.configuration.schema_mapping[0])


def _rename_schema_mapping(package: ConfigurationPackage) -> None:
    cast("Any", package.configuration.schema_mapping[0]).name = "Changed"


def _append_schema_identifier(package: ConfigurationPackage) -> None:
    cast("Any", package.configuration.schema_mapping[0].identifiers).append("serial")


def _mutate_schema_filter_value(package: ConfigurationPackage) -> None:
    filters = package.configuration.schema_mapping[0].filters
    assert filters is not None
    value = cast("Any", filters[0].value)
    value["expected"].append(False)


def _mutate_schema_static_value(package: ConfigurationPackage) -> None:
    value = cast("Any", package.configuration.schema_mapping[0].fields[0].static)
    value["labels"].append("core")


def _assert_json_containers(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert type(key) is str
            _assert_json_containers(item)
    elif isinstance(value, list):
        for item in value:
            _assert_json_containers(item)
    else:
        assert value is None or type(value) in {str, int, float, bool}
        if type(value) is float:
            assert math.isfinite(value)


def test_checksum_is_stable_across_mapping_order() -> None:
    first = _package()
    second = ConfigurationPackage.model_validate(
        {
            "credentials": dict(reversed(list(first.model_dump(mode="json")["credentials"].items()))),
            "configuration": first.model_dump(mode="json")["configuration"],
            "package_metadata": {"adapter_api_version": 1},
            "format_version": 1,
        }
    )

    assert first.checksum() == second.checksum()


def test_checksum_changes_with_declared_credential_identifier() -> None:
    baseline = _package()
    changed = baseline.model_dump(mode="json")
    changed["credentials"]["netbox-token"]["identifier"] = "OTHER_NETBOX_TOKEN"

    assert ConfigurationPackage.model_validate(changed).checksum() != baseline.checksum()


def test_package_credentials_cannot_mutate_after_validation() -> None:
    package = _package()
    checksum = package.checksum()

    with pytest.raises(TypeError):
        cast("Any", package.credentials)["later"] = package.credentials["netbox-token"]

    assert package.checksum() == checksum


def test_default_empty_credentials_cannot_mutate_after_validation() -> None:
    data = _package().model_dump(mode="json")
    del data["credentials"]
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(TypeError):
        cast("Any", package.credentials)["later"] = {"provider": "env", "identifier": "LATER"}


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_set_source_setting, id="source-settings"),
        pytest.param(_set_destination_setting, id="destination-settings"),
        pytest.param(_set_store_setting, id="store-settings"),
        pytest.param(_append_order, id="order"),
        pytest.param(_append_diffsync_flag, id="diffsync-flags"),
        pytest.param(_append_schema_mapping, id="schema-mapping-list"),
        pytest.param(_rename_schema_mapping, id="schema-mapping-model"),
        pytest.param(_append_schema_identifier, id="schema-mapping-identifiers"),
        pytest.param(_mutate_schema_filter_value, id="schema-mapping-filter-value"),
        pytest.param(_mutate_schema_static_value, id="schema-mapping-static-value"),
    ],
)
def test_declared_configuration_cannot_mutate_after_validation(
    mutate: Callable[[ConfigurationPackage], None],
) -> None:
    package = _package_with_nested_declared_content()
    validate_package_credentials(package)
    declared_content = package.declared_content()
    checksum = package.checksum()

    with pytest.raises((AttributeError, TypeError, ValidationError)):
        mutate(package)

    assert package.declared_content() == declared_content
    assert package.checksum() == checksum
    validate_package_credentials(package)


def test_default_package_dump_is_json_native_and_round_trips() -> None:
    package = _package_with_nested_declared_content()

    dumped = package.model_dump(by_alias=True)

    _assert_json_containers(dumped)
    json.dumps(dumped)
    reparsed = ConfigurationPackage.model_validate(dumped)
    assert reparsed.declared_content() == package.declared_content()
    assert reparsed.checksum() == package.checksum()


def test_package_rejects_machine_local_directory() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["directory"] = "/example/generated"

    with pytest.raises(ValidationError, match="unsupported declared fields: directory"):
        ConfigurationPackage.model_validate(data)


def test_package_rejects_machine_local_adapter_path() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["adapters_path"] = ["/home/alice/custom-adapters"]

    with pytest.raises(ValidationError, match="unsupported declared fields: adapters_path"):
        ConfigurationPackage.model_validate(data)


@pytest.mark.parametrize("role", ["source", "destination"])
def test_package_rejects_custom_adapter_override(role: str) -> None:
    data = _package().model_dump(mode="json")
    data["configuration"][role]["adapter"] = "evil.module:CustomSync"

    with pytest.raises(ValidationError, match="unsupported declared fields: adapter"):
        ConfigurationPackage.model_validate(data)


def test_package_rejects_nested_fields_legacy_models_would_ignore() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["ignored"] = "would-not-be-hashed"

    with pytest.raises(ValidationError, match=r"configuration\.source contains unsupported declared fields: ignored"):
        ConfigurationPackage.model_validate(data)


@pytest.mark.parametrize("value", [datetime(2026, 8, 12, tzinfo=timezone.utc), ("not", "json"), {1: "non-string-key"}])
def test_package_rejects_non_json_values(value: object) -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["settings"]["unexpected"] = value

    with pytest.raises(ValidationError, match=r"non-JSON|non-string"):
        ConfigurationPackage.model_validate(data)


def test_credential_reference_whitespace_is_rejected_not_normalized() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["settings"]["token"] = {"$credential": " netbox-token "}
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match="malformed credential reference"):
        validate_package_credentials(package)


def test_package_rejects_non_finite_numbers() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["settings"]["timeout"] = float("nan")

    with pytest.raises(ValidationError, match="non-finite"):
        ConfigurationPackage.model_validate(data)


def test_package_rejects_recursive_declarations() -> None:
    data = _package().model_dump(mode="json")
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    data["configuration"]["source"]["settings"]["recursive"] = recursive

    with pytest.raises(ValidationError, match="recursive mapping"):
        ConfigurationPackage.model_validate(data)


@pytest.mark.parametrize(
    "unsafe_credentials",
    [
        {"nt": "hunter2-inline-secret"},
        {"nt": {"provider": "env", "identifier": "X", "value": "hunter2-extra-secret"}},
    ],
)
def test_safe_parse_boundary_does_not_echo_rejected_credential_values(
    unsafe_credentials: dict[str, object],
) -> None:
    canaries = ("hunter2-inline-secret", "hunter2-extra-secret")
    data = _package().model_dump(mode="json")
    data["credentials"] = unsafe_credentials

    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(data)

    message = str(caught.value)
    assert "/credentials/nt" in message
    assert all(canary not in message for canary in canaries)


@pytest.mark.parametrize(
    ("surrogate", "reference_node", "visible_surrogate"),
    [
        pytest.param("\ud800", {"$credential": "missing-reference-canary"}, r"\ud800", id="missing-reference"),
        pytest.param(
            "\ud801",
            {"$credential": "netbox-token", "fallback": "malformed-reference-value-canary"},
            r"\ud801",
            id="malformed-reference",
        ),
    ],
)
def test_safe_parse_rejects_distinct_surrogate_keys_before_serialization(
    surrogate: str,
    reference_node: dict[str, object],
    visible_surrogate: str,
) -> None:
    canaries = ("missing-reference-canary", "malformed-reference-value-canary")
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["settings"][f"nested{surrogate}reference~/"] = reference_node

    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(data)

    message = str(caught.value)
    assert message == (
        "configuration package is invalid at "
        f"/configuration/source/settings/nested{visible_surrogate}reference~0~1: invalid Unicode surrogate"
    )
    assert len(message.splitlines()) == 1
    assert all(character.isprintable() for character in message)
    assert surrogate not in message
    assert all(canary not in message for canary in canaries)


@pytest.mark.parametrize("codepoint", [0xDC00, 0xDFFF], ids=["low-start", "low-end"])
def test_safe_parse_rejects_surrogate_string_values_without_echo(codepoint: int) -> None:
    canary = "surrogate-value-canary"
    surrogate = chr(codepoint)
    data = _package().model_dump(mode="json")
    data["configuration"]["name"] = f"{canary}{surrogate}"

    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(data)

    message = str(caught.value)
    assert message == "configuration package is invalid at /configuration/name: invalid Unicode surrogate"
    assert len(message.splitlines()) == 1
    assert all(character.isprintable() for character in message)
    assert surrogate not in message
    assert canary not in message


def test_safe_parse_names_unknown_adapter_field_without_echoing_its_value() -> None:
    canaries = (
        "adapter-field-value-canary",
        "url-userinfo-canary",
        "url-query-canary",
        "url-fragment-canary",
    )
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["setings"] = {
        "token": canaries[0],
        "url": f"https://user:{canaries[1]}@example.test/path?token={canaries[2]}#{canaries[3]}",
    }

    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(data)

    message = str(caught.value)
    assert "/configuration/source/setings: unsupported declared field" in message
    assert all(canary not in message for canary in canaries)
    assert "https://" not in message


def test_safe_parse_names_unknown_nested_mapping_field_without_echoing_its_value() -> None:
    canary = "nested-field-value-canary"
    data = _package().model_dump(mode="json")
    data["configuration"]["schema_mapping"] = [
        {
            "name": "Device",
            "fields": [{"name": "serial", "mapping": "serial", "maping": canary}],
        }
    ]

    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(data)

    message = str(caught.value)
    assert "/configuration/schema_mapping/0/fields/0/maping: unsupported declared field" in message
    assert canary not in message


@pytest.mark.parametrize(
    ("control", "visible"),
    [
        ("\n", r"\n"),
        ("\r", r"\r"),
        ("\t", r"\t"),
        ("\x1b", r"\u001b"),
        ("\x00", r"\u0000"),
        ("\x7f", r"\u007f"),
        ("\x85", r"\u0085"),
        ("\x9f", r"\u009f"),
        pytest.param("\u2028", r"\u2028", id="line-separator"),
        pytest.param("\u2029", r"\u2029", id="paragraph-separator"),
        pytest.param("\u202e", r"\u202e", id="right-to-left-override"),
        pytest.param("\u200b", r"\u200b", id="zero-width-space"),
        pytest.param("\u2066", r"\u2066", id="left-to-right-isolate"),
        pytest.param("\ufeff", r"\ufeff", id="zero-width-no-break-space"),
        pytest.param("\U000e0001", r"\U000e0001", id="language-tag"),
    ],
)
def test_safe_parse_renders_unknown_field_controls_without_echoing_values(control: str, visible: str) -> None:
    canary = "control-field-value-canary"
    data = _package().model_dump(mode="json")
    data["configuration"]["source"][f"bad{control}field~/"] = canary

    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(data)

    message = str(caught.value)
    assert f"/configuration/source/bad{visible}field~0~1: unsupported declared field" in message
    assert len(message.splitlines()) == 1
    assert all(character.isprintable() for character in message)
    assert control not in message
    assert canary not in message


@pytest.mark.parametrize(
    ("control", "visible"),
    [
        ("\n", r"\n"),
        ("\r", r"\r"),
        ("\t", r"\t"),
        ("\x1b", r"\u001b"),
        pytest.param("\u2028", r"\u2028", id="line-separator"),
        pytest.param("\u2029", r"\u2029", id="paragraph-separator"),
        pytest.param("\u202e", r"\u202e", id="right-to-left-override"),
        pytest.param("\u200b", r"\u200b", id="zero-width-space"),
        pytest.param("\u2066", r"\u2066", id="left-to-right-isolate"),
        pytest.param("\ufeff", r"\ufeff", id="zero-width-no-break-space"),
        pytest.param("\U000e0001", r"\U000e0001", id="language-tag"),
    ],
)
def test_safe_parse_distinguishes_controls_from_literal_escape_text(control: str, visible: str) -> None:
    canaries = ("control-value-canary", "literal-value-canary")
    messages = []
    for field_name, canary in ((f"bad{control}field~/", canaries[0]), (f"bad{visible}field~/", canaries[1])):
        data = _package().model_dump(mode="json")
        data["configuration"]["source"][field_name] = canary
        with pytest.raises(ConfigurationPackageParseError) as caught:
            parse_configuration_package(data)
        messages.append(str(caught.value))

    literal_visible = visible.replace("\\", r"\\")
    assert f"/configuration/source/bad{visible}field~0~1: unsupported declared field" in messages[0]
    assert f"/configuration/source/bad{literal_visible}field~0~1: unsupported declared field" in messages[1]
    assert messages[0] != messages[1]
    assert all(len(message.splitlines()) == 1 for message in messages)
    assert all(character.isprintable() for message in messages for character in message)
    assert control not in messages[0]
    assert all(canary not in message for canary in canaries for message in messages)


def test_safe_parse_preserves_printable_unicode_in_unknown_field_locations() -> None:
    canary = "printable-unicode-value-canary"
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["café-東京-😀~/"] = canary

    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(data)

    message = str(caught.value)
    assert "/configuration/source/café-東京-😀~0~1: unsupported declared field" in message
    assert canary not in message


def test_inline_credential_is_refused_without_echoing_value() -> None:
    canary = "canary-inline-secret"
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["settings"]["token"] = canary
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    assert "inline credential" in str(caught.value)
    assert canary not in str(caught.value)


def test_prometheus_custom_headers_are_refused_without_echoing_values() -> None:
    canary = "prometheus-inline-canary"
    data = _package().model_dump(mode="json")
    data["configuration"]["source"] = {
        "name": "prometheus",
        "settings": {
            "url": "https://prometheus.example",
            "headers": {"Authorization": f"Bearer {canary}"},
        },
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    assert "unsupported declared settings" in str(caught.value)
    assert canary not in str(caught.value)


def test_generic_rest_api_custom_headers_are_refused_without_echoing_values() -> None:
    canary = "generic-rest-api-inline-canary"
    data = _package().model_dump(mode="json")
    data["configuration"]["source"] = {
        "name": "genericrestapi",
        "settings": {
            "url": "https://rest-api.example",
            "headers": {"Authorization": f"Bearer {canary}"},
        },
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    assert "unsupported declared settings" in str(caught.value)
    assert canary not in str(caught.value)


@pytest.mark.parametrize(
    ("adapter_name", "setting", "value", "canary"),
    [
        pytest.param(
            "netbox",
            "client_secret",
            "netbox-inline-canary",
            "netbox-inline-canary",
            id="undeclared-client-secret",
        ),
        pytest.param(
            "prometheus",
            "params",
            {"api_key": "prometheus-params-inline-canary"},
            "prometheus-params-inline-canary",
            id="prometheus-params",
        ),
        pytest.param(
            "genericrestapi",
            "params",
            {"api_key": "generic-rest-params-inline-canary"},
            "generic-rest-params-inline-canary",
            id="generic-rest-params",
        ),
        pytest.param(
            "peeringmanager",
            "headers",
            {"Authorization": "Bearer peering-manager-header-inline-canary"},
            "peering-manager-header-inline-canary",
            id="peering-manager-inherited-headers",
        ),
        pytest.param(
            "peeringmanager",
            "params",
            {"api_key": "peering-manager-params-inline-canary"},
            "peering-manager-params-inline-canary",
            id="peering-manager-inherited-params",
        ),
        pytest.param(
            "slurpitsync",
            "client_secret",
            "slurpit-splat-inline-canary",
            "slurpit-splat-inline-canary",
            id="slurpit-splat",
        ),
        *(
            pytest.param(
                adapter_name,
                setting,
                [canary],
                canary,
                id=f"{adapter_name}-{setting}",
            )
            for adapter_name in ("genericrestapi", "peeringmanager")
            for setting, canary in (
                ("url_env_vars", "url-selector-inline-canary"),
                ("token_env_vars", "token-selector-inline-canary"),
                ("username_env_vars", "username-selector-inline-canary"),
                ("password_env_vars", "password-selector-inline-canary"),
            )
        ),
    ],
)
def test_unproved_adapter_settings_are_refused_without_echoing_values(
    adapter_name: str,
    setting: str,
    value: object,
    canary: str,
) -> None:
    data = _package().model_dump(mode="json")
    settings = {"url": "https://source.example", setting: value}
    if setting == "base_url":
        settings.pop("url")
    data["configuration"]["source"] = {
        "name": adapter_name,
        "settings": settings,
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    assert "unsupported declared settings" in str(caught.value)
    assert canary not in str(caught.value)


def test_unsupported_adapter_settings_name_fields_in_stable_order_without_echoing_values() -> None:
    canaries = ("alpha-setting-value-canary", "zulu-setting-value-canary")
    data = _package().model_dump(mode="json")
    data["configuration"]["source"] = {
        "name": "netbox",
        "settings": {
            "url": "https://source.example",
            "zulu_setting": canaries[1],
            "alpha_setting": canaries[0],
        },
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    message = str(caught.value)
    assert message == (
        "adapter 'netbox' contains unsupported declared settings for the source role: "
        '["alpha_setting", "zulu_setting"]'
    )
    assert all(canary not in message for canary in canaries)


def test_unsupported_adapter_settings_render_hostile_names_safely_without_echoing_values() -> None:
    canaries = ("literal-slash-value-canary", "newline-value-canary", "c1-value-canary")
    data = _package().model_dump(mode="json")
    data["configuration"]["source"] = {
        "name": "netbox",
        "settings": {
            "url": "https://source.example",
            r"bad\nfield~/": canaries[0],
            "bad\nfield~/": canaries[1],
            "bad\x85field~/": canaries[2],
        },
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    message = str(caught.value)
    expected_names = json.dumps(
        sorted(safe_pointer_component(name) for name in (r"bad\nfield~/", "bad\nfield~/", "bad\x85field~/"))
    )
    assert message == f"adapter 'netbox' contains unsupported declared settings for the source role: {expected_names}"
    assert all(ord(character) >= 32 and not 127 <= ord(character) <= 159 for character in message)
    assert all(canary not in message for canary in canaries)


def _unsupported_adapter_settings_message(settings: Mapping[str, object]) -> str:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"] = {
        "name": "netbox",
        "settings": {"url": "https://source.example", **settings},
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    return str(caught.value)


def _unsupported_store_settings_message(settings: Mapping[str, object]) -> str:
    data = _package().model_dump(mode="json")
    data["configuration"]["store"] = {
        "type": "redis",
        "settings": {"host": "localhost", **settings},
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    return str(caught.value)


@pytest.mark.parametrize(
    "message_for_settings",
    [_unsupported_adapter_settings_message, _unsupported_store_settings_message],
    ids=["adapter", "redis-store"],
)
def test_unsupported_setting_name_lists_preserve_unambiguous_boundaries(
    message_for_settings: Callable[[Mapping[str, object]], str],
) -> None:
    hostile_prefix = 'a"\\\n\t\x85\x9f'
    first_names = (hostile_prefix, "b, c")
    second_names = (f"{hostile_prefix}, b", "c")
    canaries = (
        "first-setting-value-canary",
        "second-setting-value-canary",
        "third-setting-value-canary",
        "fourth-setting-value-canary",
    )
    first_settings = dict(zip(first_names, canaries[:2], strict=True))
    second_settings = dict(zip(second_names, canaries[2:], strict=True))

    first_message = message_for_settings(first_settings)
    second_message = message_for_settings(second_settings)

    assert first_message == message_for_settings(dict(reversed(first_settings.items())))
    assert second_message == message_for_settings(dict(reversed(second_settings.items())))
    assert first_message != second_message
    assert json.loads(first_message.rsplit(": ", maxsplit=1)[1]) == sorted(
        safe_pointer_component(name) for name in first_names
    )
    assert json.loads(second_message.rsplit(": ", maxsplit=1)[1]) == sorted(
        safe_pointer_component(name) for name in second_names
    )
    assert all(ord(character) >= 32 and not 127 <= ord(character) <= 159 for character in first_message)
    assert all(ord(character) >= 32 and not 127 <= ord(character) <= 159 for character in second_message)
    assert all(canary not in first_message and canary not in second_message for canary in canaries)


@pytest.mark.parametrize("adapter_name", ["genericrestapi", "peeringmanager"])
@pytest.mark.parametrize(
    ("mapping", "canary"),
    [
        pytest.param("https://absolute-inline-canary.example/devices", "absolute-inline-canary", id="absolute"),
        pytest.param(
            "//user:userinfo-inline-canary@other.example/devices",
            "userinfo-inline-canary",
            id="userinfo",
        ),
        pytest.param("devices?api_key=query-inline-canary", "query-inline-canary", id="query"),
        pytest.param("devices#fragment-inline-canary", "fragment-inline-canary", id="fragment"),
    ],
)
def test_generic_rest_schema_mapping_endpoints_are_secret_safe(
    adapter_name: str,
    mapping: str,
    canary: str,
) -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"] = {
        "name": adapter_name,
        "settings": {"url": "https://source.example", "auth_method": "none"},
    }
    data["configuration"]["schema_mapping"] = [{"name": "Device", "mapping": mapping}]
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    assert "/configuration/schema_mapping/0/mapping" in str(caught.value)
    assert "relative request path" in str(caught.value)
    assert canary not in str(caught.value)


@pytest.mark.parametrize("adapter_name", ["genericrestapi", "peeringmanager"])
def test_generic_rest_schema_mapping_accepts_relative_resource_paths(adapter_name: str) -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"] = {
        "name": adapter_name,
        "settings": {"url": "https://source.example", "auth_method": "none"},
    }
    data["configuration"]["schema_mapping"] = [
        {"name": "Device", "mapping": "api/v1/devices"},
        {"name": "Interface", "mapping": "/api/v1/interfaces"},
    ]

    validate_package_credentials(ConfigurationPackage.model_validate(data))


def test_closed_registered_settings_do_not_change_legacy_sync_config() -> None:
    inline_value = "legacy-inline-value"
    configuration = SyncConfig.model_validate(
        {
            "name": "legacy-rest",
            "source": {
                "name": "genericrestapi",
                "settings": {
                    "params": {"api_key": inline_value},
                    "client_secret": inline_value,
                    "token_env_vars": ["CUSTOM_TOKEN"],
                },
            },
            "destination": {"name": "infrahub", "settings": {}},
            "schema_mapping": [{"name": "Device", "mapping": f"devices?api_key={inline_value}"}],
        }
    )

    assert configuration.source.settings == {
        "params": {"api_key": inline_value},
        "client_secret": inline_value,
        "token_env_vars": ["CUSTOM_TOKEN"],
    }
    assert configuration.schema_mapping[0].mapping == f"devices?api_key={inline_value}"


@pytest.mark.parametrize(
    ("adapter_name", "setting", "value", "canary"),
    [
        pytest.param(
            "netbox",
            "url",
            "https://user:netbox-url-inline-canary@netbox.example",
            "netbox-url-inline-canary",
            id="url-userinfo",
        ),
        pytest.param(
            "genericrestapi",
            "url",
            "https://api.example?api_key=generic-rest-query-inline-canary",
            "generic-rest-query-inline-canary",
            id="url-query",
        ),
        pytest.param(
            "ipfabricsync",
            "base_url",
            "https://ipfabric.example#ipfabric-fragment-inline-canary",
            "ipfabric-fragment-inline-canary",
            id="base-url-fragment",
        ),
        pytest.param(
            "prometheus",
            "endpoint",
            "/metrics?token=prometheus-endpoint-inline-canary",
            "prometheus-endpoint-inline-canary",
            id="endpoint-query",
        ),
        pytest.param(
            "peeringmanager",
            "api_endpoint",
            "/api#peering-manager-endpoint-inline-canary",
            "peering-manager-endpoint-inline-canary",
            id="inherited-endpoint-fragment",
        ),
    ],
)
def test_url_settings_refuse_credential_bearing_forms_without_echo(
    adapter_name: str,
    setting: str,
    value: str,
    canary: str,
) -> None:
    data = _package().model_dump(mode="json")
    settings = {"url": "https://source.example", setting: value}
    if setting == "base_url":
        settings.pop("url")
    data["configuration"]["source"] = {
        "name": adapter_name,
        "settings": settings,
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    assert "cannot contain user information, query parameters, or fragments" in str(caught.value)
    assert canary not in str(caught.value)


def test_multiple_invalid_url_settings_choose_the_same_first_error_across_hash_seeds() -> None:
    canaries = ("api-endpoint-query-canary", "url-query-canary")
    script = textwrap.dedent(
        f"""
        from infrahub_sync.configuration import (
            ConfigurationPackage,
            CredentialConfigurationError,
            validate_package_credentials,
        )

        package = ConfigurationPackage.model_validate(
            {{
                "format_version": 1,
                "configuration": {{
                    "name": "from-aci",
                    "source": {{
                        "name": "aci",
                        "settings": {{
                            "url": "https://aci.example?token={canaries[1]}",
                            "api_endpoint": "/api?token={canaries[0]}",
                        }},
                    }},
                    "destination": {{"name": "infrahub", "settings": {{}}}},
                }},
            }}
        )
        try:
            validate_package_credentials(package)
        except CredentialConfigurationError as exc:
            print(exc)
        """
    )
    messages = []
    for seed in ("0", "2"):
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and in-repository test script.
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        messages.append(completed.stdout.strip())

    expected = (
        "/configuration/source/settings/api_endpoint cannot contain user information, query parameters, or fragments"
    )
    assert messages == [expected, expected]
    assert all(canary not in message for canary in canaries for message in messages)


def test_non_string_url_setting_gets_type_diagnostic_without_echoing_value() -> None:
    canary = "non-string-url-value-canary"
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["settings"]["url"] = {"nested": canary}
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    message = str(caught.value)
    assert message == "/configuration/source/settings/url must be declared as a string"
    assert canary not in message


@pytest.mark.parametrize(
    "node",
    [
        {"$credential": "netbox-token", "fallback": "unsafe"},
        {"$credential": ""},
        {"$credential": 3},
    ],
)
def test_malformed_reference_node_is_refused_without_echo(node: object) -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["settings"]["token"] = node
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match="malformed credential reference"):
        validate_package_credentials(package)


def test_unknown_named_reference_is_refused() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["settings"]["token"] = {"$credential": "missing-token"}
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match="unknown credential reference 'missing-token'"):
        validate_package_credentials(package)


def test_reserved_reference_node_is_validated_outside_known_credential_paths() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["source"]["settings"]["headers"] = {"authorization": {"$credential": "missing-header"}}
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match="unknown credential reference 'missing-header'"):
        validate_package_credentials(package)


@pytest.mark.parametrize(
    ("reference_node", "expected_detail"),
    [
        pytest.param(
            {"$credential": "missing-reference"},
            "names unknown credential reference 'missing-reference'",
            id="missing-reference",
        ),
        pytest.param(
            {"$credential": "netbox-token", "fallback": "malformed-reference-value-canary"},
            "contains a malformed credential reference",
            id="malformed-reference",
        ),
    ],
)
def test_nested_reference_errors_render_hostile_setting_paths_safely(
    reference_node: dict[str, object],
    expected_detail: str,
) -> None:
    canaries = ("unsupported-setting-value-canary", "malformed-reference-value-canary")
    hostile_components = (
        "bad\n\x85\u2028\u202e\u200b\U000e0001\\setting~/",
        "nested\r\x9f\u2029\u2066\ufeff\\node~/",
    )
    literal_components = (
        r"bad\n\u0085\u2028\u202e\u200b\U000e0001\setting~/",
        r"nested\r\u009f\u2029\u2066\ufeff\node~/",
    )
    messages = []
    for setting_name, nested_key in (hostile_components, literal_components):
        data = _package().model_dump(mode="json")
        data["configuration"]["source"]["settings"][setting_name] = {
            "other": canaries[0],
            nested_key: reference_node,
        }
        package = ConfigurationPackage.model_validate(data)

        with pytest.raises(CredentialConfigurationError) as caught:
            validate_package_credentials(package)
        messages.append(str(caught.value))

    hostile_pointer = (
        r"/configuration/source/settings/bad\n\u0085\u2028\u202e\u200b\U000e0001\\setting~0~1/"
        r"nested\r\u009f\u2029\u2066\ufeff\\node~0~1"
    )
    literal_pointer = (
        r"/configuration/source/settings/bad\\n\\u0085\\u2028\\u202e\\u200b\\U000e0001\\setting~0~1/"
        r"nested\\r\\u009f\\u2029\\u2066\\ufeff\\node~0~1"
    )
    assert messages == [f"{hostile_pointer} {expected_detail}", f"{literal_pointer} {expected_detail}"]
    assert messages[0] != messages[1]
    assert all(len(message.splitlines()) == 1 for message in messages)
    assert all(character.isprintable() for message in messages for character in message)
    assert all(character not in messages[0] for character in "\u2028\u2029\u202e\u200b\u2066\ufeff\U000e0001")
    assert all(canary not in message for canary in canaries for message in messages)


def test_reserved_reference_node_is_validated_in_schema_mapping_static_value() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["schema_mapping"] = [
        {
            "name": "Device",
            "fields": [{"name": "token", "static": {"$credential": "missing-static"}}],
        }
    ]
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match="unknown credential reference 'missing-static'"):
        validate_package_credentials(package)


def test_store_inline_credential_is_refused_without_echoing_value() -> None:
    canary = "store-inline-secret"
    data = _package().model_dump(mode="json")
    data["configuration"]["store"] = {
        "type": "redis",
        "settings": {"host": "localhost", "password": canary},
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    assert "inline credential" in str(caught.value)
    assert canary not in str(caught.value)


@pytest.mark.parametrize("setting", ["sentinel_password", "tls_key_password"])
def test_redis_store_rejects_undeclared_credential_settings(setting: str) -> None:
    canary = "undeclared-store-secret"
    data = _package().model_dump(mode="json")
    data["configuration"]["store"] = {
        "type": "redis",
        "settings": {"host": "localhost", setting: canary},
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    assert "unsupported declared settings" in str(caught.value)
    assert canary not in str(caught.value)


def test_redis_store_renders_unsupported_control_name_safely_without_echoing_value() -> None:
    canary = "store-control-value-canary"
    data = _package().model_dump(mode="json")
    data["configuration"]["store"] = {
        "type": "redis",
        "settings": {"host": "localhost", "bad\nfield~/": canary},
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package)

    message = str(caught.value)
    assert message == r"""store type 'redis' contains unsupported declared settings: ["bad\\nfield~0~1"]"""
    assert all(ord(character) >= 32 and not 127 <= ord(character) <= 159 for character in message)
    assert canary not in message


def test_reserved_reference_node_is_validated_in_store_settings() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["store"] = {
        "type": "redis",
        "settings": {"host": "localhost", "token": {"$credential": "missing-store"}},
    }
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match="unknown credential reference 'missing-store'"):
        validate_package_credentials(package)


def test_declared_references_validate_without_resolving_environment() -> None:
    package = _package()

    validate_package_credentials(package)


def test_package_validation_checks_provider_without_reading_environment() -> None:
    data = _package().model_dump(mode="json")
    data["credentials"]["netbox-token"]["provider"] = "vault"
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match=r"provider 'vault'.*not installed"):
        validate_package_credentials(package)


def test_package_validation_checks_environment_identifier_without_reading_value() -> None:
    data = _package().model_dump(mode="json")
    data["credentials"]["netbox-token"]["identifier"] = "INVALID-NAME"
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match="invalid environment identifier"):
        validate_package_credentials(package)


def test_environment_provider_returns_exact_runtime_value() -> None:
    provider = EnvironmentCredentialProvider({"TOKEN_NAME": "runtime-secret"})

    assert provider.resolve("TOKEN_NAME") == "runtime-secret"


@pytest.mark.parametrize("identifier", ["", "9INVALID", "HAS-DASH"])
def test_environment_provider_refuses_invalid_identifier(identifier: str) -> None:
    with pytest.raises(CredentialConfigurationError, match="identifier"):
        EnvironmentCredentialProvider({identifier: "secret"}).resolve(identifier)


@pytest.mark.parametrize("environment", [{}, {"NETBOX_TOKEN": ""}])
def test_environment_provider_refuses_missing_or_empty_value(environment: dict[str, str]) -> None:
    with pytest.raises(CredentialConfigurationError, match="missing or empty"):
        EnvironmentCredentialProvider(environment).resolve("NETBOX_TOKEN")


def test_reference_resolution_does_not_mutate_declared_package() -> None:
    package = _package()
    before = package.model_dump(mode="json")

    value = resolve_reference(package, "netbox-token", environment={"NETBOX_TOKEN": "runtime-secret"})

    assert value == "runtime-secret"
    assert package.model_dump(mode="json") == before
    assert "runtime-secret" not in str(package.model_dump(mode="json"))


def test_unknown_provider_is_refused_without_reading_environment() -> None:
    data = _package().model_dump(mode="json")
    data["credentials"]["netbox-token"]["provider"] = "vault"
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match="provider 'vault' is not installed"):
        resolve_reference(package, "netbox-token", environment={"NETBOX_TOKEN": "secret"})


def test_all_bundled_adapter_modules_have_static_declarations() -> None:
    expected = {
        "aci",
        "genericrestapi",
        "infrahub",
        "ipfabricsync",
        "nautobot",
        "netbox",
        "peeringmanager",
        "prometheus",
        "slurpitsync",
    }

    assert set(BUILTIN_ADAPTER_CAPABILITIES) == expected
    assert all(capability.contract_version == 1 for capability in BUILTIN_ADAPTER_CAPABILITIES.values())


def test_bundled_destination_writes_only_advertise_remote_operations() -> None:
    assert {
        name: capability.supported_destination_write_operations
        for name, capability in BUILTIN_ADAPTER_CAPABILITIES.items()
        if "destination" in capability.roles
    } == {
        "infrahub": frozenset({"create", "update"}),
        "peeringmanager": frozenset({"update"}),
    }


@pytest.mark.parametrize("capability", BUILTIN_ADAPTER_CAPABILITIES.values(), ids=lambda item: item.adapter_name)
def test_all_bundled_supported_setting_shapes_validate(capability: AdapterConfigurationCapabilities) -> None:
    credential_node = {"$credential": "adapter-credential"}
    generic_rest_settings: dict[str, object] = {
        "url": "https://api.example",
        "api_endpoint": "/api/v1",
        "auth_method": "basic",
        "token": credential_node,
        "username": credential_node,
        "password": credential_node,
        "timeout": 30,
        "verify_ssl": True,
        "response_key_pattern": "results",
    }
    settings_by_adapter: dict[str, dict[str, object]] = {
        "aci": {
            "url": "https://aci.example",
            "username": credential_node,
            "password": credential_node,
            "verify": True,
            "api_endpoint": "api",
        },
        "genericrestapi": generic_rest_settings,
        "infrahub": {
            "url": "https://infrahub.example",
            "token": credential_node,
            "verify_ssl": True,
            "branch": "main",
            "source": "network-source",
            "owner": "network-owner",
        },
        "ipfabricsync": {"base_url": "https://ipfabric.example", "auth": credential_node, "verify_ssl": True},
        "nautobot": {"url": "https://nautobot.example", "token": credential_node, "verify_ssl": True},
        "netbox": {"url": "https://netbox.example", "token": credential_node, "verify_ssl": True},
        "peeringmanager": generic_rest_settings,
        "prometheus": {
            "mode": "api",
            "url": "https://prometheus.example",
            "endpoint": "/api/v1/query",
            "timeout": 10,
            "verify_ssl": True,
            "auth_method": "bearer",
            "username": credential_node,
            "password": credential_node,
            "token": credential_node,
            "promql": {"resources": {"node_info": "node_uname_info"}},
        },
        "slurpitsync": {
            "url": "https://slurpit.example",
            "api_key": credential_node,
            "token": credential_node,
            "verify_ssl": True,
        },
    }
    data = _package().model_dump(mode="json")
    data["configuration"]["source"] = {
        "name": capability.adapter_name,
        "settings": settings_by_adapter[capability.adapter_name],
    }
    data["credentials"]["adapter-credential"] = {"provider": "env", "identifier": "ADAPTER_CREDENTIAL"}

    validate_package_credentials(ConfigurationPackage.model_validate(data))


def test_capability_lookup_is_case_insensitive_but_unknown_is_refused() -> None:
    assert get_adapter_capabilities("NetBox").adapter_name == "netbox"
    with pytest.raises(UnknownAdapterCapabilitiesError, match="no configuration capability declaration"):
        get_adapter_capabilities("custom")


def test_source_only_capability_cannot_claim_destination_writes() -> None:
    with pytest.raises(ValueError, match="source-only"):
        AdapterConfigurationCapabilities(
            adapter_name="example",
            roles=frozenset({"source"}),
            supported_destination_write_operations=frozenset({"create"}),
        )


def test_capability_collections_are_normalized_and_immutable() -> None:
    roles = {"source"}
    allowed_settings = {"token"}
    paths = ["token"]
    writes: set[str] = set()
    capability = AdapterConfigurationCapabilities(
        adapter_name="example",
        roles=cast("Any", roles),
        allowed_settings=cast("Any", allowed_settings),
        credential_setting_paths=cast("Any", paths),
        supported_destination_write_operations=cast("Any", writes),
    )

    roles.add("destination")
    allowed_settings.add("password")
    paths.append("password")
    writes.add("create")

    assert capability.roles == frozenset({"source"})
    assert capability.allowed_settings == frozenset({"token"})
    assert capability.credential_setting_paths == ("token",)
    assert capability.supported_destination_write_operations == frozenset()


def test_capability_credential_paths_must_be_allowed_settings() -> None:
    with pytest.raises(ValueError, match="credential paths outside allowed settings"):
        AdapterConfigurationCapabilities(
            adapter_name="example",
            roles=frozenset({"source"}),
            allowed_settings=frozenset({"url"}),
            credential_setting_paths=("client_secret",),
        )


@pytest.mark.parametrize(
    ("roles", "writes", "message"),
    [
        ({"reader"}, set(), "unsupported roles"),
        ({"destination"}, {"replace"}, "unsupported destination write operations"),
    ],
)
def test_capability_rejects_unsupported_literal_values(
    roles: set[str],
    writes: set[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        AdapterConfigurationCapabilities(
            adapter_name="example",
            roles=cast("Any", roles),
            supported_destination_write_operations=cast("Any", writes),
        )


def test_wrong_adapter_role_is_refused() -> None:
    data = _package().model_dump(mode="json")
    data["configuration"]["destination"] = {"name": "netbox", "settings": {}}
    package = ConfigurationPackage.model_validate(data)

    with pytest.raises(CredentialConfigurationError, match="does not support the destination role"):
        validate_package_credentials(package)


def test_findings_use_deterministic_interface_order() -> None:
    findings = [
        ValidationFinding(code="optional-field", severity="warning", location="/z", message="optional"),
        ValidationFinding(code="missing-field", severity="error", location="/a", message="missing"),
        ValidationFinding(code="another-error", severity="error", location="/a", message="another"),
        ValidationFinding(code="warning-first", severity="warning", location="/a", message="warning"),
    ]

    assert [finding.code for finding in sort_findings(findings)] == [
        "another-error",
        "missing-field",
        "warning-first",
        "optional-field",
    ]
