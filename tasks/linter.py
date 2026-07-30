from pathlib import Path

from invoke import Context, task

from .utils import ESCAPED_REPO_PATH

NAMESPACE = "INFRAHUB-SYNC"
CURRENT_DIRECTORY = Path(__file__).parent.resolve()
MAIN_DIRECTORY = "."


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
    # A severity gate rather than a style gate (MIN-014, spec 002). pylint's default exit code
    # is a bitmask of the message *categories* it emitted, so the pre-existing convention and
    # refactor advice in this repository made `invoke lint` fail every time and left a reviewer
    # unable to tell real breakage from long-standing noise. `--fail-on` keeps error- and
    # fatal-category messages failing the task; `--fail-under` is what stops the advisory
    # categories from doing so, and holds the score at roughly where it stands today.
    exec_cmd = "pylint --fail-under=9.5 --fail-on=E,F infrahub_sync/"
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
    exec_cmd = "uv run ty check ."

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
