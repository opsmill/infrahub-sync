"""The preview smoke's request bodies are the ones the shipped API accepts.

These tests run offline. The smoke itself only runs against a live stack, so a request
body that drifted from the API model used to surface as a live 422 during `preview.up`
— on a perfectly healthy environment — and never in branch CI. Validating each body
against the model the route declares closes that gap where it is cheap to close.
"""

from __future__ import annotations

import pytest

from infrahub_sync.client.models import ApplyRunRequest, ConfigMutationRequest, CreateRunRequest
from tests.preview import test_managed_api as smoke

INFRAHUB_URL = "http://localhost:8080"
CHECKSUM = "a" * 64


def test_the_registration_body_is_a_valid_config_mutation_request() -> None:
    ConfigMutationRequest.model_validate(smoke.register_request(INFRAHUB_URL))


def test_the_run_body_is_a_valid_create_run_request() -> None:
    """`CreateRunRequest` forbids extras, so a retired field fails here, not live."""
    request = CreateRunRequest.model_validate(smoke.create_run_request("config-001", 1))

    assert request.config_id == "config-001"
    assert request.registry_version == 1
    assert request.operation == "plan"


def test_the_apply_body_is_a_valid_apply_run_request() -> None:
    assert ApplyRunRequest.model_validate(smoke.apply_run_request(CHECKSUM)).expected_checksum == CHECKSUM


@pytest.mark.parametrize("retired", ["sync_name", "configuration_reference"])
def test_no_smoke_body_carries_a_retired_run_field(retired: str) -> None:
    """The registered API names a configuration version, never a directory on disk."""
    bodies = (
        smoke.register_request(INFRAHUB_URL),
        smoke.create_run_request("config-001", 1),
        smoke.apply_run_request(CHECKSUM),
    )

    assert all(retired not in body for body in bodies)


def test_the_registered_package_parses_as_a_configuration_package() -> None:
    """The package must be registrable before any of the rest of the lifecycle matters."""
    from infrahub_sync.configuration import parse_configuration_package

    package = parse_configuration_package(smoke.smoke_package(INFRAHUB_URL))

    assert package.configuration.destination.name == "infrahub"
    assert package.configuration.source.name == "infrahub"


def test_the_registered_package_uses_no_filesystem_adapter() -> None:
    """Registered execution resolves installed adapters only; a path would refuse."""
    configuration = smoke.smoke_package(INFRAHUB_URL)["configuration"]

    for side in ("source", "destination"):
        assert "adapter" not in configuration[side], side


def test_the_registered_package_declares_no_credential_value() -> None:
    """Only a credential *reference* is posted; the worker resolves it from its own env."""
    rendered = repr(smoke.smoke_package(INFRAHUB_URL))

    assert "$credential" in rendered
    assert "INFRAHUB_API_TOKEN" in rendered
    assert "token': '" not in rendered
