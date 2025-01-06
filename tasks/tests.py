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
    pass


@task
def tests_integration(context: Context) -> None:
    pass
