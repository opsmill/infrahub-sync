import logging
from typing import TYPE_CHECKING

import typer
from infrahub_sdk import InfrahubClientSync
from infrahub_sdk.exceptions import ServerNotResponsiveError
from rich.console import Console

from infrahub_sync.api import SyncError, diff as api_diff, list_projects as api_list_projects, sync as api_sync
from infrahub_sync.utils import (
    find_missing_schema_model,
    get_infrahub_config,
    get_instance,
    render_adapter,
)

if TYPE_CHECKING:
    from infrahub_sync import SyncInstance

app = typer.Typer()
console = Console()

logging.basicConfig(level=logging.WARNING)


def print_error_and_abort(message: str) -> typer.Abort:
    console.print(f"Error: {message}", style="bold red")
    raise typer.Abort


@app.command(name="list")
def list_projects(
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
) -> None:
    """List all available SYNC projects."""
    try:
        projects = api_list_projects(directory=directory)
        for project in projects:
            console.print(f"{project.name} | {project.source.name} >> {project.destination.name} | {project.directory}")
    except Exception as exc:
        print_error_and_abort(f"Failed to list projects: {exc}")


@app.command(name="diff")
def diff_cmd(
    name: str = typer.Option(default=None, help="Name of the sync to use"),
    config_file: str = typer.Option(default=None, help="File path to the sync configuration YAML file"),
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
    branch: str = typer.Option(default=None, help="Branch to use for the diff."),
    show_progress: bool = typer.Option(default=True, help="Show a progress bar during diff"),
) -> None:
    """Calculate and print the differences between the source and the destination systems for a given project."""
    try:
        result = api_diff(
            name=name,
            config_file=config_file,
            directory=directory,
            branch=branch,
            show_progress=show_progress,
        )
        if result.success:
            print(result.message)
        else:
            print_error_and_abort(f"Diff failed: {result.error}")
    except SyncError as exc:
        print_error_and_abort(str(exc))


@app.command(name="sync")
def sync_cmd(
    name: str = typer.Option(default=None, help="Name of the sync to use"),
    config_file: str = typer.Option(default=None, help="File path to the sync configuration YAML file"),
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
    branch: str = typer.Option(default=None, help="Branch to use for the sync."),
    diff: bool = typer.Option(
        default=True,
        help="Print the differences between the source and the destination before syncing",
    ),
    show_progress: bool = typer.Option(default=True, help="Show a progress bar during syncing"),
) -> None:
    """Synchronize the data between source and the destination systems for a given project or configuration file."""
    try:
        result = api_sync(
            name=name,
            config_file=config_file,
            directory=directory,
            branch=branch,
            diff_first=diff,
            show_progress=show_progress,
        )
        if result.success:
            if result.changes_detected:
                console.print(f"Sync: Completed in {result.duration} sec")
            else:
                console.print("No difference found. Nothing to sync")
        else:
            print_error_and_abort(f"Sync failed: {result.error}")
    except SyncError as exc:
        print_error_and_abort(str(exc))


@app.command(name="generate")
def generate(
    name: str = typer.Option(default=None, help="Name of the sync to use"),
    config_file: str = typer.Option(default=None, help="File path to the sync configuration YAML file"),
    directory: str = typer.Option(default=None, help="Base directory to search for sync configurations"),
    branch: str = typer.Option(default=None, help="Branch to use for the sync."),
) -> None:
    """Generate all the Python files for a given sync based on the configuration."""

    if sum([bool(name), bool(config_file)]) != 1:
        print_error_and_abort("Please specify exactly one of 'name' or 'config_file'.")

    sync_instance: SyncInstance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        print_error_and_abort(f"Unable to find the sync {name}. Use the list command to see the sync available")

    # Check if the destination is infrahub
    infrahub_address = ""
    # Determine if infrahub is in source or destination
    # We are using the destination as the "constraint", if there is 2 infrahubs instance
    sdk_config = None
    if sync_instance.destination.name == "infrahub" and sync_instance.destination.settings:
        infrahub_address = sync_instance.destination.settings.get("url") or ""
        sdk_config = get_infrahub_config(settings=sync_instance.destination.settings, branch=branch)
    elif sync_instance.source.name == "infrahub" and sync_instance.source.settings:
        infrahub_address = sync_instance.source.settings.get("url") or ""
        sdk_config = get_infrahub_config(settings=sync_instance.source.settings, branch=branch)

    # Initialize InfrahubClientSync if address and config are available
    client = InfrahubClientSync(address=infrahub_address, config=sdk_config)

    try:
        schema = client.schema.all()
    except ServerNotResponsiveError as exc:
        print_error_and_abort(str(exc))

    missing_schema_models = find_missing_schema_model(sync_instance=sync_instance, schema=schema)
    if missing_schema_models:
        print_error_and_abort(f"One or more model model are not present in the Schema - {missing_schema_models}")

    rendered_files = render_adapter(sync_instance=sync_instance, schema=schema)
    for template, output_path in rendered_files:
        console.print(f"Rendered template {template} to {output_path}")
