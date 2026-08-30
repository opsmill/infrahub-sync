"""Public package, dependency, docs, and workflow closure tests."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_embedded_v1_api_is_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("infrahub_sync.api.v1")


def test_httpx_is_a_base_dependency_only() -> None:
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    base, optional = metadata.split("[project.optional-dependencies]", maxsplit=1)
    managed, _dev = optional.split("dev = [", maxsplit=1)

    assert '"httpx>=0.27,<1"' in base
    assert "httpx" not in managed


def test_python_api_docs_describe_only_the_http_client() -> None:
    docs = (ROOT / "docs/docs/reference/python-api.mdx").read_text(encoding="utf-8")

    assert "SyncClient" in docs
    assert "infrahub_sync.api.v1" not in docs
    assert "product_cache_location" not in docs
    assert "in-process" not in docs
    assert "config_directory" not in docs


def test_pr_workflow_can_be_dispatched_manually() -> None:
    workflow = (ROOT / ".github/workflows/trigger-pr-develop.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
