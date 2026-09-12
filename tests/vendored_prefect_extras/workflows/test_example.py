"""The documented example, executed exactly as it is written.

A documented example that no longer runs is a defect, so the example is not
retyped here. Every module of the tour is parsed straight out of
``opsmill_prefect_extras.workflows.__doc__``, written to disk verbatim, and
imported -- the text in the docstring *is* the code under test, so the two
cannot drift apart: a docstring edit that breaks the tour fails this file, and
an API change that breaks the tour fails it too.

Each documented module is introduced by a comment naming its path (e.g.
``# example_app/inventory/workflows.py``), and that marker delimits the blocks.
Renaming or removing a marker changes what this file executes, which
:func:`test_the_example_documents_every_module_of_the_tour` is there to catch.

Two mechanics worth knowing:

* ``example_app`` needs no ``__init__.py`` files -- it imports as an implicit
  namespace package -- so nothing is written that the example does not show.
* The example's own test module is loaded from its file rather than imported by
  its documented dotted path, because this repository already has a ``tests``
  package of its own on ``sys.path``.

Everything runs offline: constructing Prefect ``Flow`` objects and validating
deployment input are local operations.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from ast import literal_eval
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from prefect.flows import Flow

import opsmill_prefect_extras.workflows as workflows_package

EXAMPLE_MODULES = (
    "example_app/inventory/flows.py",
    "example_app/inventory/workflows.py",
    "example_app/reports/flows.py",
    "example_app/reports/workflows.py",
    "example_app/composition.py",
    "tests/test_workflow_catalogue.py",
)
"""Every module the tour documents, in the order the docstring presents them."""

FLOW_MODULES = ("example_app.inventory.flows", "example_app.reports.flows")
"""The example's flow modules -- the ones nothing but resolution may import."""

CONSUMER_TEST_MODULE = "tests/test_workflow_catalogue.py"
"""The documented path of the example's own CI check."""

EXPECTED_PAYLOAD = {
    "name": "scheduled",
    "tags": ["inventory"],
    "schedules": [{"schedule": {"cron": "0 2 * * *"}}],
    "concurrency_limit": 1,
    "concurrency_options": {"collision_strategy": "CANCEL_NEW"},
}
"""The rendering the tour promises -- asserted independently of the docstring."""

_MARKER = re.compile(r"^(?P<indent> +)# (?P<path>(?:example_app|tests)/[\w/]+\.py)$")
_PAYLOAD_LINE = "payload = definition.to_deployment_input()"


def _documented_modules() -> dict[str, str]:
    """Parse the docstring's example into ``documented path -> module source``.

    Returns:
        One entry per marked block, in docstring order, dedented to column
        zero and ready to write to disk.
    """
    docstring = workflows_package.__doc__
    assert docstring is not None, "the example lives in the package docstring"

    sources: dict[str, str] = {}
    lines = docstring.splitlines()
    index = 0
    while index < len(lines):
        marker = _MARKER.match(lines[index])
        if marker is None:
            index += 1
            continue
        indent = len(marker["indent"])
        body = [lines[index][indent:]]
        index += 1
        while index < len(lines):
            line = lines[index]
            if _MARKER.match(line):
                break
            if line.strip() and len(line) - len(line.lstrip()) < indent:
                break
            body.append(line[indent:] if line.strip() else "")
            index += 1
        sources[marker["path"]] = "\n".join(body).strip("\n") + "\n"
    return sources


def _documented_payload() -> object:
    """Read back the rendered payload the docstring claims, as an object.

    The tour shows the payload as a commented dict literal directly under the
    ``to_deployment_input()`` call, so the claim can be compared against what
    rendering actually produces instead of being taken on trust.

    Returns:
        The commented dict literal, evaluated.
    """
    lines = _documented_modules()["example_app/composition.py"].splitlines()
    commented = []
    for line in lines[lines.index(_PAYLOAD_LINE) + 1 :]:
        if not line.startswith("#"):
            break
        commented.append(line[1:].strip())
    return literal_eval(" ".join(commented))


def _load_consumer_test_module(root: Path) -> ModuleType:
    """Load the example's own test module from its file.

    Args:
        root: Directory the documented modules were written into.

    Returns:
        The executed module, with its test function ready to call.
    """
    path = root / CONSUMER_TEST_MODULE
    spec = importlib.util.spec_from_file_location("example_app_consumer_tests", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def example_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Write the documented modules out and make ``example_app`` importable.

    Yields:
        The directory holding the example, laid out at the documented paths.
    """
    for path, source in _documented_modules().items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path

    for name in [
        name
        for name in sys.modules
        if name == "example_app"
        or name.startswith(("example_app.", "example_app_consumer_tests"))
    ]:
        del sys.modules[name]


def test_the_example_documents_every_module_of_the_tour() -> None:
    assert tuple(_documented_modules()) == EXAMPLE_MODULES


def test_declaring_definitions_imports_no_flow_module(example_app: Path) -> None:
    for name in FLOW_MODULES:
        assert name not in sys.modules

    inventory = importlib.import_module("example_app.inventory.workflows")

    assert [definition.key for definition in inventory.INVENTORY_WORKFLOWS] == [
        "inventory-refresh/scheduled"
    ]
    assert inventory.INVENTORY_REFRESH.module == "example_app.inventory.flows"
    for name in FLOW_MODULES:
        assert name not in sys.modules


def test_the_composition_root_composes_both_groups(example_app: Path) -> None:
    for name in FLOW_MODULES:
        assert name not in sys.modules

    composition = importlib.import_module("example_app.composition")

    assert composition.CATALOGUE.keys() == (
        "inventory-refresh/scheduled",
        "reports/nightly",
    )
    assert len(composition.CATALOGUE) == 2
    for name in FLOW_MODULES:
        assert name not in sys.modules


def test_lookup_returns_the_split_identity_definition(example_app: Path) -> None:
    composition = importlib.import_module("example_app.composition")

    definition = composition.definition

    assert definition is composition.CATALOGUE["inventory-refresh/scheduled"]
    assert definition.flow_name == "inventory-refresh"
    assert definition.deployment_name == "scheduled"
    assert definition.key == "inventory-refresh/scheduled"


def test_the_documented_payload_is_what_rendering_produces(example_app: Path) -> None:
    composition = importlib.import_module("example_app.composition")

    payload = composition.payload

    assert payload == EXPECTED_PAYLOAD
    assert payload == _documented_payload()
    assert "entrypoint" not in payload


def test_the_documented_definition_loads_its_real_prefect_flow(
    example_app: Path,
) -> None:
    composition = importlib.import_module("example_app.composition")

    resolved = composition.definition.load()

    assert isinstance(resolved, Flow)
    assert resolved.name == "inventory-refresh"


def test_the_shipped_ci_check_passes_and_resolves_every_flow(
    example_app: Path,
) -> None:
    """The one-import CI check runs green -- and provably did the resolving.

    Asserting the flow modules are in ``sys.modules`` afterwards is what stops
    this passing vacuously: a check that resolved nothing would leave them
    absent, exactly as the composition tests above require.
    """
    for name in FLOW_MODULES:
        assert name not in sys.modules
    consumer_tests = _load_consumer_test_module(example_app)

    consumer_tests.test_workflow_catalogue_resolves()

    for name in FLOW_MODULES:
        assert name in sys.modules
