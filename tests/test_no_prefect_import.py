"""The base package must never import Prefect (DBA-001, SC-006, DBR-010).

The `sys.modules` half runs in a FRESH interpreter on purpose: this suite is
collected with the `prefect` extra installed, and collecting
`tests/orchestration/test_flow.py` imports Prefect into the worker process, so an
in-process assertion would be measuring pytest's own imports rather than the
package's. The static import-graph half is collection-safe and stays in-process.
"""

from __future__ import annotations

import ast
import subprocess  # noqa: S404 - fixed argv probe, the point of the test
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "infrahub_sync"
EXAMPLES_DIR = REPO_ROOT / "examples"

PROBE_SCRIPT = f"""
import sys

import infrahub_sync
import infrahub_sync.cli
import infrahub_sync.execution
from typer.testing import CliRunner

runner = CliRunner()
help_result = runner.invoke(infrahub_sync.cli.app, ["--help"])
assert help_result.exit_code == 0, help_result.output
list_result = runner.invoke(
    infrahub_sync.cli.app, ["list", "--directory", {str(EXAMPLES_DIR)!r}]
)
assert list_result.exit_code == 0, list_result.output

leaked = sorted(m for m in sys.modules if m == "prefect" or m.startswith("prefect."))
assert not leaked, f"prefect modules imported by the base package: {{leaked}}"
print("NO-PREFECT-IMPORT-OK")
"""


def _module_paths() -> list[Path]:
    """Every base-package module — the whole package except `orchestration/`."""
    return [
        path
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        if "orchestration" not in path.relative_to(PACKAGE_ROOT).parts
    ]


def _imported_names(path: Path) -> set[str]:
    """Return every module name `path` imports, dotted form, from static analysis."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_base_package_imports_and_runs_without_prefect_in_a_fresh_interpreter() -> None:
    """Importing the package and running CLI sanity must not pull in Prefect."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, this interpreter, no shell
        [sys.executable, "-c", PROBE_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "NO-PREFECT-IMPORT-OK" in completed.stdout


def test_execution_surface_imports_no_prefect_and_no_orchestration() -> None:
    """The shared surface is import-light: no Prefect, no orchestration package."""
    imported = _imported_names(PACKAGE_ROOT / "execution.py")
    assert not [name for name in imported if name == "prefect" or name.startswith("prefect.")]
    assert not [name for name in imported if name.startswith("infrahub_sync.orchestration")]


def test_no_base_package_module_imports_the_orchestration_package() -> None:
    """Nothing outside `orchestration/` may reach into it — that is what keeps the
    base install Prefect-free even though the extra ships in the same package."""
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(
            name
            for name in _imported_names(path)
            if name == "prefect" or name.startswith(("prefect.", "infrahub_sync.orchestration"))
        )
        for path in _module_paths()
    }
    assert not {path: names for path, names in offenders.items() if names}
