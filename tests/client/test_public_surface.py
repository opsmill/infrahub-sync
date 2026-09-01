"""Public package, dependency, docs, and workflow closure tests."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

import infrahub_sync.client as client_package
from infrahub_sync.client import SyncClient, SyncClientError

ROOT = Path(__file__).resolve().parents[2]


def test_embedded_v1_api_is_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("infrahub_sync.api.v1")


def test_httpx_is_a_base_dependency_only() -> None:
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    base, optional = metadata.split("[project.optional-dependencies]", maxsplit=1)
    service, _dev = optional.split("dev = [", maxsplit=1)

    assert '"httpx>=0.27,<1"' in base
    assert "httpx" not in service


def test_typing_extensions_supports_the_python_310_client_tests() -> None:
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"typing-extensions>=4.4"' in metadata


def test_python_api_docs_describe_only_the_http_client() -> None:
    docs = (ROOT / "docs/docs/reference/python-api.mdx").read_text(encoding="utf-8")

    assert "SyncClient" in docs
    assert "infrahub_sync.api.v1" not in docs
    assert "product_cache_location" not in docs
    assert "in-process" not in docs
    assert "config_directory" not in docs
    assert "HTTP provides no transport encryption for bearer credentials" in docs


def test_public_client_surface_has_concise_docstrings() -> None:
    for name in client_package.__all__:
        assert getattr(client_package, name).__doc__, name
    for name, operation in inspect.getmembers(SyncClient, inspect.isfunction):
        if not name.startswith("_"):
            assert operation.__doc__, name


def test_client_originated_exception_taxonomy_is_closed() -> None:
    pending = list(SyncClientError.__subclasses__())
    subclasses: set[type[SyncClientError]] = set()
    while pending:
        error_type = pending.pop()
        subclasses.add(error_type)
        pending.extend(error_type.__subclasses__())

    assert {error_type.__name__ for error_type in subclasses} == {
        "APIError",
        "ClientInputError",
        "ClientTimeoutError",
        "CompatibilityError",
        "ConfigsAPIError",
        "ProtocolError",
        "RunTerminalError",
        "RunWaitTimeoutError",
        "TransportError",
    }


def test_pr_workflow_can_be_dispatched_manually() -> None:
    workflow = (ROOT / ".github/workflows/trigger-pr-develop.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
