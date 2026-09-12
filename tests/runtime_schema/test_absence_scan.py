"""AR3/AR9: the runtime model path holds no global state and reaches no generated code."""

from __future__ import annotations

import ast
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest

from infrahub_sync import runtime_schema

PACKAGE = Path(runtime_schema.__file__).parent
MODULES = sorted(PACKAGE.glob("*.py"))

# Names that would make one run's construction reachable from another, or would put
# generated Python back on the registered path.
FORBIDDEN_NAMES = frozenset(
    {
        "render_adapter",
        "render_template",
        "import_adapter",
        "modules",
        "path",
        "setattr",
    }
)


def _module_source(module: Path) -> ast.Module:
    return ast.parse(module.read_text(encoding="utf-8"), filename=str(module))


def _assigned_name(node: ast.Assign | ast.AnnAssign) -> str:
    target = node.targets[0] if isinstance(node, ast.Assign) else node.target
    return target.id if isinstance(target, ast.Name) else ast.dump(target)


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.name)
def test_no_module_holds_a_mutable_global(module: Path) -> None:
    """A module-level mutable container would outlive one run's classes."""
    tree = _module_source(module)
    assignments = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
    mutable = [
        _assigned_name(node)
        for node in assignments
        if isinstance(node.value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp))
    ]

    # ``__all__`` names exports; it holds no run state.
    assert [name for name in mutable if name != "__all__"] == []


@pytest.mark.parametrize("module", MODULES, ids=lambda module: module.name)
def test_no_module_reaches_generated_code_or_the_import_system(module: Path) -> None:
    """Rendering, importing generated files, and editing sys.path/sys.modules are absent."""
    tree = _module_source(module)
    used = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    # ``setattr`` is how one run's classes are bound onto that run's adapter instance.
    allowed = {"setattr"} if module.name == "worker.py" else set()

    assert (used & FORBIDDEN_NAMES) <= allowed


def test_the_only_shared_table_is_immutable() -> None:
    """The attribute-kind domain is shared across runs, so it must not be writable."""
    assert isinstance(runtime_schema.ATTRIBUTE_TYPE_DOMAIN, MappingProxyType)

    with pytest.raises(TypeError):
        cast("dict[str, Any]", runtime_schema.ATTRIBUTE_TYPE_DOMAIN)["Bandwidth"] = str
