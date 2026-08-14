"""Local harness for the byte-identical vendored upstream test suite.

Three adaptations, all confined to this file so no vendored test changes:

1. Skip the whole directory when the prefect extra is absent — the vendored
   package imports prefect at module scope, matching `tests/managed/` guards.
2. Alias the `tests.workflows` package to the vendored location in
   `sys.modules`. Upstream fixture definitions reference their flow modules by
   the dotted paths `tests.workflows.flows` / `tests.workflows.sentinel` and
   resolve them with `importlib.import_module`; in upstream those resolve from
   the repository root, while here the package lives at
   `tests.vendored_prefect_extras.workflows`. Only the package is aliased —
   submodules resolve through its `__path__` on demand, which matters because
   the sentinel module raises on import by design and must not be imported here.
3. Skip the one test that reads the upstream repository's root `README.md`
   (`test_executor_docs_disclose_local_engine_limitations`) — that file is
   upstream project documentation and is deliberately not vendored.
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("prefect", reason="vendored prefect-extras tests require the prefect extra")

from tests.vendored_prefect_extras import workflows as _vendored_workflows  # noqa: E402

_REAL_WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "workflows"
if _REAL_WORKFLOWS_DIR.exists():
    msg = (
        "tests/workflows exists as a real package; the vendored suite aliases that "
        "module name and the two would shadow each other — rename one of them"
    )
    raise RuntimeError(msg)

sys.modules["tests.workflows"] = _vendored_workflows

_UPSTREAM_DOC_TESTS = frozenset({"test_executor_docs_disclose_local_engine_limitations"})


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip vendored tests that assert upstream repository documentation."""
    marker = pytest.mark.skip(reason="asserts the upstream repository's README.md, which is not vendored")
    for item in items:
        if item.name in _UPSTREAM_DOC_TESTS and "vendored_prefect_extras" in str(item.path):
            item.add_marker(marker)
