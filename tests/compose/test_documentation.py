"""The deployment page reaches readers, and what it shows resolves against the product.

None of these links is covered by the documentation build: Docusaurus only warns
about a broken Markdown link, and a page listed nowhere still builds cleanly
while nobody can find it. The rest is the part of the operator contract that a
copy-and-paste would break: the CLI calls an operator makes through the `cli`
service, and the Python client example.
"""

from __future__ import annotations

import ast
import shlex
from typing import Any, get_type_hints

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from infrahub_sync.client import SyncClient
from tests.compose.conftest import REPO_ROOT

DOCUMENT_ID = "compose-deployment"
PAGE = REPO_ROOT / "docs" / "docs" / f"{DOCUMENT_ID}.mdx"
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"
API_REFERENCE = REPO_ROOT / "docs" / "docs" / "reference" / "sync-http-api.mdx"

# The CLI calls of the reviewed-run procedure, as an operator passes them to
# `docker compose run --rm cli ...`. Each has to be a call the shipped CLI has.
CLI_CALLS = (
    "configs list",
    "configs register /input/package.yaml --reason 'register my configuration'",
    "configs show CONFIG_ID",
    "configs versions CONFIG_ID",
    "configs validate CONFIG_ID 1",
    "diff --config-id CONFIG_ID --version 1 --branch BRANCH_NAME --reason 'review initial sync'",
    "runs plan RUN_ID --detail",
    "apply RUN_ID --expected-checksum CHECKSUM --branch BRANCH_NAME --reason 'apply reviewed initial sync'",
    "runs show RUN_ID",
    "runs results RUN_ID",
    "diff --config-id CONFIG_ID --version 1 --branch BRANCH_NAME --reason 'verify unchanged source'",
    "configs version CONFIG_ID /input/package.yaml --reason 'register edited configuration'",
)


def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def sync_sidebar() -> str:
    """Return the text of the `syncSidebar` array alone.

    Scoping matters: the document id appearing anywhere else in the file would
    satisfy a whole-file search while the rendered navigation still omits it.
    """
    text = SIDEBAR.read_text(encoding="utf-8")
    start = text.index("[", text.index("syncSidebar:"))
    depth = 0
    for offset in range(start, len(text)):
        if text[offset] == "[":
            depth += 1
        elif text[offset] == "]":
            depth -= 1
            if depth == 0:
                return text[start : offset + 1]
    msg = f"syncSidebar in {SIDEBAR} is not a closed array"
    raise AssertionError(msg)


def test_the_documented_page_path_is_the_one_the_tests_read() -> None:
    """Guards every check below against silently reading a missing file."""
    assert PAGE.is_file(), PAGE
    assert page().startswith("---\ntitle: Compose deployment\n---")


def test_the_page_is_listed_in_the_docs_sidebar() -> None:
    """Docusaurus renders no navigation entry for a page the sidebar omits."""
    assert f"'{DOCUMENT_ID}'" in sync_sidebar()


@pytest.mark.parametrize("call", CLI_CALLS)
def test_every_documented_cli_call_names_commands_the_cli_has(call: str) -> None:
    """A documented call the CLI refuses is a procedure that stops at that step.

    Resolved against the real Typer application, so a renamed command or a
    dropped option fails here rather than during an operator's first run.
    """
    from infrahub_sync.cli import app

    result = CliRunner().invoke(app, [*shlex.split(call), "--help"], env={"NO_COLOR": "1", "COLUMNS": "200"})

    assert result.exit_code == 0, f"{call}: {result.output}"


def test_the_api_reference_marks_the_configuration_directory_as_legacy_only() -> None:
    """A registered worker reads declared configuration from PostgreSQL.

    Left as a requirement, the reference would tell every deployed worker to
    mount a directory nothing opens — which is the shared filesystem this
    topology exists to remove.
    """
    row = next(
        line
        for line in API_REFERENCE.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `INFRAHUB_SYNC_CONFIG_DIRECTORY` |")
    )

    assert "Legacy" in row, row
    assert "no configuration mount" in row, row


# ---------------------------------------------------------------------------
# The Python example resolves against the real client
# ---------------------------------------------------------------------------


def python_blocks(text: str) -> list[str]:
    """Return every fenced ```python block on the page."""
    blocks: list[str] = []
    collecting: list[str] | None = None
    for line in text.splitlines():
        if line.strip() == "```python":
            collecting = []
        elif line.strip() == "```" and collecting is not None:
            blocks.append("\n".join(collecting))
            collecting = None
        elif collecting is not None:
            collecting.append(line)
    return blocks


def client_chains(source: str) -> list[tuple[tuple[str, bool], ...]]:
    """Return every attribute chain the example takes from a `SyncClient` binding.

    Each step is the attribute name and whether the example calls it, because
    `get_status` and `get_status()` resolve to different things.
    """
    tree = ast.parse(source)
    bound = {
        item.optional_vars.id
        for statement in ast.walk(tree)
        if isinstance(statement, ast.With)
        for item in statement.items
        if isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Name)
        and item.context_expr.func.id == "SyncClient"
        and isinstance(item.optional_vars, ast.Name)
    }
    chains: list[tuple[tuple[str, bool], ...]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        steps: list[tuple[str, bool]] = []
        current: ast.expr = node
        while isinstance(current, (ast.Attribute, ast.Call)):
            if isinstance(current, ast.Call):
                if not isinstance(current.func, ast.Attribute):
                    break
                current = current.func
                steps.append((current.attr, True))
            else:
                steps.append((current.attr, False))
            current = current.value
        if isinstance(current, ast.Name) and current.id in bound:
            chains.append(tuple(reversed(steps)))

    # Walking the tree yields every prefix of a chain as its own node, so only the
    # longest one of each is kept. Prefixes are compared by name: the same
    # attribute appears once called and once not.
    def names(chain: tuple[tuple[str, bool], ...]) -> tuple[str, ...]:
        return tuple(name for name, _called in chain)

    return [
        chain
        for chain in chains
        if not any(names(other)[: len(chain)] == names(chain) and len(other) > len(chain) for other in chains)
    ]


def resolve_step(owner: Any, name: str, *, called: bool) -> Any:  # noqa: ANN401 -- it walks real annotations
    """Return what one documented step resolves to, or fail naming what broke."""
    if isinstance(owner, type) and issubclass(owner, BaseModel):
        assert name in owner.model_fields, f"{owner.__name__} has no field {name!r}"
        return owner.model_fields[name].annotation
    member = getattr(owner, name, None)
    assert member is not None, f"{owner.__name__} has no attribute {name!r}"
    if not called:
        return member
    return get_type_hints(member)["return"]


@pytest.mark.parametrize("chain", client_chains("\n\n".join(python_blocks(page()))), ids=str)
def test_every_documented_client_chain_resolves_against_the_real_client(chain: tuple[tuple[str, bool], ...]) -> None:
    """A method or field the page names has to be one the client actually has.

    Checking that the page merely mentions `SyncClient` would pass for an example
    calling a method nobody wrote. This walks the chain the reader would type,
    one link at a time, against the real class and the real resource models.
    """
    resolved: Any = SyncClient
    for name, called in chain:
        resolved = resolve_step(resolved, name, called=called)


def test_the_page_takes_at_least_one_chain_from_the_client() -> None:
    """Guards the parametrised check above against silently covering nothing."""
    assert client_chains("\n\n".join(python_blocks(page()))) != []
