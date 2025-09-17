"""Python API for Infrahub Sync.

This module provides a Python API to perform sync operations
programmatically without using the CLI.
"""

from __future__ import annotations

from timeit import default_timer as timer
from typing import TYPE_CHECKING, Any

from rich.console import Console

from infrahub_sync.utils import (
    get_all_sync,
    get_instance,
    get_potenda_from_instance,
)

if TYPE_CHECKING:
    from infrahub_sync import SyncInstance
    from infrahub_sync.potenda import Potenda

console = Console()


class SyncError(Exception):
    """Raised when sync operations fail."""

    pass


class SyncResult:
    """Result object returned by sync operations."""

    def __init__(
        self,
        success: bool,
        message: str | None = None,
        duration: float | None = None,
        changes_detected: bool | None = None,
        error: Exception | None = None,
    ):
        self.success = success
        self.message = message
        self.duration = duration
        self.changes_detected = changes_detected
        self.error = error

    def __repr__(self) -> str:
        return f"SyncResult(success={self.success}, message='{self.message}', duration={self.duration})"


def list_projects(directory: str | None = None) -> list[SyncInstance]:
    """List all available sync projects.
    
    Args:
        directory: Base directory to search for sync configurations.
                   If None, searches from the package directory.
    
    Returns:
        List of SyncInstance objects representing available sync projects.
    
    Example:
        >>> from infrahub_sync.api import list_projects
        >>> projects = list_projects()
        >>> for project in projects:
        ...     print(f"{project.name}: {project.source.name} -> {project.destination.name}")
    """
    return get_all_sync(directory=directory)


def diff(
    name: str | None = None,
    config_file: str | None = None,
    directory: str | None = None,
    branch: str | None = None,
    show_progress: bool = True,
) -> SyncResult:
    """Calculate and return the differences between source and destination systems.
    
    Args:
        name: Name of the sync to use (mutually exclusive with config_file).
        config_file: File path to the sync configuration YAML file.
        directory: Base directory to search for sync configurations.
        branch: Branch to use for the diff.
        show_progress: Show a progress bar during diff.
    
    Returns:
        SyncResult object containing diff information and success status.
    
    Raises:
        SyncError: When exactly one of name or config_file is not specified,
                   or when sync initialization/loading fails.
    
    Example:
        >>> from infrahub_sync.api import diff
        >>> result = diff(name="my-sync")
        >>> if result.success:
        ...     print(f"Diff completed: {result.message}")
        >>> else:
        ...     print(f"Diff failed: {result.error}")
    """
    if sum([bool(name), bool(config_file)]) != 1:
        raise SyncError("Please specify exactly one of 'name' or 'config_file'.")

    sync_instance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        raise SyncError("Failed to load sync instance.")

    try:
        ptd = get_potenda_from_instance(sync_instance=sync_instance, branch=branch, show_progress=show_progress)
    except ValueError as exc:
        raise SyncError(f"Failed to initialize the Sync Instance: {exc}") from exc

    try:
        ptd.source_load()
        ptd.destination_load()
    except ValueError as exc:
        raise SyncError(f"Failed to load data: {exc}") from exc

    start_time = timer()
    mydiff = ptd.diff()
    end_time = timer()

    changes_detected = mydiff.has_diffs()
    diff_str = mydiff.str() if changes_detected else "No differences found"

    return SyncResult(
        success=True,
        message=diff_str,
        duration=end_time - start_time,
        changes_detected=changes_detected,
    )


def sync(
    name: str | None = None,
    config_file: str | None = None,
    directory: str | None = None,
    branch: str | None = None,
    diff_first: bool = True,
    show_progress: bool = True,
) -> SyncResult:
    """Synchronize data between source and destination systems.
    
    Args:
        name: Name of the sync to use (mutually exclusive with config_file).
        config_file: File path to the sync configuration YAML file.
        directory: Base directory to search for sync configurations.
        branch: Branch to use for the sync.
        diff_first: Calculate and show differences before syncing.
        show_progress: Show a progress bar during syncing.
    
    Returns:
        SyncResult object containing sync status and information.
    
    Raises:
        SyncError: When exactly one of name or config_file is not specified,
                   or when sync initialization/loading fails.
    
    Example:
        >>> from infrahub_sync.api import sync
        >>> result = sync(name="my-sync")
        >>> if result.success:
        ...     print(f"Sync completed in {result.duration:.2f} seconds")
        >>> else:
        ...     print(f"Sync failed: {result.error}")
    """
    if sum([bool(name), bool(config_file)]) != 1:
        raise SyncError("Please specify exactly one of 'name' or 'config_file'.")

    sync_instance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        raise SyncError("Failed to load sync instance.")

    try:
        ptd = get_potenda_from_instance(sync_instance=sync_instance, branch=branch, show_progress=show_progress)
    except ValueError as exc:
        raise SyncError(f"Failed to initialize the Sync Instance: {exc}") from exc

    try:
        ptd.source_load()
        ptd.destination_load()
    except ValueError as exc:
        raise SyncError(f"Failed to load data: {exc}") from exc

    mydiff = ptd.diff()

    if mydiff.has_diffs():
        diff_message = f"Found differences to sync" + (f":\n{mydiff.str()}" if diff_first else "")
        start_synctime = timer()
        ptd.sync(diff=mydiff)
        end_synctime = timer()
        
        return SyncResult(
            success=True,
            message=f"Sync completed. {diff_message}",
            duration=end_synctime - start_synctime,
            changes_detected=True,
        )
    else:
        return SyncResult(
            success=True,
            message="No differences found. Nothing to sync.",
            duration=0.0,
            changes_detected=False,
        )


def create_potenda(
    name: str | None = None,
    config_file: str | None = None,
    directory: str | None = None,
    branch: str | None = None,
    show_progress: bool = True,
) -> Potenda:
    """Create a Potenda instance for advanced programmatic control.
    
    This function provides direct access to the Potenda object for users who need
    fine-grained control over the sync process, allowing them to call individual
    methods like source_load(), destination_load(), diff(), and sync().
    
    Args:
        name: Name of the sync to use (mutually exclusive with config_file).
        config_file: File path to the sync configuration YAML file.
        directory: Base directory to search for sync configurations.
        branch: Branch to use for the sync.
        show_progress: Show a progress bar during operations.
    
    Returns:
        Potenda instance configured with the specified sync configuration.
    
    Raises:
        SyncError: When exactly one of name or config_file is not specified,
                   or when sync initialization fails.
    
    Example:
        >>> from infrahub_sync.api import create_potenda
        >>> ptd = create_potenda(name="my-sync")
        >>> ptd.source_load()
        >>> ptd.destination_load()
        >>> diff = ptd.diff()
        >>> if diff.has_diffs():
        ...     ptd.sync(diff=diff)
    """
    if sum([bool(name), bool(config_file)]) != 1:
        raise SyncError("Please specify exactly one of 'name' or 'config_file'.")

    sync_instance = get_instance(name=name, config_file=config_file, directory=directory)
    if not sync_instance:
        raise SyncError("Failed to load sync instance.")

    try:
        return get_potenda_from_instance(sync_instance=sync_instance, branch=branch, show_progress=show_progress)
    except ValueError as exc:
        raise SyncError(f"Failed to initialize the Sync Instance: {exc}") from exc


# Convenient aliases for common operations
diff_sync = diff  # Alternative name for clarity
sync_data = sync  # Alternative name for clarity

__all__ = [
    "SyncError",
    "SyncResult", 
    "list_projects",
    "diff",
    "sync",
    "create_potenda",
    "diff_sync",
    "sync_data",
]