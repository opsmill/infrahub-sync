"""The vendored prefect-extras package must be all-in or all-out.

A half-executed re-adoption — vendored directory removed but the wheel entry or
the restored Git dependency forgotten, or vice versa — would ship a broken
package. These assertions pin the three coupled facts together; see
``opsmill_prefect_extras/VENDORED.md`` for the freeze and re-adoption rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Stdlib only on Python 3.11+; the 3.10 test leg skips this module, and every
# 3.11+ leg enforces it.
tomllib = pytest.importorskip("tomllib")

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_DIR = REPO_ROOT / "opsmill_prefect_extras"
VENDORED_TESTS_DIR = REPO_ROOT / "tests" / "vendored_prefect_extras"


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_vendored_package_state_is_consistent() -> None:
    data = _pyproject()
    vendored = VENDORED_DIR.is_dir()

    wheel_packages = data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    managed_deps = data["project"]["optional-dependencies"]["managed"]
    has_git_dep = any("prefect-extras.git" in dep for dep in managed_deps)

    if vendored:
        assert "opsmill_prefect_extras" in wheel_packages, "vendored dir present but not shipped in the wheel"
        assert not has_git_dep, "vendored dir present alongside the upstream Git dependency"
        assert (VENDORED_DIR / "VENDORED.md").is_file(), "vendored dir must carry its freeze/re-adoption record"
        assert VENDORED_TESTS_DIR.is_dir(), "vendored package present but its upstream test suite is missing"
    else:
        assert "opsmill_prefect_extras" not in wheel_packages, "wheel still ships a removed vendored package"
        assert has_git_dep, "vendored dir removed but the upstream dependency was not restored"
        assert not VENDORED_TESTS_DIR.exists(), "vendored tests remain after the package was re-adopted"


def test_managed_storage_drivers_are_not_base_dependencies() -> None:
    """Only the managed profile carries PostgreSQL and S3 client dependencies."""
    data = _pyproject()
    base_dependencies = data["project"]["dependencies"]
    managed_dependencies = data["project"]["optional-dependencies"]["managed"]

    for package in ("boto3", "psycopg"):
        assert not any(dependency.lower().startswith(package) for dependency in base_dependencies)
        assert any(dependency.lower().startswith(package) for dependency in managed_dependencies)
