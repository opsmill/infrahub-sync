import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

from invoke import Context, Exit, task

from .utils import ESCAPED_REPO_PATH, REPO_BASE

NAMESPACE = "INFRAHUB-SYNC-TEST"
CURRENT_DIRECTORY = Path(__file__).parent.resolve()
MAIN_DIRECTORY = CURRENT_DIRECTORY.parent

# ----------------------------------------------------------------------------
# Tests tasks
# ----------------------------------------------------------------------------


@task
def tests_unit(context: Context) -> None:
    """Run tests excluding the integration, preview, Docker, and Compose markers."""
    command = 'pytest -m "not integration and not preview and not docker and not compose"'
    if sys.version_info < (3, 11):
        command += " --ignore=tests/service --ignore=tests/runtime_schema/test_worker_path.py"
    with context.cd(MAIN_DIRECTORY):
        context.run(command, pty=True)


@task
def tests_integration(context: Context) -> None:
    """Run integration tests against a live Infrahub.

    Each test family skips when its settings are missing; see
    docs/docs/development/guidelines/testing-tiers.md#integration.
    """
    with context.cd(MAIN_DIRECTORY):
        context.run("pytest -m integration", pty=True)


@task
def tests_product_store_postgresql(context: Context) -> None:
    """Run the product store's and the admission race's PostgreSQL integration tests and fail if any is skipped.

    Requires ``PRODUCT_STORE_TEST_POSTGRESQL_DSN``. Plain ``pytest -m integration`` skips these
    tests when the driver or server is missing; this task turns every skip, an unset DSN, and an
    empty selection into a failure. See docs/docs/development/guidelines/testing-tiers.md.
    """
    if not os.environ.get("PRODUCT_STORE_TEST_POSTGRESQL_DSN"):
        print("PRODUCT_STORE_TEST_POSTGRESQL_DSN is not set; refusing to run without a PostgreSQL server.")
        raise Exit(code=1)
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "junit.xml"
        with context.cd(MAIN_DIRECTORY):
            result = context.run(
                "pytest -m integration tests/product_store tests/service/test_apply_versus_verify_race.py "
                f"-p no:cacheprovider -q --no-cov -rs --junitxml={shlex.quote(str(report))}",
                warn=True,
                pty=True,
            )
        if result is None or not result.ok:
            raise Exit(code=result.return_code if result else 1)
        # The report is our own pytest's; read the suite counters from its first element.
        suite = re.search(r"<testsuite\b[^>]*>", report.read_text(encoding="utf-8"))
        attributes = dict(re.findall(r'(\w+)="(\d+)"', suite.group(0))) if suite else {}
        tests = int(attributes.get("tests", 0))
        skipped = int(attributes.get("skipped", 0))
    if tests == 0 or skipped:
        print(
            f"PostgreSQL product-store check selected {tests} tests and skipped {skipped}; a run needs at least one test and no skips."
        )
        raise Exit(code=1)


@task(name="all")
def tests_all(context: Context) -> None:
    """Run the unit tests, then the integration tests."""
    tests_unit(context)
    tests_integration(context)


# ----------------------------------------------------------------------------
# Python 3.10 legs
# ----------------------------------------------------------------------------

CHECK_310_LEGS = (
    "ty check, with the Python 3.10 excludes for infrahub_sync/service and tests/service",
    "unit tests on Python 3.10 with the dev and prefect extras",
    "unit tests on Python 3.10 with the dev extra alone, without service runtimes",
)

# `uv sync --python 3.10` repins whichever environment it is pointed at, and no later
# sync restores that environment without an explicit `--python`. The 3.10 legs therefore
# build under the development runtime-state directory, never in the active .venv.
CHECK_310_ENVIRONMENT = {"UV_PROJECT_ENVIRONMENT": str(REPO_BASE / ".dev" / "check-310-venv")}


@task(name="check-310")
def check_310(context: Context) -> None:
    """Run the three CI legs the active environment cannot check, stopping at the first failure.

    Builds its environments under .dev/check-310-venv so the active .venv keeps its
    own interpreter and extras. Skips loudly and succeeds when Python 3.10 is absent.
    """
    if not _python_310_available(context):
        print(f" - [{NAMESPACE}] check-310 SKIPPED: no Python 3.10 interpreter, so nothing below was checked:")
        for leg in CHECK_310_LEGS:
            print(f" - [{NAMESPACE}]     {leg}")
        print(
            f" - [{NAMESPACE}] Install one with `uv python install 3.10`, then re-run `uv run invoke tests.check-310`."
        )
        return

    with context.cd(ESCAPED_REPO_PATH):
        print(f" - [{NAMESPACE}] Build the Python 3.10 dev+prefect environment")
        context.run("uv sync --python 3.10 --frozen --extra dev --extra prefect", env=CHECK_310_ENVIRONMENT, pty=True)

        print(f" - [{NAMESPACE}] {CHECK_310_LEGS[0]}")
        context.run(
            "uv run --no-sync ty check --exclude infrahub_sync/service --exclude tests/service .",
            env=CHECK_310_ENVIRONMENT,
            pty=True,
        )

        print(f" - [{NAMESPACE}] {CHECK_310_LEGS[1]}")
        context.run("uv run --no-sync invoke tests.tests-unit", env=CHECK_310_ENVIRONMENT, pty=True)

        print(f" - [{NAMESPACE}] Build the Python 3.10 base install")
        context.run("uv sync --python 3.10 --frozen --extra dev", env=CHECK_310_ENVIRONMENT, pty=True)

        print(f" - [{NAMESPACE}] {CHECK_310_LEGS[2]}")
        context.run("uv run --no-sync invoke tests.tests-unit", env=CHECK_310_ENVIRONMENT, pty=True)

    print(f" - [{NAMESPACE}] check-310 passed all three Python 3.10 legs")


def _python_310_available(context: Context) -> bool:
    """Return whether uv can resolve an installed Python 3.10 interpreter."""
    result = context.run("uv python find 3.10 --no-project", hide=True, warn=True)
    return result is not None and result.ok
