"""The removed credential format: the ``credentials`` block and ``$credential`` references (spec 027, FR-021).

A package in that format is refused when it is parsed, with a message that names
``credential: <name>``. A version that was stored before the removal is refused when a run of it
starts, and by the worker route that releases stored credential values, with the code
``credential-format-removed`` and a message that says to register a new version.

The refusals name no reference, no provider and no identifier of the refused package.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from infrahub_sync.configuration import (
    ConfigurationPackage,
    ConfigurationPackageParseError,
    parse_configuration_package,
)
from infrahub_sync.configuration.models import RemovedCredentialFormatError
from tests.configuration.validation_packages import credential_package_data
from tests.helpers.removed_credential_format import (
    REMOVED_IDENTIFIER,
    REMOVED_REFERENCE_NAME,
    removed_format_content,
)

REFERENCE_NAME = REMOVED_REFERENCE_NAME
IDENTIFIER = REMOVED_IDENTIFIER
NAMES_THE_NEW_FORMAT = "credential: <name>"
FORMAT_REMOVED = "credential-format-removed"
WORKER_TOKEN = "worker-token-of-the-environment-1"  # noqa: S105 - a test value, not a secret.


def _with_credentials_block(block: dict[str, Any]) -> dict[str, Any]:
    data = credential_package_data()
    data["credentials"] = block
    return data


def _with_reference_in_settings() -> dict[str, Any]:
    data = credential_package_data()
    del data["configuration"]["source"]["credential"]
    data["configuration"]["source"]["settings"]["token"] = {"$credential": REFERENCE_NAME}
    return data


def _with_nested_reference() -> dict[str, Any]:
    data = credential_package_data()
    data["configuration"]["source"]["settings"]["params"] = {"filters": [{"value": {"$credential": REFERENCE_NAME}}]}
    return data


def _old_format() -> dict[str, Any]:
    """Return a package as a version stored before the removal declares it."""
    return removed_format_content(credential_package_data())


REMOVED_FORMAT_PACKAGES = {
    "credentials-block": lambda: _with_credentials_block(
        {REFERENCE_NAME: {"provider": "env", "identifier": IDENTIFIER}}
    ),
    "empty-credentials-block": lambda: _with_credentials_block({}),
    "reference-in-a-credential-setting": _with_reference_in_settings,
    "reference-nested-in-a-setting": _with_nested_reference,
    "block-and-references": _old_format,
}


@pytest.mark.parametrize("case", sorted(REMOVED_FORMAT_PACKAGES))
def test_a_package_in_the_removed_format_is_refused_at_parse_time(case: str) -> None:
    data = REMOVED_FORMAT_PACKAGES[case]()

    with pytest.raises(RemovedCredentialFormatError) as refused:
        parse_configuration_package(data)

    message = str(refused.value)
    assert NAMES_THE_NEW_FORMAT in message
    assert REFERENCE_NAME not in message
    assert IDENTIFIER not in message
    assert isinstance(refused.value, ConfigurationPackageParseError)


def test_the_refusal_names_the_credentials_block_and_the_references_it_found() -> None:
    with pytest.raises(RemovedCredentialFormatError) as block:
        parse_configuration_package(_with_credentials_block({}))
    with pytest.raises(RemovedCredentialFormatError) as reference:
        parse_configuration_package(_with_reference_in_settings())
    with pytest.raises(RemovedCredentialFormatError) as both:
        parse_configuration_package(_old_format())

    assert "`credentials` block" in str(block.value)
    assert "$credential" not in str(block.value)
    assert "`$credential` references" in str(reference.value)
    assert "`credentials` block" not in str(reference.value)
    assert "`credentials` block" in str(both.value)
    assert "`$credential` references" in str(both.value)


@pytest.mark.parametrize("case", sorted(REMOVED_FORMAT_PACKAGES))
def test_the_package_model_refuses_the_removed_format_without_the_parse_function(case: str) -> None:
    with pytest.raises(ValidationError) as refused:
        ConfigurationPackage.model_validate(REMOVED_FORMAT_PACKAGES[case]())

    assert refused.value.error_count() == 1
    rendered = str(refused.value.errors(include_input=False, include_url=False))
    assert REFERENCE_NAME not in rendered
    assert IDENTIFIER not in rendered


def test_the_removed_format_is_reported_before_every_other_defect_of_the_package() -> None:
    data = _old_format()
    data["configuration"]["unknown_field"] = True
    data["unknown_root_field"] = True

    with pytest.raises(RemovedCredentialFormatError) as refused:
        parse_configuration_package(data)

    assert "unknown" not in str(refused.value)


def test_a_package_that_names_stored_credentials_is_parsed_and_declares_no_credentials_block() -> None:
    package = parse_configuration_package(credential_package_data())

    assert "credentials" not in package.declared_content()
    assert not hasattr(package, "credentials")


def test_a_setting_value_that_only_looks_like_a_reference_name_is_not_the_removed_format() -> None:
    data = credential_package_data()
    data["configuration"]["source"]["settings"]["params"] = {"note": "$credential", "credentials": "credentials"}

    package = parse_configuration_package(copy.deepcopy(data))

    assert package.configuration.source.settings is not None
