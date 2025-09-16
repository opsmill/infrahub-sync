# Infrahub Sync Python API

This document describes how to use Infrahub Sync programmatically via Python instead of the command line interface.

## Overview

The Python API provides the same functionality as the CLI but allows you to integrate sync operations directly into your Python applications. This is useful for:

- Automating sync operations within larger Python workflows
- Building custom integrations and tools
- Implementing conditional sync logic
- Creating monitoring and alerting systems around sync operations

## Installation

The Python API is available when you install infrahub-sync:

```bash
pip install infrahub-sync
```

## Quick Start

### Basic Usage

```python
from infrahub_sync.api import sync, diff, list_projects

# List all available sync projects
projects = list_projects()
for project in projects:
    print(f"{project.name}: {project.source.name} -> {project.destination.name}")

# Perform a diff operation
result = diff(name="my-sync-project")
if result.success and result.changes_detected:
    print(f"Changes detected: {result.message}")

# Perform a sync operation
result = sync(name="my-sync-project")
if result.success:
    print(f"Sync completed in {result.duration:.2f} seconds")
```

### Using Configuration Files

```python
from infrahub_sync.api import sync, diff

# Use a specific configuration file
result = diff(config_file="/path/to/config.yml")

# Use a configuration file with custom directory
result = sync(
    config_file="config.yml", 
    directory="/path/to/sync/configs"
)
```

### Advanced Usage with Potenda

For fine-grained control over the sync process:

```python
from infrahub_sync.api import create_potenda

# Create a Potenda instance
ptd = create_potenda(name="my-sync-project")

# Load data from source and destination separately
ptd.source_load()
ptd.destination_load()

# Calculate differences
diff_result = ptd.diff()

# Only sync if there are changes
if diff_result.has_diffs():
    print("Changes detected, syncing...")
    ptd.sync(diff=diff_result)
else:
    print("No changes to sync")
```

## API Reference

### Functions

#### `list_projects(directory=None)`

List all available sync projects.

**Parameters:**
- `directory` (str, optional): Base directory to search for sync configurations

**Returns:**
- `List[SyncInstance]`: List of available sync configurations

**Example:**
```python
projects = list_projects()
projects_in_custom_dir = list_projects(directory="/custom/sync/configs")
```

#### `diff(name=None, config_file=None, directory=None, branch=None, show_progress=True)`

Calculate differences between source and destination systems.

**Parameters:**
- `name` (str, optional): Name of the sync project (mutually exclusive with config_file)
- `config_file` (str, optional): Path to sync configuration YAML file
- `directory` (str, optional): Base directory to search for sync configurations
- `branch` (str, optional): Branch to use for the diff
- `show_progress` (bool): Show progress bar during operation

**Returns:**
- `SyncResult`: Result object with success status, message, duration, and change detection

**Raises:**
- `SyncError`: When parameters are invalid or operation fails

**Example:**
```python
result = diff(name="my-sync")
if result.success:
    if result.changes_detected:
        print(f"Changes found: {result.message}")
    else:
        print("No changes detected")
```

#### `sync(name=None, config_file=None, directory=None, branch=None, diff_first=True, show_progress=True)`

Synchronize data between source and destination systems.

**Parameters:**
- `name` (str, optional): Name of the sync project (mutually exclusive with config_file)  
- `config_file` (str, optional): Path to sync configuration YAML file
- `directory` (str, optional): Base directory to search for sync configurations
- `branch` (str, optional): Branch to use for the sync
- `diff_first` (bool): Calculate and show differences before syncing
- `show_progress` (bool): Show progress bar during operation

**Returns:**
- `SyncResult`: Result object with success status, message, and timing information

**Raises:**
- `SyncError`: When parameters are invalid or operation fails

**Example:**
```python
result = sync(name="my-sync", show_progress=True)
if result.success:
    print(f"Sync completed in {result.duration:.2f} seconds")
    print(f"Changes synced: {result.changes_detected}")
else:
    print(f"Sync failed: {result.error}")
```

#### `create_potenda(name=None, config_file=None, directory=None, branch=None, show_progress=True)`

Create a Potenda instance for advanced programmatic control.

**Parameters:**
- `name` (str, optional): Name of the sync project (mutually exclusive with config_file)
- `config_file` (str, optional): Path to sync configuration YAML file  
- `directory` (str, optional): Base directory to search for sync configurations
- `branch` (str, optional): Branch to use for operations
- `show_progress` (bool): Show progress bar during operations

**Returns:**
- `Potenda`: Potenda instance for direct method calls

**Raises:**
- `SyncError`: When parameters are invalid or initialization fails

**Example:**
```python
ptd = create_potenda(name="my-sync")
ptd.source_load()
ptd.destination_load()
diff_result = ptd.diff()
if diff_result.has_diffs():
    ptd.sync(diff=diff_result)
```

### Classes

#### `SyncResult`

Result object returned by sync operations.

**Attributes:**
- `success` (bool): Whether the operation succeeded
- `message` (str): Description of the result
- `duration` (float): Time taken for the operation in seconds
- `changes_detected` (bool): Whether changes were found/synced
- `error` (Exception): Exception object if operation failed

#### `SyncError`

Exception raised when sync operations fail.

Inherits from `Exception` and is raised when:
- Invalid parameters are provided
- Sync configuration cannot be loaded
- Source or destination loading fails
- Sync operations encounter errors

## Error Handling

Always wrap API calls in try-catch blocks to handle potential errors:

```python
from infrahub_sync.api import sync, SyncError

try:
    result = sync(name="my-sync")
    if result.success:
        print("Sync completed successfully")
    else:
        print(f"Sync failed: {result.error}")
except SyncError as e:
    print(f"Sync configuration or setup error: {e}")
except Exception as e:
    print(f"Unexpected error: {e}")
```

## Migration from CLI

Here's how common CLI commands translate to API calls:

| CLI Command | Python API Equivalent |
|-------------|----------------------|
| `infrahub-sync list` | `list_projects()` |
| `infrahub-sync diff --name my-sync` | `diff(name="my-sync")` |
| `infrahub-sync sync --name my-sync` | `sync(name="my-sync")` |
| `infrahub-sync diff --config-file config.yml` | `diff(config_file="config.yml")` |
| `infrahub-sync sync --branch feature --name my-sync` | `sync(name="my-sync", branch="feature")` |

## Examples

See the `examples/python_api_example.py` file in the repository for a complete working example that demonstrates all API functions.

## Integration Tips

### In Automation Scripts

```python
#!/usr/bin/env python3
from infrahub_sync.api import sync, SyncError
import sys

def main():
    try:
        result = sync(name="production-sync", show_progress=False)
        if result.success:
            if result.changes_detected:
                print(f"✅ Sync completed: {result.changes_detected} changes applied")
            else:
                print("✅ No changes needed")
            return 0
        else:
            print(f"❌ Sync failed: {result.error}")
            return 1
    except SyncError as e:
        print(f"❌ Configuration error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

### In Monitoring Systems

```python
from infrahub_sync.api import diff, SyncError
import time

def check_sync_drift(sync_name: str) -> dict:
    """Check if sync has drift and return monitoring data."""
    start_time = time.time()
    
    try:
        result = diff(name=sync_name, show_progress=False)
        return {
            "success": result.success,
            "drift_detected": result.changes_detected,
            "check_duration": time.time() - start_time,
            "message": result.message if result.changes_detected else "No drift detected"
        }
    except SyncError as e:
        return {
            "success": False,
            "error": str(e),
            "check_duration": time.time() - start_time
        }

# Use in monitoring loop
monitoring_data = check_sync_drift("critical-system-sync")
```

This Python API provides full programmatic access to all Infrahub Sync functionality while maintaining the same robustness and error handling as the CLI interface.