"""The Compose resolution helper names profiles, lists images and ignores the caller's environment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.compose.conftest import COMPOSE_FILE, CONTRACT_ENVIRONMENT
from tests.compose.stacks import image_references, resolve_stack

if TYPE_CHECKING:
    import pytest


def test_image_references_are_distinct_sorted_and_skip_build_only_services() -> None:
    """Two services on one image give one reference; a service with only `build` gives none."""
    model = {
        "services": {
            "b": {"image": "registry.example/b:1"},
            "a": {"image": "registry.example/a:1"},
            "a-again": {"image": "registry.example/a:1"},
            "built": {"build": {"context": "."}},
        }
    }
    assert image_references(model) == ["registry.example/a:1", "registry.example/b:1"]


def test_a_named_profile_adds_its_services() -> None:
    """The `cli` service of the operator file resolves only when its profile is named."""
    without = resolve_stack((COMPOSE_FILE,), environment=CONTRACT_ENVIRONMENT)
    with_cli = resolve_stack((COMPOSE_FILE,), profiles=("cli",), environment=CONTRACT_ENVIRONMENT)
    assert "cli" not in without.model["services"]
    assert "cli" in with_cli.model["services"]
    assert with_cli.images == image_references(with_cli.model)


def test_an_exported_variable_does_not_reach_the_resolved_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Sync image a developer exported is not the image the resolved stack runs."""
    monkeypatch.setenv("INFRAHUB_SYNC_DOCKER_IMAGE", "exported.example/not-the-default")
    resolved = resolve_stack((COMPOSE_FILE,), environment=CONTRACT_ENVIRONMENT)
    assert not any(image.startswith("exported.example/") for image in resolved.images)
