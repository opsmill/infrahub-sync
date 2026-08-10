"""The base package must never import optional Prefect profiles (DBA-001, SC-006, DBR-010).

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

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "infrahub_sync"
EXAMPLES_DIR = REPO_ROOT / "examples"
OPTIONAL_PACKAGE_NAMES = frozenset({"managed", "orchestration"})
OPTIONAL_PACKAGE_PREFIXES = tuple(f"infrahub_sync.{name}" for name in sorted(OPTIONAL_PACKAGE_NAMES))
OPTIONAL_DISTRIBUTION_NAMES = frozenset({"fastapi", "opsmill_prefect_extras", "prefect", "uvicorn"})

PROBE_SCRIPT = f"""
import sys


class BlockOptionalImport:
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.partition(".")[0]
        if root in {sorted(OPTIONAL_DISTRIBUTION_NAMES)!r}:
            raise ModuleNotFoundError(f"{{root}} is deliberately unavailable in this base-package probe")
        return None


sys.meta_path.insert(0, BlockOptionalImport())

import infrahub_sync
import infrahub_sync.api.v1
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

optional_roots = {sorted(OPTIONAL_DISTRIBUTION_NAMES)!r}
leaked = sorted(m for m in sys.modules if m.partition(".")[0] in optional_roots)
assert not leaked, f"optional managed modules imported by the base package: {{leaked}}"
print("NO-OPTIONAL-MANAGED-IMPORT-OK")
"""


def _module_paths() -> list[Path]:
    """Every base-package module, excluding optional runtime profiles."""
    return [
        path
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        if not OPTIONAL_PACKAGE_NAMES.intersection(path.relative_to(PACKAGE_ROOT).parts)
    ]


def _imported_names(path: Path, *, root: Path = REPO_ROOT) -> set[str]:
    """Return every module name `path` imports, dotted form, from static analysis.

    RELATIVE imports are resolved against `path`'s own package, so
    `from .orchestration import flow` yields the same
    `infrahub_sync.orchestration.flow` an absolute import would. Skipping them
    would leave the boundary test blind to the most natural in-package form —
    and the package already uses relative imports in `adapters/`.

    `root` is the directory the dotted path is measured from; tests override it.
    """
    parts = path.relative_to(root).with_suffix("").parts
    # `path`'s package: for `pkg/mod.py` and for `pkg/__init__.py` alike this is
    # `pkg`, because a relative import in either resolves against `pkg`.
    package = parts[:-1]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # One leading dot is `package` itself; each extra dot climbs one level.
                base = package[: max(len(package) - (node.level - 1), 0)]
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            if not prefix:
                continue
            names.add(prefix)
            names.update(f"{prefix}.{alias.name}" for alias in node.names)
    return names


def test_base_package_imports_and_runs_without_managed_dependencies_in_a_fresh_interpreter() -> None:
    """Base imports and CLI sanity must not pull in managed dependencies."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, this interpreter, no shell
        [sys.executable, "-c", PROBE_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "NO-OPTIONAL-MANAGED-IMPORT-OK" in completed.stdout


def test_execution_surface_imports_no_optional_managed_runtime() -> None:
    """The shared surface imports no optional runtime distribution or package."""
    imported = _imported_names(PACKAGE_ROOT / "execution.py")
    assert not [name for name in imported if name.partition(".")[0] in OPTIONAL_DISTRIBUTION_NAMES]
    assert not [name for name in imported if name.startswith(OPTIONAL_PACKAGE_PREFIXES)]


def test_no_base_package_module_imports_an_optional_runtime_package() -> None:
    """Base modules must not reach into optional runtime profiles."""
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(
            name
            for name in _imported_names(path)
            if name.partition(".")[0] in OPTIONAL_DISTRIBUTION_NAMES or name.startswith(OPTIONAL_PACKAGE_PREFIXES)
        )
        for path in _module_paths()
    }
    assert not {path: names for path, names in offenders.items() if names}


@pytest.mark.parametrize(
    ("module_path", "source"),
    [
        ("infrahub_sync/probe.py", "from .orchestration import flow\n"),
        ("infrahub_sync/probe.py", "from . import orchestration\n"),
        ("infrahub_sync/adapters/probe.py", "from ..orchestration import flow\n"),
        ("infrahub_sync/adapters/__init__.py", "from ..orchestration import flow\n"),
        ("infrahub_sync/probe.py", "from .managed import app\n"),
        ("infrahub_sync/adapters/probe.py", "from ..managed import app\n"),
    ],
)
def test_the_scan_resolves_relative_imports_of_optional_runtime_packages(
    tmp_path: Path, module_path: str, source: str
) -> None:
    """The boundary is only enforced if the scan sees the in-package import forms.

    Each of these reaches `infrahub_sync.orchestration` exactly as the absolute
    import does. A scan that skipped `ImportFrom` nodes carrying a level would
    report no name at all and pass the boundary test on a real violation.
    """
    probe = tmp_path / module_path
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(source, encoding="utf-8")

    names = _imported_names(probe, root=tmp_path)

    assert [name for name in names if name.startswith(OPTIONAL_PACKAGE_PREFIXES)]
