"""The deployment page reaches readers and states what an operator cannot guess.

None of these links is covered by the documentation build: Docusaurus only warns
about a broken Markdown link, and a page listed nowhere still builds cleanly
while nobody can find it. The rest is the operator contract — the commands, the
three states, the refusal families, and the two immutable image forms — where a
value the page and the bundle disagree about is a copy-and-paste failure.
"""

from __future__ import annotations

import ast
from typing import Any, get_type_hints

import pytest
from pydantic import BaseModel

from infrahub_sync.client import SyncClient
from tests.compose.conftest import BUNDLE, REPO_ROOT

DOCUMENT_ID = "compose-deployment"
PAGE = REPO_ROOT / "docs" / "docs" / f"{DOCUMENT_ID}.mdx"
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"
ENTRY_POINT = BUNDLE / "infrahub-sync-compose"
API_REFERENCE = REPO_ROOT / "docs" / "docs" / "reference" / "sync-http-api.mdx"

# Every command the entry point answers to. A command nobody wrote down is one
# the deployment appears not to have.
COMMANDS = ("init", "preflight", "start", "status", "logs", "stop", "restart", "reset")

# The three lifecycle states and the exit code each one carries, so a reader can
# script against them.
STATES = (("READY", "0"), ("DEGRADED", "3"), ("STOPPED", "4"))

# What preflight refuses. Each is a decision an operator has to act on, and the
# page is where they find out what the family name meant.
REFUSAL_FAMILIES = (
    "compose-too-old",
    "credentials-missing",
    "image-not-immutable",
    "port-occupied",
    "port-unprovable",
    "foreign-resource",
)

# The files the bundle ships or generates. An operator who does not know which
# ones hold credentials cannot keep them out of a backup or a commit.
BUNDLE_FILES = (
    "compose.yaml",
    "infrahub-sync-compose",
    "defaults.conf",
    "configuration/qualification.yaml",
    "bootstrap/databases.sh",
    "operator.env",
    "secrets/postgres-admin-password",
    ".instance",
)

DEVELOPER_TASKS = ("compose.contract", "compose.lifecycle", "compose.reclaim")


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


@pytest.mark.parametrize("command", COMMANDS)
def test_the_page_documents_every_lifecycle_command(command: str) -> None:
    """`--help` names them; this page is where their consequences are written down."""
    assert f"infrahub-sync-compose {command}" in page()


def test_the_page_documents_no_command_the_entry_point_does_not_have() -> None:
    """A documented command that refuses is worse than an undocumented one."""
    usage = ENTRY_POINT.read_text(encoding="utf-8")
    documented = {command for command in COMMANDS if f"infrahub-sync-compose {command}" in page()}

    for command in documented:
        assert f"    {command})" in usage or f"    {command} " in usage, command


@pytest.mark.parametrize(("state", "exit_code"), STATES)
def test_the_page_documents_every_state_and_its_exit_code(state: str, exit_code: str) -> None:
    """A smoke scripts against the exit code, so the mapping has to be written down."""
    rows = [line for line in page().splitlines() if line.startswith(f"| `{state}` |")]

    assert rows, f"{state} has no row on the page"
    assert f"| {exit_code} |" in rows[0], rows[0]


@pytest.mark.parametrize("family", REFUSAL_FAMILIES)
def test_the_page_names_every_refusal_family_it_documents(family: str) -> None:
    """The family is all a refusal prints, so it has to mean something to a reader."""
    assert family in page()
    assert family in ENTRY_POINT.read_text(encoding="utf-8"), f"{family} is not a family the bundle reports"


@pytest.mark.parametrize("name", BUNDLE_FILES)
def test_the_page_documents_every_file_the_bundle_ships_or_generates(name: str) -> None:
    """Which files hold credentials is not something to leave a reader to infer."""
    assert f"`{name}`" in page()


@pytest.mark.parametrize("task_name", DEVELOPER_TASKS)
def test_the_page_documents_every_bundle_task(task_name: str) -> None:
    """`invoke --list` names them; the page says what each one needs first."""
    assert f"invoke {task_name}" in page()


def test_the_page_states_the_minimum_compose_version_the_bundle_enforces() -> None:
    """A reader who installs the version below the floor is refused at preflight."""
    minimum = next(
        line.split("=", 1)[1].strip()
        for line in ENTRY_POINT.read_text(encoding="utf-8").splitlines()
        if line.startswith("MINIMUM_COMPOSE=")
    )

    assert minimum in page(), minimum


def test_the_page_documents_both_immutable_image_forms() -> None:
    """One is what a local candidate looks like; the other is what a published one does."""
    text = page()

    assert "INFRAHUB_SYNC_IMAGE=sha256:" in text
    assert "@sha256:" in text


def test_the_page_says_this_is_not_yet_the_clean_host_release_claim() -> None:
    """The bundle is qualified from a checkout; saying otherwise would overclaim."""
    assert "clean-host release claim" in page()


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
