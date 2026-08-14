import sys
from pathlib import Path

from invoke import Context, task

from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC"
CURRENT_DIRECTORY = Path(__file__).parent.resolve()
MAIN_DIRECTORY = "."


def _ty_check_command(python_major: int, python_minor: int) -> str:
    """Return the type-check command for the active supported runtime profile."""
    if (python_major, python_minor) == (3, 10):
        return "uv run ty check --exclude infrahub_sync/managed --exclude tests/managed ."
    return "uv run ty check ."


@task(name="format")
def lint_all(context: Context) -> None:
    """This will run all linter."""

    lint_ruff(context)
    lint_pylint(context)
    lint_yaml(context)
    lint_ty(context)

    print(f" - [{NAMESPACE}] All linter have been executed!")


@task(name="format")
def format_all(context: Context) -> None:
    """This will run all formatter."""

    format_ruff(context)

    print(f" - [{NAMESPACE}] All formatters have been executed!")


# ----------------------------------------------------------------------------
# Linter tasks - Python
# ----------------------------------------------------------------------------
@task
def lint_pylint(context: Context) -> None:
    """This will run pylint for the specified name and Python version."""

    print(f" - [{NAMESPACE}] Check code with pylint")
    exec_cmd = "pylint infrahub_sync/"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


@task
def lint_ruff(context: Context) -> None:
    """This will run ruff."""

    print(f" - [{NAMESPACE}] Check code with ruff")
    exec_cmd = f"ruff format --check --diff {MAIN_DIRECTORY} &&"
    exec_cmd += f"ruff check --diff {MAIN_DIRECTORY}"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


# ----------------------------------------------------------------------------
# Linter tasks - Yaml
# ----------------------------------------------------------------------------


@task
def lint_yaml(context: Context) -> None:
    """This will run yamllint to validate formatting of all yaml files."""

    print(f" - [{NAMESPACE}] Format yaml with yamllint")
    exec_cmd = f"yamllint {MAIN_DIRECTORY}"
    context.run(exec_cmd, pty=True)


@task
def lint_ty(context: Context) -> None:
    """Run ty type checker against project files."""

    print(f" - [{NAMESPACE}] Check code with ty")
    exec_cmd = _ty_check_command(sys.version_info.major, sys.version_info.minor)

    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)


# ----------------------------------------------------------------------------
# Formatting tasks - Python
# ----------------------------------------------------------------------------
@task
def format_ruff(context: Context) -> None:
    """This will run ruff."""

    print(f" - [{NAMESPACE}] Check code with ruff")
    exec_cmd = f"ruff format {MAIN_DIRECTORY} && "
    exec_cmd += f"ruff check --fix {MAIN_DIRECTORY}"
    with context.cd(ESCAPED_REPO_PATH):
        context.run(exec_cmd)
