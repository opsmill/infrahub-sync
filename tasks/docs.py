from pathlib import Path

from invoke import Context, task

from .utils import ESCAPED_REPO_PATH, check_if_command_available

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
    exec_cmd = 'poetry run typer infrahub_sync.cli utils docs --name "infrahub-sync"'
    exec_cmd += " --output docs/reference/cli.mdx"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


@task
def markdownlint(context: Context) -> None:
    has_markdownlint = check_if_command_available(context=context, command_name="markdownlint-cli2")

    if not has_markdownlint:
        print("Warning, markdownlint-cli2 is not installed")
        return
    exec_cmd = "markdownlint-cli2 **/*.{md,mdx} '#**/node_modules/**'"
    print(" - [docs] Lint docs with markdownlint-cli2")
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


@task
def format_markdownlint(context: Context) -> None:
    """Run markdownlint-cli2 to format all .md/mdx files."""

    print(" - [docs] Format code with markdownlint-cli2")
    exec_cmd = "markdownlint-cli2 **/*.{md,mdx} --fix"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


@task
def format(context: Context) -> None:
    """This will run all formatter."""
    format_markdownlint(context)


@task
def lint(context: Context) -> None:
    """This will run all linter."""
    markdownlint(context)
