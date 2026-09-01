import sys
from pathlib import Path

from invoke import Context, task

NAMESPACE = "INFRAHUB-SYNC-TEST"
CURRENT_DIRECTORY = Path(__file__).parent.resolve()
MAIN_DIRECTORY = CURRENT_DIRECTORY.parent

# ----------------------------------------------------------------------------
# Tests tasks
# ----------------------------------------------------------------------------


@task
def tests_unit(context: Context) -> None:
    """Run unit tests — everything under tests/ except integration-marked tests."""
    command = 'pytest -m "not integration and not preview"'
    if sys.version_info < (3, 11):
        command += " --ignore=tests/service --ignore=tests/runtime_schema/test_worker_path.py"
    with context.cd(MAIN_DIRECTORY):
        context.run(command, pty=True)


@task
def tests_integration(context: Context) -> None:
    """Run integration tests against a live Infrahub.

    Requires INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN in the environment;
    tests skip themselves when those aren't set.
    """
    with context.cd(MAIN_DIRECTORY):
        context.run("pytest -m integration", pty=True)
