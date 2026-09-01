"""The preview smoke's request bodies are the ones the shipped API accepts.

These tests run offline. The smoke itself only runs against a live stack, so a request
body that drifted from the API model used to surface as a live 422 during `preview.up`
— on a perfectly healthy environment — and never in branch CI. Validating each body
against the model the route declares closes that gap where it is cheap to close.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from infrahub_sync.client.models import ApplyRunRequest, ConfigMutationRequest, CreateRunRequest
from tasks.preview import SHARED_DEVICE_NAME
from tests.preview import test_service_api as smoke

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


# ======================================================================================
# A plan that writes nothing must not read as a passing smoke
# ======================================================================================


def plan_summary(**by_action: int) -> dict[str, Any]:
    """One plan summary in the shape `GET /runs/{id}/plan` returns."""
    return {
        "by_action": dict(by_action),
        "by_kind": {"InfraDevice": sum(by_action.values())},
        "total": sum(by_action.values()),
        "delete_operations_computed": True,
        "deletes_not_executed": by_action.get("delete", 0),
    }


@pytest.mark.parametrize(
    "summary",
    [
        # What the controller's live run actually produced: an empty source against a
        # populated destination derives deletes only, a v2 apply records them without
        # executing them, and every terminal signal stays green with nothing written.
        pytest.param(plan_summary(delete=5), id="delete-only"),
        pytest.param(plan_summary(), id="empty"),
        pytest.param(plan_summary(create=1, delete=2), id="writes-but-skips-deletes"),
    ],
)
def test_a_plan_that_leaves_the_destination_unwritten_is_reported(summary: dict[str, Any]) -> None:
    assert smoke.unwritten_plan_reasons(summary) != []


@pytest.mark.parametrize(
    "summary",
    [
        pytest.param(plan_summary(create=1), id="create"),
        pytest.param(plan_summary(update=1), id="update"),
        pytest.param(plan_summary(create=2, update=3), id="create-and-update"),
    ],
)
def test_a_plan_that_writes_is_accepted(summary: dict[str, Any]) -> None:
    assert smoke.unwritten_plan_reasons(summary) == []


def test_the_report_names_both_reasons_a_delete_only_plan_writes_nothing() -> None:
    """Two distinct facts, so a failure says which one to fix."""
    reasons = smoke.unwritten_plan_reasons(plan_summary(delete=5))

    assert any("create or update" in reason for reason in reasons)
    assert any("skipped" in reason for reason in reasons)


# ======================================================================================
# Mirroring the destination copies values; it does not manufacture them
# ======================================================================================


@dataclass
class FakeAttr:
    """Stand-in for an ``InfrahubNodeSync`` attribute manager — only ``.value`` is read."""

    value: Any


class FakeNode:
    """Stand-in for ``InfrahubNodeSync`` exposing its attributes by name."""

    def __init__(self, **attributes: Any) -> None:  # noqa: ANN401 — an attribute holds any value
        for name, value in attributes.items():
            setattr(self, name, FakeAttr(value=value))


def test_mirroring_copies_every_mapped_field_from_the_destination() -> None:
    """The devices the destination already holds must compare equal after mirroring."""
    nodes = [FakeNode(name="core01", type="juniper mx204"), FakeNode(name="edge02", type="cisco nexus9000")]

    assert smoke.mirrored_device_payloads(nodes) == [
        {"name": "core01", "type": "juniper mx204"},
        {"name": "edge02", "type": "cisco nexus9000"},
    ]


def test_mirroring_manufactures_no_value_of_its_own() -> None:
    """The live failure: a literal in place of the real value made every mirror an update.

    An update rewriting a device's own unique attribute is rejected at the destination, so
    the apply died on its first operation with nothing written.
    """
    nodes = [FakeNode(name=f"device{index:02d}", type=f"model-{index}") for index in range(5)]

    payloads = smoke.mirrored_device_payloads(nodes)

    assert {payload["type"] for payload in payloads} == {f"model-{index}" for index in range(5)}
    assert len({payload["type"] for payload in payloads}) == len(payloads), "values collapsed to a constant"


def test_mirroring_covers_exactly_the_fields_the_package_maps() -> None:
    """A mapped field the mirror omits reappears as an update the plan cannot converge."""
    mapped = {
        field["name"] for field in smoke.smoke_package(INFRAHUB_URL)["configuration"]["schema_mapping"][0]["fields"]
    }

    assert set(smoke.SMOKE_FIELDS) == mapped
    assert set(smoke.mirrored_device_payloads([FakeNode(name="core01", type="juniper mx204")])[0]) == mapped


def test_mirroring_preserves_an_unset_value_rather_than_substituting_one() -> None:
    """An absent value is still the destination's value; substituting one is an update."""
    assert smoke.mirrored_device_payloads([FakeNode(name="core01", type=None)]) == [{"name": "core01", "type": None}]


# ======================================================================================
# One shared device is mutated, so the plan is an update rather than a create
# ======================================================================================


def test_every_run_mutates_to_a_value_no_earlier_run_used() -> None:
    """A fixed value would converge and silently empty the plan the smoke asserts on.

    The first apply writes it to the destination, the next run's mirror copies it back
    into `main`, and the shared device then compares equal — no update left to prove.
    """
    assert len({smoke.mutated_device_type() for _ in range(50)}) == 50


def test_the_mutation_covers_exactly_the_fields_the_package_maps() -> None:
    """A mapped field the mutation omits is one the destination sees cleared."""
    assert set(smoke.mutation_payload("preview-smoke-abc123")) == set(smoke.SMOKE_FIELDS)


def test_the_mutation_targets_the_device_seeded_before_the_fork() -> None:
    """Any other name is absent from the destination, which makes the plan a create."""
    assert smoke.mutation_payload("preview-smoke-abc123")["name"] == SHARED_DEVICE_NAME


def test_mirroring_then_mutating_differs_from_the_destination_in_one_device() -> None:
    """The whole plan shape in one place: no create, no delete, exactly one update."""
    nodes = [FakeNode(name=SHARED_DEVICE_NAME, type="juniper mx204"), FakeNode(name="edge02", type="cisco nexus9000")]
    destination = {payload["name"]: payload for payload in smoke.mirrored_device_payloads(nodes)}

    mutated = smoke.mutation_payload(smoke.mutated_device_type())
    source = {**destination, mutated["name"]: mutated}

    assert set(source) == set(destination), "the same devices on both sides: nothing to create, nothing to delete"
    assert [name for name in source if source[name] != destination[name]] == [SHARED_DEVICE_NAME]
