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


def _generate_infrahubsync_documentation(context: Context) -> None:
    """Generate the documentation for infrahub-sync using typer-cli."""

    print(" - Generate infrahub-sync CLI documentation")
    exec_cmd = 'uv run typer infrahub_sync.cli utils docs --name "infrahub-sync"'
    exec_cmd += " --output docs/docs/reference/cli.mdx"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


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
