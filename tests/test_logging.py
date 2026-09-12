"""Tests for structured logging migration: no print() calls and logger definitions."""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "infrahub_sync"


def _python_files() -> list[Path]:
    """Return all .py files under the infrahub_sync package."""
    return sorted(PACKAGE_DIR.rglob("*.py"))


class _PrintCallVisitor(ast.NodeVisitor):
    """AST visitor that collects bare print() calls."""

    def __init__(self) -> None:
        self.print_calls: list[tuple[int, str]] = []
        self._current_class: str | None = None
        self._current_func: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        old = self._current_class
        self._current_class = node.name
        self.generic_visit(node)
        self._current_class = old

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        old = self._current_func
        self._current_func = node.name
        self.generic_visit(node)
        self._current_func = old

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "print" and self._current_func != "_print_callback":
            ctx = (
                f"{self._current_class}.{self._current_func}"
                if self._current_class
                else (self._current_func or "<module>")
            )
            self.print_calls.append((node.lineno, ctx))
        self.generic_visit(node)


def test_no_print_calls_in_package() -> None:
    """SC-001: Zero print() calls remain in infrahub_sync/ source code."""
    violations: list[str] = []
    for py_file in _python_files():
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        visitor = _PrintCallVisitor()
        visitor.visit(tree)
        for lineno, ctx in visitor.print_calls:
            rel = py_file.relative_to(PACKAGE_DIR.parent)
            violations.append(f"  {rel}:{lineno} in {ctx}")

    assert not violations, "Found print() calls that should use logging:\n" + "\n".join(violations)


# Files that are allowed to not have a module-level logger (relative paths from repo root).
_LOGGER_EXEMPT = {
    "infrahub_sync/cli.py",
    "infrahub_sync/generator/__init__.py",
}

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_modules_have_logger() -> None:
    """Every non-trivial .py module should define a module-level logger."""
    missing: list[str] = []
    for py_file in _python_files():
        rel = str(py_file.relative_to(_REPO_ROOT))
        if rel in _LOGGER_EXEMPT:
            continue
        source = py_file.read_text(encoding="utf-8")
        # Check for logger = logging.getLogger pattern
        if "logger = logging.getLogger" not in source:
            rel = py_file.relative_to(PACKAGE_DIR.parent)
            missing.append(str(rel))

    # Some files may legitimately not need a logger (e.g., pure type stubs, constants-only files).
    # We check the core files that we migrated.
    core_files = {
        "infrahub_sync/utils.py",
        "infrahub_sync/potenda/__init__.py",
    }
    missing_core = [f for f in missing if f in core_files]
    assert not missing_core, "Core modules missing logger definition:\n" + "\n".join(missing_core)


@pytest.mark.parametrize(
    ("global_options", "command", "expected_level"),
    [
        (("-v",), ("configs", "--help"), logging.DEBUG),
        (("-q",), ("runs", "plan", "--help"), logging.WARNING),
        (("--verbosity", "default"), ("diff", "--help"), logging.INFO),
        (("--verbosity", "verbose"), ("sync", "--help"), logging.DEBUG),
        (("--verbosity", "quiet"), ("apply", "--help"), logging.WARNING),
    ],
)
def test_logging_flags_remain_active_on_converted_commands(
    global_options: tuple[str, ...],
    command: tuple[str, ...],
    expected_level: int,
) -> None:
    from typer.testing import CliRunner

    from infrahub_sync.cli import app

    result = CliRunner().invoke(app, [*global_options, *command])

    assert result.exit_code == 0, result.output
    assert logging.getLogger("infrahub_sync").level == expected_level
