"""The deployment page reaches readers, and what it shows resolves against the product.

None of these links is covered by the documentation build: Docusaurus only warns
about a broken Markdown link, and a page listed nowhere still builds cleanly
while nobody can find it. The rest is the part of the operator contract that a
copy-and-paste would break: the CLI calls an operator makes through the `cli`
service, and the Python client example.
"""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path
from typing import Any, get_type_hints

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from infrahub_sync.client import SyncClient
from tests.compose.conftest import REPO_ROOT

DOCUMENT_ID = "compose-deployment"
PAGE = REPO_ROOT / "docs" / "docs" / f"{DOCUMENT_ID}.mdx"
QUICKSTART = REPO_ROOT / "docs" / "docs" / "quickstart-compose.mdx"
TROUBLESHOOTING = REPO_ROOT / "docs" / "docs" / "operations" / "compose-troubleshooting.mdx"
SIDEBAR = REPO_ROOT / "docs" / "sidebars.ts"
API_REFERENCE = REPO_ROOT / "docs" / "docs" / "reference" / "sync-http-api.mdx"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# Every command of the removed `infrahub-sync-compose` wrapper. An operator who used
# one has to find its `docker compose` replacement on the page.
WRAPPER_COMMANDS = ("init", "preflight", "start", "status", "logs", "stop", "restart", "reset", "cli")
# How every CLI call of the operator sequence is run against the deployment.
CLI_PREFIX = "docker compose run --rm --no-deps -T"
# The opt-in suite, as the page tells a contributor to run it.
COMPOSE_SUITE_COMMAND = (
    "INFRAHUB_SYNC_DOCKER_IMAGE=infrahub-sync VERSION=compose-test uv run pytest -m compose tests/compose"
)

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


# ---------------------------------------------------------------------------
# The single-file deployment the page documents is the one the repository ships
# ---------------------------------------------------------------------------


def section(text: str, heading: str) -> str:
    """Return the body of one `## heading` section, up to the next `## ` heading."""
    start = text.index(f"\n## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def shell_lines(text: str) -> list[str]:
    """Return every line of the page's ```bash and ```sh blocks, stripped."""
    lines: list[str] = []
    collecting = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in {"```bash", "```sh"}:
            collecting = True
        elif stripped == "```":
            collecting = False
        elif collecting:
            lines.append(stripped)
    return lines


def compose_variables() -> set[str]:
    """Return every variable `docker-compose.yml` interpolates."""
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", COMPOSE_FILE.read_text(encoding="utf-8")))


def required_compose_variables() -> set[str]:
    """Return every variable `docker-compose.yml` guards with `:?`, so refuses to default."""
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", COMPOSE_FILE.read_text(encoding="utf-8")))


def test_the_compose_file_has_required_credentials_to_document() -> None:
    """Guards the credential checks below against a pattern that matches nothing."""
    assert len(required_compose_variables()) == 7, required_compose_variables()


@pytest.mark.parametrize("command", WRAPPER_COMMANDS)
def test_the_page_maps_every_removed_wrapper_command(command: str) -> None:
    """Each wrapper command an earlier alpha documented has a `docker compose` row."""
    rows = [line for line in section(page(), "Wrapper equivalents").splitlines() if line.startswith("| ")]

    row = next((row for row in rows if row.startswith(f"| `{command}` |")), None)

    assert row is not None, command
    assert "docker compose" in row or "`.env`" in row, row


def test_the_reset_equivalent_warns_that_it_deletes_the_data() -> None:
    """`down --volumes` asks for no confirmation, so the row itself has to say what it destroys."""
    row = next(line for line in section(page(), "Wrapper equivalents").splitlines() if line.startswith("| `reset` |"))

    assert "down --volumes" in row, row
    assert "deletes" in row, row


def test_the_page_runs_the_whole_operator_sequence_in_order_through_the_cli_service() -> None:
    """The reviewed-run procedure is the `CLI_CALLS` sequence, each run through `cli`.

    A step written for the removed wrapper, or out of order, is a procedure an operator
    cannot copy.
    """
    calls = [
        line.split(" cli ", 1)[1]
        for line in shell_lines(section(page(), "Prepare a configuration"))
        if line.startswith(CLI_PREFIX) and " cli " in line
    ]

    remaining = iter(calls)
    missing = [call for call in CLI_CALLS if not any(found == call for found in remaining)]
    assert missing == [], missing


def test_every_documented_cli_invocation_uses_the_cli_service() -> None:
    """No shell line on the page still calls the removed wrapper."""
    assert [line for line in shell_lines(page()) if "infrahub-sync-compose" in line] == []


@pytest.mark.parametrize("variable", sorted(required_compose_variables()))
def test_the_example_env_sets_every_required_credential(variable: str) -> None:
    """A copied example missing one credential stops Compose before anything starts."""
    example = section(page(), "The `.env` file")

    assert f"\n{variable}=" in example, variable


@pytest.mark.parametrize("variable", sorted(compose_variables()))
def test_the_page_documents_every_variable_the_compose_file_reads(variable: str) -> None:
    """A setting the file reads but the page never names is one an operator cannot find."""
    assert f"`{variable}`" in page() or f"\n{variable}=" in page(), variable


def test_the_page_shows_the_refusal_for_a_missing_credential() -> None:
    """The refusal names the variable, which is how an operator knows which one to add."""
    assert "is required" in section(page(), "The `.env` file")


def test_the_page_names_the_two_files_a_deployment_is_made_of() -> None:
    """The deployment is one Compose file and the `.env` an operator writes beside it."""
    assert "`docker-compose.yml`" in page()
    assert "`.env`" in page()


def test_the_page_gives_the_opt_in_suite_command() -> None:
    """The suite needs the image selected through the same two settings an operator uses."""
    assert COMPOSE_SUITE_COMMAND in page()


def test_the_page_states_the_minimum_compose_version_the_file_needs() -> None:
    """The version the page names is the one the file header names."""
    assert "Compose 2.24 or later" in COMPOSE_FILE.read_text(encoding="utf-8")
    assert "Compose 2.24 or later" in page()
    assert "2.17.3" not in page()


def test_the_page_selects_the_image_through_the_two_compose_settings() -> None:
    """The image is chosen by `VERSION` and `INFRAHUB_SYNC_DOCKER_IMAGE`, never by a binding file."""
    image_section = section(page(), "Choose an image")

    assert "image.bind" not in page()
    assert "`VERSION`" in image_section or "VERSION=" in image_section
    assert "INFRAHUB_SYNC_DOCKER_IMAGE" in image_section


@pytest.mark.parametrize("state", ["ready", "busy", "no-live-worker"])
def test_the_status_table_documents_every_worker_state(state: str) -> None:
    """`/status` is the only readiness signal now that the wrapper's states are gone."""
    rows = [line for line in section(page(), "Status").splitlines() if line.startswith("| ")]

    assert any(row.startswith(f"| `{state}` |") for row in rows), state


def test_the_quickstart_fetches_the_file_from_the_release_tag() -> None:
    """A clean host needs the file and nothing else: no archive to extract, no image to load."""
    quickstart = QUICKSTART.read_text(encoding="utf-8")

    assert "https://raw.githubusercontent.com/opsmill/infrahub-sync/<version>/docker-compose.yml" in quickstart
    assert "tar -xzf" not in quickstart
    assert "docker load" not in quickstart


@pytest.mark.parametrize("document", [PAGE, QUICKSTART], ids=lambda path: path.name)
def test_the_page_links_the_registry_login_instructions(document: Path) -> None:
    """Until 3.0.0 the registry is private, so a pull needs the login the install page gives."""
    assert "./installation.mdx#run-the-container-image" in document.read_text(encoding="utf-8")


def test_the_troubleshooting_page_covers_an_operator_file_without_the_cli_token() -> None:
    """An `operator.env` from an earlier alpha has no `INFRAHUB_SYNC_API_TOKEN` line at all."""
    troubleshooting = TROUBLESHOOTING.read_text(encoding="utf-8")

    assert "`operator.env`" in troubleshooting
    assert "INFRAHUB_SYNC_API_TOKEN" in troubleshooting
    assert "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS" in troubleshooting
