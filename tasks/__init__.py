"""Replacement for Makefile."""

from invoke import Collection, Context, task

from . import docs, linter, tests

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
ns.add_collection(linter)
ns.add_collection(docs)
ns.add_collection(tests)


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
    docs.generate_doc(context)


@task(name="docusaurus")
def docusaurus(context: Context) -> None:
    docs.docusaurus(context)


ns.add_task(lint_all)
ns.add_task(format_all)
ns.add_task(test_all)
ns.add_task(tests_unit)
ns.add_task(tests_integration)
ns.add_task(generate_doc)
ns.add_task(docusaurus)
