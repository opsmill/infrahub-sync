"""Pytest configuration for the workflow-catalogue feature tests.

The fixture modules in this package are referenced *by name* from inside the
objects under test: a ``WorkflowDefinition`` stores its target as the dotted
module path ``tests.workflows.sentinel`` / ``tests.workflows.flows`` and
resolves it with ``importlib.import_module``. The repository has no
``tests/__init__.py`` and no pytest ``pythonpath`` configuration, so those
dotted paths resolve only while the repository root is on ``sys.path`` (with
the root present, ``tests`` resolves as an implicit namespace package and
``tests.workflows`` as this package). Prepending the root here guarantees the
references import regardless of how pytest is invoked -- from the repo root,
from a subdirectory, or against a single test file.

Note for test authors: keep ``ModuleNotFoundError`` and the sentinel's own
``RuntimeError`` distinct in assertions. A ``ModuleNotFoundError`` means this
path setup is broken (a test-infrastructure bug); the ``RuntimeError`` is the
behaviour under test. Asserting only "an exception was raised" would let an
import-isolation or module-import-failure test pass vacuously because the
fixture simply failed to import.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
