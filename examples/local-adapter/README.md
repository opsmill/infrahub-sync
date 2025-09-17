# Local Adapter Loading

This example demonstrates how to create and use a custom local adapter with infrahub-sync.

## Overview

As of version 1.2.1, infrahub-sync supports loading custom "local" adapters from Python files, in addition to the built-in adapters. This allows you to:

- Create completely custom adapters for systems not supported out of the box
- Extend or modify existing adapter behavior 
- Prototype new adapters before contributing them to the main project

## Directory Structure Options

Your local adapter can be placed in several different locations relative to your `config.yml`:

```
my-sync-project/
├── config.yml
├── my_custom_adapter.py           # Option 1: Direct file
├── my_custom_adapter_adapter.py   # Option 2: With "_adapter" suffix  
├── adapters/
│   └── my_custom_adapter.py       # Option 3: In "adapters" subdirectory
└── my_custom_adapter/
    └── adapter.py                 # Option 4: In dedicated directory
```

## Example Local Adapter

Here's a complete example of a custom adapter:

**my_custom_adapter.py:**
```python
"""Custom adapter for My Custom System."""
from typing import TYPE_CHECKING
from diffsync import Adapter, DiffSyncModel

if TYPE_CHECKING:
    from infrahub_sync import SyncAdapter

class Device(DiffSyncModel):
    """Device model for the custom system."""
    _modelname = "Device"
    _identifiers = ("name",)
    _attributes = ("ip_address", "vendor", "model")
    
    name: str
    ip_address: str | None = None
    vendor: str | None = None
    model: str | None = None

class MyCustomAdapterAdapter(Adapter):
    """Custom adapter for My Custom System."""
    
    # Register the models this adapter handles
    Device = Device
    
    def __init__(self, config=None, target=None, adapter: "SyncAdapter | None" = None, **kwargs):
        """Initialize the adapter."""
        super().__init__()
        self.config = config
        self.target = target
        self.adapter_config = adapter
        
        # Extract settings from adapter config
        self.settings = adapter.settings if adapter else {}
        
    def load(self):
        """Load data from the custom system."""
        # Example: Load from a file, database, API, etc.
        devices_data = [
            {"name": "router1", "ip_address": "192.168.1.1", "vendor": "Cisco", "model": "ISR4331"},
            {"name": "switch1", "ip_address": "192.168.1.2", "vendor": "Arista", "model": "7050SX3"},
        ]
        
        for device_data in devices_data:
            device = Device(**device_data)
            self.add(device)
```

**config.yml:**
```yaml
---
name: my-custom-sync
source:
  name: my_custom_adapter
  settings:
    api_url: "https://mycustomsystem.example.com/api"
    api_key: "${CUSTOM_SYSTEM_API_KEY}"
destination:
  name: infrahub
  settings:
    url: "http://localhost:8000"
    token: "${INFRAHUB_TOKEN}"
order:
  - Device
schema_mapping:
  - name: Device
    mapping: devices
```

## Class Naming Conventions

Your adapter class can use any of these naming patterns (infrahub-sync will try them all):

- `{AdapterName}Adapter` (recommended, e.g., `MyCustomAdapterAdapter`)
- `{AdapterName}` (e.g., `MyCustomAdapter`) 
- `{AdapterName}Sync` (e.g., `MyCustomAdapterSync`)

Where `{AdapterName}` is the CamelCase version of your adapter name. For example:
- `my_custom_adapter` → `MyCustomAdapter`
- `device42` → `Device42`
- `network_scanner` → `NetworkScanner`

## Running Your Custom Sync

Once you have your adapter file and config, you can use it like any other sync:

```bash
# List available syncs (should show your custom one)
infrahub-sync list --directory .

# Test with diff (safe, read-only)
infrahub-sync diff --name my-custom-sync

# Run the actual sync
infrahub-sync sync --name my-custom-sync
```

## Adapter Loading Priority

infrahub-sync uses this priority order when loading adapters:

1. **Built-in adapters** - From `infrahub_sync.adapters.*` (netbox, nautobot, etc.)
2. **Generated adapters** - From generated `{adapter_name}/sync_adapter.py` files
3. **Local adapters** - From your custom Python files

This means you can override built-in adapters by creating a local adapter with the same name.

## Best Practices

1. **Use descriptive names** - Make your adapter name clear and unique
2. **Handle errors gracefully** - Add try/catch blocks for API calls, file operations, etc.
3. **Support configuration** - Use the `settings` dictionary for configurable parameters
4. **Add logging** - Use Python's `logging` module for debugging
5. **Document your models** - Add docstrings to help others understand your data structures
6. **Test thoroughly** - Use `diff` command to verify data loading before running `sync`

## Environment Variables

You can use environment variables in your config.yml using `${VARIABLE_NAME}` syntax:

```bash
export CUSTOM_SYSTEM_API_KEY="your-secret-key"
export INFRAHUB_TOKEN="your-infrahub-token"
infrahub-sync diff --name my-custom-sync
```

## Contributing Back

If you create a useful adapter, consider contributing it back to the main project! See the [contributing guidelines](../CONTRIBUTING.md) for more information.