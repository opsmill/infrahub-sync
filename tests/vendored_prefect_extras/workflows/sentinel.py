"""Fixture module that explodes on import -- the import-isolation proof.

Catalogue construction, key lookup, iteration, key derivation, and
``to_deployment_input()`` must never import a workflow implementation module. A
definition pointing at ``tests.workflows.sentinel`` turns that guarantee into an
executable proof: if any of those operations resolved the reference, the
``RuntimeError`` below would surface immediately, so a passing test is itself
evidence the module stayed unimported. Tests pair this with an explicit
``"tests.workflows.sentinel" not in sys.modules`` assertion.

The explicit resolution paths -- ``WorkflowDefinition.load()`` and
``validate_definitions`` -- are expected to trip it: ``load()`` propagates this
very ``RuntimeError`` unwrapped, and validation maps it to a
module-import failure message.

Assertions MUST match on the ``RuntimeError`` below, never merely on "some
exception": a ``ModuleNotFoundError`` here would mean the repo root is missing
from ``sys.path`` (see ``conftest.py``) and would let a test pass vacuously.

Named ``sentinel.py`` rather than ``test_*.py`` so pytest's default collection
never imports it.
"""

raise RuntimeError("sentinel module must never be imported by the catalogue")
