"""The shipped refusal contract of ``validate_package_credentials``, pinned site by site.

Every message below was observed by executing ``c767067``. The accumulating core replaces the
raise-first checks with finding producers, and the wrapper reproduces the message belonging to
the finding the shipped code would have raised first — the **execution-order** element, not the
sort-order one. A drift in either the messages or the check order moves an assertion here.

Each of the eighteen shipped pins carries one defect, so none of them can see the order two
*different* checks run in. ``CROSS_CHECK_REFUSALS`` covers that separately: each of its packages
carries two defects that different checks report, so reordering those checks changes which
message the wrapper raises and the pin moves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.configuration import CredentialConfigurationError, validate_package_credentials
from tests.configuration.validation_packages import package, package_data

if TYPE_CHECKING:
    from collections.abc import Callable


def _unknown_provider(data: dict[str, Any]) -> None:
    data["credentials"]["netbox-token"]["provider"] = "vault"


def _invalid_identifier(data: dict[str, Any]) -> None:
    data["credentials"]["netbox-token"]["identifier"] = "INVALID-NAME"


def _undeclared_store_type(data: dict[str, Any]) -> None:
    data["configuration"]["store"] = {"type": "mystery", "settings": {"url": "redis://localhost"}}


def _undeclared_store_setting(data: dict[str, Any]) -> None:
    data["configuration"]["store"] = {"type": "redis", "settings": {"host": "localhost", "bogus": 1}}


def _store_names_unknown_reference(data: dict[str, Any]) -> None:
    data["configuration"]["store"] = {"type": "redis", "settings": {"password": {"$credential": "nope"}}}


def _store_carries_inline_value(data: dict[str, Any]) -> None:
    data["configuration"]["store"] = {"type": "redis", "settings": {"password": "declared-in-line"}}


def _store_carries_malformed_reference(data: dict[str, Any]) -> None:
    node = {"$credential": "netbox-token", "fallback": "declared-in-line"}
    data["configuration"]["store"] = {"type": "redis", "settings": {"password": node}}


def _unknown_adapter(data: dict[str, Any]) -> None:
    data["configuration"]["source"]["name"] = "NetBox"


def _role_mismatch(data: dict[str, Any]) -> None:
    data["configuration"]["destination"] = {"name": "netbox", "settings": {}}


def _undeclared_adapter_setting(data: dict[str, Any]) -> None:
    data["configuration"]["source"]["settings"]["bogus"] = 1


def _adapter_names_unknown_reference(data: dict[str, Any]) -> None:
    data["configuration"]["source"]["settings"]["token"] = {"$credential": "nope"}


def _adapter_carries_inline_value(data: dict[str, Any]) -> None:
    inline_value = "declared-in-line"
    data["configuration"]["source"]["settings"]["token"] = inline_value


def _url_is_not_a_string(data: dict[str, Any]) -> None:
    data["configuration"]["source"]["settings"]["url"] = {"nested": "value"}


def _url_carries_request_material(data: dict[str, Any]) -> None:
    data["configuration"]["source"]["settings"]["url"] = "https://demo.netbox.dev/?page=1"


def _url_is_not_absolute(data: dict[str, Any]) -> None:
    data["configuration"]["source"]["settings"]["url"] = "demo.netbox.dev"


def _endpoint_is_not_relative(data: dict[str, Any]) -> None:
    data["configuration"]["source"] = {
        "name": "genericrestapi",
        "settings": {"url": "https://api.example", "api_endpoint": "https://evil.example/api"},
    }


def _reference_outside_declared_paths(data: dict[str, Any]) -> None:
    data["configuration"]["source"]["settings"]["verify_ssl"] = {"$credential": "netbox-token"}


def _adapter_validator_refuses(data: dict[str, Any]) -> None:
    data["configuration"]["source"] = {"name": "genericrestapi", "settings": {"url": "https://api.example"}}
    data["configuration"]["schema_mapping"] = [{"name": "Device", "mapping": "https://evil.example/devices"}]


SHIPPED_REFUSALS: tuple[tuple[str, Callable[[dict[str, Any]], None], str], ...] = (
    (
        "unknown-credential-provider",
        _unknown_provider,
        "credential reference 'netbox-token' uses provider 'vault', which is not installed",
    ),
    (
        "malformed-credential-reference-declaration",
        _invalid_identifier,
        "credential reference 'netbox-token' has an invalid environment identifier",
    ),
    (
        "missing-store-capabilities",
        _undeclared_store_type,
        "store type 'mystery' has no configuration capability declaration",
    ),
    (
        "undeclared-setting-store",
        _undeclared_store_setting,
        "store type 'redis' contains unsupported declared settings: [\"bogus\"]",
    ),
    (
        "unknown-credential-reference-store",
        _store_names_unknown_reference,
        "/configuration/store/settings/password names unknown credential reference 'nope'",
    ),
    (
        "inline-credential-value-store",
        _store_carries_inline_value,
        "/configuration/store/settings/password contains an inline credential value",
    ),
    (
        "malformed-credential-reference-store",
        _store_carries_malformed_reference,
        "/configuration/store/settings/password contains a malformed credential reference",
    ),
    (
        "missing-adapter",
        _unknown_adapter,
        "adapter 'NetBox' has no configuration capability declaration",
    ),
    (
        "adapter-role-mismatch",
        _role_mismatch,
        "adapter 'netbox' does not support the destination role",
    ),
    (
        "undeclared-setting-adapter",
        _undeclared_adapter_setting,
        "adapter 'netbox' contains unsupported declared settings for the source role: [\"bogus\"]",
    ),
    (
        "unknown-credential-reference-adapter",
        _adapter_names_unknown_reference,
        "/configuration/source/settings/token names unknown credential reference 'nope'",
    ),
    (
        "inline-credential-value-adapter",
        _adapter_carries_inline_value,
        "/configuration/source/settings/token contains an inline credential value",
    ),
    (
        "setting-not-a-string",
        _url_is_not_a_string,
        "/configuration/source/settings/url must be declared as a string",
    ),
    (
        "setting-contains-credential-material",
        _url_carries_request_material,
        "/configuration/source/settings/url cannot contain user information, query parameters, or fragments",
    ),
    (
        "endpoint-not-absolute",
        _url_is_not_absolute,
        "/configuration/source/settings/url must be an absolute http or https URL",
    ),
    (
        "endpoint-not-relative",
        _endpoint_is_not_relative,
        "/configuration/source/settings/api_endpoint must be a relative request path without a scheme or authority",
    ),
    (
        "credential-path-not-declared",
        _reference_outside_declared_paths,
        "/configuration/source/settings/verify_ssl is not a credential-bearing setting",
    ),
    (
        "adapter-validator-finding",
        _adapter_validator_refuses,
        "/configuration/schema_mapping/0/mapping: genericrestapi schema mapping endpoints must be "
        "a relative request path without authority, user information, query parameters, or fragments",
    ),
)


def _unknown_provider_and_undeclared_store_type(data: dict[str, Any]) -> None:
    _unknown_provider(data)
    _undeclared_store_type(data)


def _validator_refusal_and_inline_adapter_token(data: dict[str, Any]) -> None:
    data["configuration"]["source"] = {
        "name": "genericrestapi",
        "settings": {"url": "https://api.example", "token": "declared-in-line"},
    }
    data["configuration"]["schema_mapping"] = [{"name": "Device", "mapping": "https://evil.example/devices"}]


# Two defects, reported by two different checks, in one package. Both messages were re-observed
# by executing c767067; each pin names the check that has to keep running first.
CROSS_CHECK_REFUSALS: tuple[tuple[str, Callable[[dict[str, Any]], None], str], ...] = (
    (
        # The credential declarations run before the store surface.
        "credential-declarations-before-store",
        _unknown_provider_and_undeclared_store_type,
        "credential reference 'netbox-token' uses provider 'vault', which is not installed",
    ),
    (
        # Inside one adapter role, the adapter-owned validator runs before the credential paths.
        "adapter-validator-before-credential-paths",
        _validator_refusal_and_inline_adapter_token,
        "/configuration/schema_mapping/0/mapping: genericrestapi schema mapping endpoints must be "
        "a relative request path without authority, user information, query parameters, or fragments",
    ),
)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [pytest.param(mutate, message, id=name) for name, mutate, message in CROSS_CHECK_REFUSALS],
)
def test_the_order_two_checks_run_in_decides_the_shipped_message(
    mutate: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    data = package_data()
    mutate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package(data))

    assert str(caught.value) == expected


_REFUSAL_CASES = [pytest.param(mutate, message, id=name) for name, mutate, message in SHIPPED_REFUSALS]


@pytest.mark.parametrize(("mutate", "expected"), _REFUSAL_CASES)
def test_wrapper_reproduces_the_shipped_first_error_message(
    mutate: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    data = package_data()
    mutate(data)

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package(data))

    assert str(caught.value) == expected


@pytest.mark.parametrize(("mutate", "expected"), _REFUSAL_CASES)
def test_every_validation_refusal_raises_one_exception_type(
    mutate: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    # OES-15: the unknown adapter used to escape as UnknownAdapterCapabilitiesError.
    del expected
    data = package_data()
    mutate(data)

    with pytest.raises(CredentialConfigurationError):
        validate_package_credentials(package(data))


def test_a_warning_only_package_does_not_raise() -> None:
    # Errors prevent execution; warnings do not. A package whose only findings are
    # warnings validates with no wrapper raise, so registration cannot refuse it.
    data = package_data()
    data["omissions"] = [{"kind": "InfraDevice", "fields": ["serial_number"]}]

    validate_package_credentials(package(data))


def test_the_wrapper_raises_the_first_error_even_when_warnings_precede_it() -> None:
    # The element-zero pin's one sanctioned re-reading: "the first finding" becomes "the
    # first *error* in execution order". A warning accumulates ahead of this error, and
    # the raised message is still the error's own.
    data = package_data()
    data["configuration"]["schema_mapping"] = [{"name": "InfraDevice", "mapping": "dcim.devices"}]
    data["omissions"] = [{"kind": "InfraCircuit"}, {"kind": "InfraDevice"}]

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package(data))

    assert str(caught.value) == "omission names content a schema mapping also maps"


def test_a_legacy_defect_keeps_its_shipped_message_when_warnings_are_also_declared() -> None:
    # The warning families run after the shipped execution order, so a package carrying a
    # legacy defect raises the same message it raised before omissions existed.
    data = package_data()
    data["credentials"]["netbox-token"]["provider"] = "vault"
    data["omissions"] = [{"kind": "InfraDevice"}]

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package(data))

    assert str(caught.value) == "credential reference 'netbox-token' uses provider 'vault', which is not installed"


def _two_defective_declarations(first_name: str, second_name: str) -> dict[str, Any]:
    data = package_data()
    declarations = {
        "zeta": {"provider": "vault", "identifier": "ZETA_TOKEN"},
        "alpha": {"provider": "env", "identifier": "ALPHA-TOKEN"},
    }
    data["credentials"] = {
        **data["credentials"],
        first_name: declarations[first_name],
        second_name: declarations[second_name],
    }
    return data


@pytest.mark.parametrize(
    ("first_name", "second_name", "expected"),
    [
        pytest.param(
            "zeta",
            "alpha",
            "credential reference 'zeta' uses provider 'vault', which is not installed",
            id="zeta-declared-first",
        ),
        pytest.param(
            "alpha",
            "zeta",
            "credential reference 'alpha' has an invalid environment identifier",
            id="alpha-declared-first",
        ),
    ],
)
def test_declaration_order_decides_which_defect_is_reported_first(
    first_name: str,
    second_name: str,
    expected: str,
) -> None:
    # Two defects inside one check: the eighteen single-defect pins cannot see iteration order,
    # and sorting the declarations would silently change the message for one of these packages.
    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package(_two_defective_declarations(first_name, second_name)))

    assert str(caught.value) == expected
