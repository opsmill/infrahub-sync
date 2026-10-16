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
    _generate_infrahubsync_documentation(context=context)


CLI_REFERENCE = DOCUMENTATION_DIRECTORY / "docs" / "reference" / "cli.mdx"
# The first paragraph typer writes for the root command: the app's help text.
CLI_REFERENCE_ANCHOR = "Synchronize registered configurations through the Sync API.\n"
# Written after the root command's help in the reference only: in `--help` the link would
# print as raw Markdown.
CLI_REFERENCE_AUTH_PARAGRAPH = """
Set `INFRAHUB_SYNC_API_TOKEN` to the bearer token for your actor before running any
command; the CLI reads it from the environment and never accepts it as a flag. See
[Sync HTTP API — Configure principals](./sync-http-api.mdx#configure-principals) for how
tokens are issued.
"""


def _generate_infrahubsync_documentation(context: Context) -> None:
    """Generate the documentation for infrahub-sync using typer-cli."""

    print(" - Generate infrahub-sync CLI documentation")
    exec_cmd = 'uv run typer infrahub_sync.cli utils docs --name "infrahub-sync"'
    exec_cmd += " --output docs/docs/reference/cli.mdx"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)
    _add_auth_paragraph(CLI_REFERENCE)


def _add_auth_paragraph(reference: Path) -> None:
    """Insert the token paragraph after the root command's help in the generated reference."""
    content = reference.read_text(encoding="utf-8")
    if CLI_REFERENCE_ANCHOR not in content:
        msg = f"{reference}: the root command's help text changed; update CLI_REFERENCE_ANCHOR"
        raise RuntimeError(msg)
    content = content.replace(CLI_REFERENCE_ANCHOR, CLI_REFERENCE_ANCHOR + CLI_REFERENCE_AUTH_PARAGRAPH, 1)
    reference.write_text(content, encoding="utf-8")


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
