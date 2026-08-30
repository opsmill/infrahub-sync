"""Static guard for the deployed managed storage composition boundary."""

from __future__ import annotations

import ast
from pathlib import Path

MANAGED_PACKAGE = Path(__file__).resolve().parents[2] / "infrahub_sync" / "managed"
FORBIDDEN_PROJECTION = "local_product_projection"


def _local_projection_references(path: Path) -> tuple[str, ...]:
    """Return direct imports or references to the standalone projection."""
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
    """API and worker runtime modules must stay on the managed storage factory."""
    offenders = {
        str(path.relative_to(MANAGED_PACKAGE)): references
        for path in sorted(MANAGED_PACKAGE.rglob("*.py"))
        if (references := _local_projection_references(path))
    }

    assert offenders == {}
