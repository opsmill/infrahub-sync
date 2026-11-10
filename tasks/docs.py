import sys
from pathlib import Path

from invoke import Context, task

from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC-DOCS"
CURRENT_DIRECTORY = Path(__file__).parent.resolve()
DOCUMENTATION_DIRECTORY = CURRENT_DIRECTORY.parent / "docs"


@task
def generate(context: Context) -> None:
    """Generate documentation for the infrahub-sync cli."""
    del context  # Generation runs in process; invoke still passes its context.
    _generate_infrahubsync_documentation()


def _generate_infrahubsync_documentation() -> None:
    """Generate the infrahub-sync CLI reference with Typer's Markdown renderer.

    This renders in process, as `typer infrahub_sync.cli utils docs` would, so hidden
    commands, such as the deprecated `diff` alias, can be removed from the command tree
    first: Typer documents hidden commands too, so the reference would otherwise teach a
    command that `--help` does not show.
    """
    # Imported here so `invoke --list` does not load the CLI.
    import typer  # noqa: PLC0415
    from typer.cli import get_docs_for_click  # noqa: PLC0415
    from typer.core import MARKUP_MODE_KEY  # noqa: PLC0415

    from infrahub_sync.cli import app  # noqa: PLC0415

    print(" - Generate infrahub-sync CLI documentation")
    command = typer.main.get_command(app)
    _drop_hidden_commands(command)
    context = typer.Context(command)
    context.ensure_object(dict)[MARKUP_MODE_KEY] = app.rich_markup_mode
    docs = get_docs_for_click(obj=command, ctx=context, name="infrahub-sync")
    reference = DOCUMENTATION_DIRECTORY / "docs" / "reference" / "cli.mdx"
    reference.write_text(f"{docs.strip()}\n", encoding="utf-8")
    _insert_api_token_intro(reference)


# Hand-written paragraph placed before the root command's first "**Usage**:" line. Typer has
# no place for it, so generation inserts it after every run.
_AUTHENTICATION_INTRO = (
    "Set `INFRAHUB_SYNC_API_TOKEN` to the bearer token for your actor before running any\n"
    "command; the CLI reads it from the environment and never accepts it as a flag. See\n"
    "[Sync HTTP API — Configure principals](./sync-http-api.mdx#configure-principals) for how\n"
    "tokens are issued.\n"
    "\n"
    "When the Sync API address is missing or invalid, or the token is missing, a command prints\n"
    "`error: client-input` and `argument: service_url` or `argument: token` on standard error,\n"
    "then a `hint:` line. An address is invalid when it does not use `http` or `https`, has no\n"
    "host, or carries a user name, a password, a query string or a fragment. For the address,\n"
    "set `INFRAHUB_SYNC_API_URL` or pass `--api-url` with the absolute URL of the Sync API; for\n"
    "the token, set `INFRAHUB_SYNC_API_TOKEN`. Other `client-input` errors print no `hint:` line.\n"
)


def _insert_api_token_intro(path: Path) -> None:
    """Insert the `INFRAHUB_SYNC_API_TOKEN` paragraph before the root command's usage block."""
    text = path.read_text(encoding="utf-8")
    if _AUTHENTICATION_INTRO in text:
        return
    marker = "**Usage**:\n"
    if marker not in text:
        msg = f"{path} has no '**Usage**:' line to place the API token paragraph before"
        raise ValueError(msg)
    path.write_text(text.replace(marker, f"{_AUTHENTICATION_INTRO}\n{marker}", 1), encoding="utf-8")


def _drop_hidden_commands(command: object) -> None:
    """Remove every command registered as hidden from the click tree under ``command``."""
    # Typer may build its commands on a click copy of its own, so groups are recognized by
    # their `commands` mapping rather than by `isinstance(..., click.Group)`.
    commands = getattr(command, "commands", None)
    if not isinstance(commands, dict):
        return
    for name in [name for name, child in commands.items() if getattr(child, "hidden", False)]:
        del commands[name]
    for child in commands.values():
        _drop_hidden_commands(child)


@task
def rumdl(context: Context) -> None:
    """Lint all Markdown/MDX files with rumdl (config in pyproject.toml)."""
    print(" - [docs] Lint docs with rumdl")
    exec_cmd = "rumdl check ."
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


@task
def format_rumdl(context: Context) -> None:
    """Auto-fix all Markdown/MDX files with rumdl."""
    print(" - [docs] Format docs with rumdl")
    exec_cmd = "rumdl fmt ."
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


@task
def format(context: Context) -> None:  # noqa: A001
    """This will run all formatters."""
    format_rumdl(context)


@task
def lint(context: Context) -> None:
    """This will run all linters."""
    rumdl(context)


@task
def docusaurus(context: Context) -> None:
    """Build documentation website."""
    exec_cmd = "pnpm run build"

    with context.cd(DOCUMENTATION_DIRECTORY):
        output = context.run(exec_cmd)

    if output is None or output.exited != 0:
        sys.exit(-1)
