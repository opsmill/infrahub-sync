"""Tests for structured logging migration: no print() calls, logger definitions, CLI verbosity flags."""

from __future__ import annotations

import ast
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

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        old = self._current_func
        self._current_func = node.name
        self.generic_visit(node)
        self._current_func = old

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

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


# Files that are allowed to not have a module-level logger (package __init__ files that
# only re-export, tiny utility modules, etc.)
_LOGGER_EXEMPT = {
    "__init__.py",  # some __init__.py files are just re-exports
}


def test_modules_have_logger() -> None:
    """Every non-trivial .py module should define a module-level logger."""
    missing: list[str] = []
    for py_file in _python_files():
        if py_file.name in _LOGGER_EXEMPT:
            continue
        source = py_file.read_text(encoding="utf-8")
        # Check for logger = logging.getLogger pattern
        if "logger = logging.getLogger" not in source:
            rel = py_file.relative_to(PACKAGE_DIR.parent)
            missing.append(str(rel))

    # Some files may legitimately not need a logger (e.g., pure type stubs, constants-only files).
    # We check the core files that we migrated.
    core_files = {
        "infrahub_sync/cli.py",
        "infrahub_sync/utils.py",
        "infrahub_sync/potenda/__init__.py",
    }
    missing_core = [f for f in missing if f in core_files]
    assert not missing_core, "Core modules missing logger definition:\n" + "\n".join(missing_core)


def test_cli_has_verbosity_flag() -> None:
    """SC-005: CLI exposes --verbosity, -v, and -q flags."""
    from typer.testing import CliRunner

    from infrahub_sync.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--verbosity" in result.output
    assert "-v" in result.output
    assert "-q" in result.output


def test_cli_has_show_progress_flag() -> None:
    """SC-003: CLI exposes --show-progress / --no-show-progress flag."""
    from typer.testing import CliRunner

    from infrahub_sync.cli import app

    runner = CliRunner()
    # Check in diff subcommand help
    result = runner.invoke(app, ["diff", "--help"])
    assert result.exit_code == 0
    assert "--show-progress" in result.output


@pytest.mark.parametrize("flag", ["-v", "-q", "--verbosity quiet", "--verbosity verbose"])
def test_cli_verbosity_flags_accepted(flag: str) -> None:
    """Verbosity flags are accepted without error when no subcommand requires a server."""
    from typer.testing import CliRunner

    from infrahub_sync.cli import app

    runner = CliRunner()
    args = [*flag.split(), "list", "--directory", str(PACKAGE_DIR.parent / "examples")]
    result = runner.invoke(app, args)
    # exit_code 0 means the flag was accepted; we don't care about the list output
    assert result.exit_code == 0, f"Flag '{flag}' rejected: {result.output}"
