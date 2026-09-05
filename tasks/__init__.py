"""Replacement for Makefile."""

from invoke import Collection, Context, task

from . import docs, image, linter, preview, tests
from .utils import ESCAPED_REPO_PATH, REPO_BASE

NAMESPACE = "INFRAHUB-SYNC"

CHECK_310_LEGS = (
    "ty check, with the Python 3.10 excludes for infrahub_sync/service and tests/service",
    "unit tests on Python 3.10 with the dev and prefect extras",
    "unit tests on Python 3.10 with the dev extra alone, without service runtimes",
)

# `uv sync --python 3.10` repins whichever environment it is pointed at, and no later
# sync restores that environment without an explicit `--python`. The 3.10 legs therefore
# build under the preview runtime-state directory, never in the active .venv.
CHECK_310_ENVIRONMENT = {"UV_PROJECT_ENVIRONMENT": str(REPO_BASE / ".preview" / "check-310-venv")}

ns = Collection("infrahub_sync")
ns.configure(
    {
        "infrahub_sync": {
            "project_name": "infrahub_sync",
            "python_ver": "3.10",
            "local": False,
        }
    }
)
ns.add_collection(Collection.from_module(linter))
ns.add_collection(Collection.from_module(docs))
ns.add_collection(Collection.from_module(tests))
ns.add_collection(Collection.from_module(preview))
ns.add_collection(Collection.from_module(image))


@task(name="lint")
def lint_all(context: Context) -> None:
    docs.lint(context)
    linter.lint_all(context)


@task(name="format")
def format_all(context: Context) -> None:
    docs.format(context)
    linter.format_all(context)


@task(name="tests-all")
def test_all(context: Context) -> None:
    tests.tests_unit(context)
    tests.tests_integration(context)


@task(name="tests-unit")
def tests_unit(context: Context) -> None:
    tests.tests_unit(context)


@task(name="tests-integration")
def tests_integration(context: Context) -> None:
    tests.tests_integration(context)


@task(name="generate-doc")
def generate_doc(context: Context) -> None:
    docs.generate(context)


@task(name="docusaurus")
def docusaurus(context: Context) -> None:
    docs.docusaurus(context)


@task(name="check-310")
def check_310(context: Context) -> None:
    """Run the three CI legs the active environment cannot check, stopping at the first failure.

    Builds its environments under .preview/check-310-venv so the active .venv keeps its
    own interpreter and extras. Skips loudly and succeeds when Python 3.10 is absent.
    """
    if not _python_310_available(context):
        print(f" - [{NAMESPACE}] check-310 SKIPPED: no Python 3.10 interpreter, so nothing below was checked:")
        for leg in CHECK_310_LEGS:
            print(f" - [{NAMESPACE}]     {leg}")
        print(f" - [{NAMESPACE}] Install one with `uv python install 3.10`, then re-run `uv run invoke check-310`.")
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


ns.add_task(lint_all)
ns.add_task(format_all)
ns.add_task(test_all)
ns.add_task(tests_unit)
ns.add_task(tests_integration)
ns.add_task(generate_doc)
ns.add_task(docusaurus)
ns.add_task(check_310)
