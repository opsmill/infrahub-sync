"""The Invoke task names follow `dev/specs/010-prefect-from-sync-image/contracts/invoke-tasks.md`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tasks import ns

if TYPE_CHECKING:
    from invoke import Collection

# The only tasks outside a namespace, as in Infrahub's own tasks.
TOP_LEVEL = {"format", "lint"}

# Every name the contract lists as existing after the rename.
REQUIRED = {
    "format",
    "lint",
    "docs.docusaurus",
    "docs.generate",
    "docs.format",
    "docs.lint",
    "linter.format",
    "linter.lint",
    "tests.tests-unit",
    "tests.tests-integration",
    "tests.all",
    "tests.check-310",
    "dev.build",
    "dev.start",
    "dev.deps",
    "dev.api",
    "dev.worker",
    "dev.restart",
    "dev.stop",
    "dev.destroy",
    "dev.status",
    "dev.logs",
    "dev.seed",
    "dev.smoke",
    "netbox.start",
    "netbox.destroy",
    "netbox.seed",
}

# Every name the contract removes.
REMOVED = {
    "docusaurus",
    "generate-doc",
    "tests-unit",
    "tests-integration",
    "tests-all",
    "check-310",
    "build",
    "start",
    "destroy",
    "preview.up",
    "preview.seed",
    "preview.smoke",
    "preview.status",
    "preview.logs",
    "preview.down",
    "netbox.up",
    "netbox.down",
}


def _names(collection: Collection, prefix: str = "") -> list[str]:
    """Every task name of a collection, dotted by namespace, in walk order."""
    names = [f"{prefix}{name}" for name in collection.tasks]
    for sub_name, sub in collection.collections.items():
        names.extend(_names(sub, f"{prefix}{sub_name}."))
    return names


NAMES = _names(ns)


def test_only_format_and_lint_are_top_level() -> None:
    assert {name for name in NAMES if "." not in name} == TOP_LEVEL


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_every_contract_name_exists(name: str) -> None:
    assert name in NAMES


@pytest.mark.parametrize("name", sorted(REMOVED))
def test_no_removed_name_exists(name: str) -> None:
    assert name not in NAMES


def test_no_name_appears_twice() -> None:
    assert len(NAMES) == len(set(NAMES))


def test_no_preview_namespace_exists() -> None:
    assert "preview" not in ns.collections
