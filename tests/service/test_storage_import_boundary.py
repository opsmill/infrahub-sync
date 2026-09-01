"""Static guard for the deployed service storage composition boundary."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

SERVICE_PACKAGE = Path(__file__).resolve().parents[2] / "infrahub_sync" / "service"
FORBIDDEN_PROJECTION = "local_product_projection"


def _local_projection_references(path: Path) -> tuple[str, ...]:
    """Return direct imports or references to the local projection."""
    references: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == FORBIDDEN_PROJECTION for alias in node.names):
            references.append(f"line {node.lineno}: import")
        elif isinstance(node, ast.Attribute) and node.attr == FORBIDDEN_PROJECTION:
            references.append(f"line {node.lineno}: attribute")
        elif isinstance(node, ast.Name) and node.id == FORBIDDEN_PROJECTION:
            references.append(f"line {node.lineno}: name")
    return tuple(references)


def test_deployed_managed_runtime_cannot_import_or_reference_the_local_projection() -> None:
    """API and worker runtime modules must stay on the service storage factory."""
    offenders = {
        str(path.relative_to(SERVICE_PACKAGE)): references
        for path in sorted(SERVICE_PACKAGE.rglob("*.py"))
        if (references := _local_projection_references(path))
    }

    assert offenders == {}


def test_deployed_runtime_defaults_bind_the_managed_projection_call_boundary() -> None:
    """API and worker defaults call the service factory while retaining explicit injection."""
    pytest.importorskip("boto3")
    pytest.importorskip("prefect")
    pytest.importorskip("psycopg")

    from infrahub_sync.service import flow, serve, storage

    api_default = inspect.signature(serve.build_app).parameters["projection_factory"].default
    worker_default = inspect.signature(flow._runtime).parameters["projection_factory"].default

    assert api_default is storage.service_product_projection
    assert worker_default is storage.service_product_projection
