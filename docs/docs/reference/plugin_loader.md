# Plugin Loader

The plugin loader system allows loading adapters from various sources:

## Available Sources

1. **Built-in adapters**: `infrahub_sync.adapters.<name>`
2. **Dotted paths**: `myproj.adapters.foo:MyAdapter`
3. **Filesystem paths**: `./adapters/foo.py:MyAdapter` or a package directory
4. **Python entry points**: group `infrahub_sync.adapters`

## Configuration

You can configure adapter paths in several ways:

### In `config.yml`

```yaml
name: my-sync
adapters_path:
  - ./adapters
  - ../shared/adapters
source:
  name: custom-source
  adapter: ./adapters/my_adapter.py:MyCustomAdapter
  settings:
    url: "http://localhost:8000"
destination:
  name: infrahub
  settings:
    url: "http://localhost:8000"
```

### Environment Variable

```shell
# Unix/Linux/macOS
export INFRAHUB_SYNC_ADAPTER_PATHS=./adapters:/shared/adapters

# Windows
set INFRAHUB_SYNC_ADAPTER_PATHS=./adapters;/shared/adapters
```

### CLI Flag

```shell
infrahub-sync generate --adapter-path ./adapters --adapter-path ../shared/adapters
```

## Using the Plugin Loader in Your Code

```python
from infrahub_sync.plugin_loader import PluginLoader

# Create a loader with paths from environment and CLI
loader = PluginLoader.from_env_and_args(['./adapters'])

# Resolve an adapter class
adapter_class = loader.resolve('custom-adapter')

# Resolve with explicit class name
adapter_class = loader.resolve('mymodule.adapters:CustomAdapter')

# Resolve a model class
model_class = loader.resolve('custom-adapter', default_class_candidates=('Model',))
```

## Resolution Order

The plugin loader resolves adapter classes in the following order:

1. Check cache for previous resolutions
2. Explicit dotted path: `pkg.mod[:Class]`
3. Filesystem path: `path.py[:Class]` or `dir[:Class]`
4. Entry point: group `infrahub_sync.adapters`, by name
5. Built-in: `infrahub_sync.adapters.<name>`

## Class Name Resolution

If no class name is specified in the spec, the plugin loader tries to infer it:

1. For adapter `netbox`, tries: `NetboxAdapter`, `NetboxModel` (if `Model` in candidates)
2. For adapter `generic-rest-api`, tries: `GenericRestApiAdapter`

This applies "camelizing" to handle hyphenated or underscored names.