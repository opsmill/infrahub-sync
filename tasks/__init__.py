"""Replacement for Makefile."""

from invoke import Collection, Context, task

from . import bench, dev, docs, linter, netbox, release, tests

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
ns.add_collection(Collection.from_module(netbox))
ns.add_collection(Collection.from_module(bench))
ns.add_collection(Collection.from_module(release))
ns.add_collection(dev.ns)


@task(name="lint")
def lint_all(context: Context) -> None:
    docs.lint(context)
    linter.lint_all(context)


@task(name="format")
def format_all(context: Context) -> None:
    docs.format(context)
    linter.format_all(context)


ns.add_task(lint_all)
ns.add_task(format_all)
