import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

from invoke import Context, Exit, task

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
    docs/docs/develop/guidelines/testing-tiers.md#integration.
    """
    with context.cd(MAIN_DIRECTORY):
        context.run("pytest -m integration", pty=True)


@task
def tests_product_store_postgresql(context: Context) -> None:
    """Run the product store's and the admission race's PostgreSQL integration tests and fail if any is skipped.

    Requires ``PRODUCT_STORE_TEST_POSTGRESQL_DSN``. Plain ``pytest -m integration`` skips these
    tests when the driver or server is missing; this task turns every skip, an unset DSN, and an
    empty selection into a failure. See docs/docs/develop/guidelines/testing-tiers.md.
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
