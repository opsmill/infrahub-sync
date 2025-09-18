# Plugin Loader System

The new plugin loader system provides flexible ways to load adapters from various sources while maintaining backward compatibility.

## Adapter Resolution Order

The plugin loader tries to resolve adapters in this order:

1. **Explicit dotted paths**: `pkg.mod[:Class]`
2. **Filesystem paths**: `./adapters/foo.py[:Class]` or package directories  
3. **Python entry points**: group `infrahub_sync.adapters`
4. **Built-in adapters**: `infrahub_sync.adapters.<name>` with automatic class name resolution

## Configuration Examples

### Backward Compatible (Legacy)

```yaml
name: my-sync
source:
  name: netbox  # Uses built-in NetboxAdapter
  settings:
    url: "https://netbox.example.com"
destination:
  name: infrahub
  settings:
    url: "http://localhost:8000"
```

### New Adapter Specification Formats

#### Local File with Explicit Class
```yaml
name: my-sync
source:
  adapter: "./adapters/custom_netbox.py:CustomNetboxAdapter"
  settings:
    url: "https://netbox.example.com"
```

#### Local File with Auto-Detection
```yaml 
name: my-sync
source:
  adapter: "./adapters/custom_netbox.py"  # Finds class ending in "Adapter"
  settings:
    url: "https://netbox.example.com"
```

#### Dotted Python Import
```yaml
name: my-sync
source:
  adapter: "myproject.adapters.netbox:CustomNetboxAdapter"
  settings:
    url: "https://netbox.example.com"
```

#### Package Directory
```yaml
name: my-sync
source:
  adapter: "./adapters/custom_netbox/"  # Looks for adapter.py or __init__.py
  settings:
    url: "https://netbox.example.com"
```

## Search Paths Configuration

### In Configuration File
```yaml
name: my-sync
adapters_path:
  - "./adapters"
  - "../shared/adapters"
source:
  adapter: "my_custom_adapter"  # Will search in adapters_path
```

### CLI Option
```bash
infrahub-sync diff --name my-sync --adapter-path ./adapters --adapter-path ../shared
```

### Environment Variable
```bash
export INFRAHUB_SYNC_ADAPTER_PATHS="./adapters:../shared/adapters"
infrahub-sync diff --name my-sync
```

## Priority Order for Paths
1. Environment variable `INFRAHUB_SYNC_ADAPTER_PATHS`
2. CLI `--adapter-path` options
3. Configuration file `adapters_path`

## Custom Adapter Examples

### Simple Local Adapter
**File: `adapters/my_system.py`**
```python
"""Custom adapter for My System."""
from diffsync import Adapter, DiffSyncModel

class Device(DiffSyncModel):
    _modelname = "Device"
    _identifiers = ("name",)
    _attributes = ("ip_address", "status")
    
    name: str
    ip_address: str | None = None
    status: str = "active"

class MySystemAdapter(Adapter):
    """Custom adapter for My System."""
    
    Device = Device
    
    def __init__(self, config=None, target=None, adapter=None, **kwargs):
        super().__init__()
        self.settings = adapter.settings if adapter else {}
        
    def load(self):
        # Load data from your custom system
        devices = self._fetch_devices()
        for device_data in devices:
            self.add(Device(**device_data))
    
    def _fetch_devices(self):
        # Your custom data loading logic
        return [
            {"name": "router1", "ip_address": "10.0.1.1", "status": "active"},
            {"name": "switch1", "ip_address": "10.0.1.2", "status": "active"},
        ]
```

**Configuration:**
```yaml
name: my-system-sync
adapters_path:
  - "./adapters"
source:
  adapter: "my_system"  # Resolves to MySystemAdapter
  settings: {}
destination:
  name: infrahub
  settings:
    url: "http://localhost:8000"
```

### Package-Style Adapter
**Directory structure:**
```
adapters/
└── complex_system/
    ├── __init__.py
    ├── adapter.py  # Contains the adapter class
    ├── models.py   # Contains DiffSync models
    └── client.py   # API client code
```

**File: `adapters/complex_system/adapter.py`**
```python
"""Complex system adapter."""
from diffsync import Adapter
from .models import Device, Interface
from .client import ComplexSystemClient

class ComplexSystemAdapter(Adapter):
    Device = Device
    Interface = Interface
    
    def __init__(self, config=None, target=None, adapter=None, **kwargs):
        super().__init__()
        self.client = ComplexSystemClient(adapter.settings)
        
    def load(self):
        devices = self.client.get_devices()
        for device in devices:
            self.add(Device(**device))
```

**Configuration:**
```yaml
name: complex-sync
source:
  adapter: "./adapters/complex_system/"  # Loads from adapter.py
  settings:
    api_url: "https://api.example.com"
```

### Entry Point Adapter
**In your package's `pyproject.toml`:**
```toml
[project.entry-points."infrahub_sync.adapters"]
my_adapter = "mypackage.adapters:MyAdapter"
```

**Configuration:**
```yaml
name: entry-point-sync  
source:
  adapter: "my_adapter"  # Resolved via entry points
  settings: {}
```

## Error Handling

The plugin loader provides detailed error messages when adapters cannot be loaded:

```
PluginLoadError: Could not resolve plugin 'my_missing_adapter'. Tried:
  - Dotted import: No module named 'my_missing_adapter'
  - Filesystem import: File not found: ./my_missing_adapter.py
  - Entry point: No entry point named 'my_missing_adapter'
  - Built-in adapter: No module named 'infrahub_sync.adapters.my_missing_adapter'
```

## Generated Files

With the plugin loader system, generated `sync_adapter.py` files now use dynamic loading:

```python
from infrahub_sync.plugin_loader import get_loader

# Load the adapter class dynamically
_loader = get_loader(['./adapters'])
_AdapterClass = _loader.resolve("my_custom_adapter")

class MyCustomAdapterSync(_AdapterClass):
    Device = Device
    Interface = Interface
```

This ensures that generated files work regardless of where the adapter comes from.