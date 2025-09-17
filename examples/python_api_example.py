#!/usr/bin/env python3
"""Example usage of the Infrahub Sync Python API.

This script demonstrates how to use the Python API for Infrahub Sync
instead of the command line interface.
"""

from infrahub_sync.api import (
    SyncError,
    list_projects,
    diff,
    sync,
    create_potenda,
)


def main():
    """Main example demonstrating API usage."""
    print("=== Infrahub Sync Python API Example ===\n")

    # Example 1: List all available sync projects
    print("1. Listing all available sync projects:")
    try:
        projects = list_projects()
        if projects:
            for project in projects:
                print(f"   - {project.name}: {project.source.name} -> {project.destination.name}")
        else:
            print("   No sync projects found.")
    except Exception as e:
        print(f"   Error listing projects: {e}")
    print()

    # Example 2: Perform a diff operation (commented out as we don't have real config)
    print("2. Example diff operation (requires actual sync configuration):")
    print("""
    try:
        result = diff(name="my-sync", show_progress=True)
        if result.success:
            print(f"   Diff completed in {result.duration:.2f} seconds")
            if result.changes_detected:
                print(f"   Changes detected:\\n{result.message}")
            else:
                print("   No changes detected")
        else:
            print(f"   Diff failed: {result.error}")
    except SyncError as e:
        print(f"   Sync error: {e}")
    """)

    # Example 3: Perform a sync operation (commented out as we don't have real config)
    print("3. Example sync operation (requires actual sync configuration):")
    print("""
    try:
        result = sync(name="my-sync", diff_first=True, show_progress=True)
        if result.success:
            print(f"   Sync completed in {result.duration:.2f} seconds")
            print(f"   Result: {result.message}")
        else:
            print(f"   Sync failed: {result.error}")
    except SyncError as e:
        print(f"   Sync error: {e}")
    """)

    # Example 4: Advanced usage with Potenda object
    print("4. Example advanced usage with Potenda object:")
    print("""
    try:
        # Create a Potenda instance for fine-grained control
        ptd = create_potenda(name="my-sync", show_progress=True)
        
        # Load data from source and destination
        ptd.source_load()
        ptd.destination_load()
        
        # Calculate differences
        diff_result = ptd.diff()
        
        if diff_result.has_diffs():
            print("   Changes detected, proceeding with sync...")
            ptd.sync(diff=diff_result)
            print("   Sync completed successfully!")
        else:
            print("   No changes to sync.")
            
    except SyncError as e:
        print(f"   Sync error: {e}")
    """)

    # Example 5: Error handling
    print("5. Example error handling:")
    print("""
    try:
        # This will fail because we're not providing name or config_file
        result = diff()
    except SyncError as e:
        print(f"   Expected error: {e}")
    """)

    print("\n=== API Documentation ===")
    print("""
Available functions in infrahub_sync.api:

1. list_projects(directory=None) -> List[SyncInstance]
   - Lists all available sync configurations
   
2. diff(name=None, config_file=None, directory=None, branch=None, show_progress=True) -> SyncResult
   - Calculates differences between source and destination
   
3. sync(name=None, config_file=None, directory=None, branch=None, diff_first=True, show_progress=True) -> SyncResult
   - Synchronizes data from source to destination
   
4. create_potenda(name=None, config_file=None, directory=None, branch=None, show_progress=True) -> Potenda
   - Creates a Potenda instance for advanced programmatic control

Classes:
- SyncError: Exception raised when sync operations fail
- SyncResult: Result object returned by sync operations with success status, messages, timing, etc.

For more information, see the docstrings in infrahub_sync.api module.
""")


if __name__ == "__main__":
    main()