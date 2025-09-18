# Plugin Loader Demo

This example demonstrates the new plugin loader system in infrahub-sync, which provides flexible ways to load adapters from various sources.

## What's Demonstrated

- **Local adapter loading** from `adapters/` directory
- **Search path configuration** via `adapters_path`  
- **Dotted imports** for Python packages
- **Built-in adapter** usage (backward compatibility)
- **CLI options** for adapter paths
- **Environment variable** support

## Files

- `adapters/my_network.py` - Custom network monitoring adapter
- `config.yml` - Configuration using search paths
- `config-dotted.yml` - Configuration using dotted imports  
- `demo.sh` - Demo script explaining the features

## Custom Adapter

The example includes `MyNetworkAdapter` which simulates loading network device data:

```python
class NetworkDevice(DiffSyncModel):
    _modelname = "NetworkDevice"
    _identifiers = ("hostname",)
    _attributes = ("ip_address", "device_type", "location", "vendor")

class MyNetworkAdapter(Adapter):
    NetworkDevice = NetworkDevice
    
    def load(self):
        # Load from custom system
        devices = fetch_from_api()
        for device in devices:
            self.add(NetworkDevice(**device))
```

## Configuration Examples

### Using Search Paths (Recommended)
```yaml
adapters_path:
  - "./adapters"
source:
  adapter: "my_network"  # Resolved from adapters_path
```

### Using Explicit File Paths
```yaml
source:
  adapter: "./adapters/my_network.py:MyNetworkAdapter"
```

### Using Dotted Imports
```yaml
source:
  adapter: "adapters.my_network:MyNetworkAdapter"
```

### Using Built-in Adapters (Legacy)
```yaml
source:
  name: "netbox"  # Uses infrahub_sync.adapters.netbox
```

## Usage

```bash
# List configurations
infrahub-sync list --directory .

# Test adapter loading (requires Infrahub server)
INFRAHUB_TOKEN="your-token" infrahub-sync diff --name plugin-loader-demo

# Use CLI adapter paths
infrahub-sync diff --name plugin-loader-demo --adapter-path ./adapters

# Use environment variables
INFRAHUB_SYNC_ADAPTER_PATHS="./adapters:../shared" infrahub-sync diff --name plugin-loader-demo
```

## Key Benefits

1. **Flexibility** - Load adapters from any source
2. **Backward Compatibility** - Existing configs work unchanged
3. **Easy Development** - Simple local adapter development
4. **Package Support** - Import from installed Python packages
5. **Dynamic Loading** - Generated files work with any adapter source