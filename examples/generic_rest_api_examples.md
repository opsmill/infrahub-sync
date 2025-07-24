# Generic REST API Adapter Configuration Examples

This document provides examples of how to configure the `GenericRestApiAdapter` for various tools and APIs.

## Basic Configuration Example

```yaml
# Example configuration for a generic REST API
adapters:
  - name: "generic_tool"
    source: "GenericRestApi"
    settings:
      url: "https://api.example.com"
      auth_method: "token"
      token: "your_api_token_here"
      api_endpoint: "/api/v1"
      timeout: 30
      verify_ssl: true
      params:
        format: "json"
        limit: 100
```

## Advanced Configuration with Environment Variables

```yaml
# Example using environment variables for credentials
adapters:
  - name: "custom_monitoring_tool"
    source: "GenericRestApi"
    adapter_type: "CustomMonitoring"  # Optional: customize the adapter type name
    settings:
      # Define which environment variables to check for URL
      url_env_vars: ["CUSTOM_TOOL_URL", "MONITORING_API_URL"]
      # Define which environment variables to check for token
      token_env_vars: ["CUSTOM_TOOL_TOKEN", "MONITORING_API_TOKEN"]
      
      # Fallback values if env vars are not set
      url: "https://monitoring.example.com"
      
      auth_method: "x-auth-token"  # Use LibreNMS-style auth
      api_endpoint: "/api/v2"
      timeout: 60
      verify_ssl: false
```

## Basic Authentication Example

```yaml
# Example using basic authentication (like Observium)
adapters:
  - name: "legacy_system"
    source: "GenericRestApi" 
    adapter_type: "LegacySystem"
    settings:
      url: "https://legacy.example.com"
      auth_method: "basic"
      username_env_vars: ["LEGACY_USERNAME"]
      password_env_vars: ["LEGACY_PASSWORD"]
      api_endpoint: "/rest/api"
```

## Multiple Authentication Methods

```yaml
# Example showing different auth methods supported
adapters:
  # Token-based (default)
  - name: "tool_with_token"
    source: "GenericRestApi"
    settings:
      url: "https://api1.example.com"
      auth_method: "token"
      token: "Bearer token_value"

  # API Key style (PeeringDB)
  - name: "tool_with_api_key" 
    source: "GenericRestApi"
    settings:
      url: "https://api2.example.com"
      auth_method: "api-key"
      token: "api_key_value"

  # X-Auth-Token style (LibreNMS)
  - name: "tool_with_x_auth"
    source: "GenericRestApi"
    settings:
      url: "https://api3.example.com"
      auth_method: "x-auth-token"
      token: "auth_token_value"

  # Key style (RIPE API)
  - name: "tool_with_key"
    source: "GenericRestApi"
    settings:
      url: "https://api4.example.com"
      auth_method: "key"
      token: "key_value"
```

## Environment Variables

The following environment variables can be used (customizable via settings):

### Default Environment Variable Names:
- `URL` or `ADDRESS` - API base URL
- `TOKEN` - API token/key
- `USERNAME` - Username for basic auth
- `PASSWORD` - Password for basic auth

### Custom Environment Variable Names:
You can specify custom environment variable names in the settings:

```yaml
settings:
  url_env_vars: ["MY_TOOL_URL", "MY_TOOL_ADDRESS"]
  token_env_vars: ["MY_TOOL_API_KEY", "MY_TOOL_TOKEN"] 
  username_env_vars: ["MY_TOOL_USER", "MY_TOOL_USERNAME"]
  password_env_vars: ["MY_TOOL_PASS", "MY_TOOL_PASSWORD"]
```

## Migration from Existing Adapters

### From LibreNMS Adapter:
```yaml
# Old LibreNMS configuration
adapters:
  - name: "librenms"
    source: "LibreNMS"
    settings:
      url: "https://librenms.example.com"
      token: "librenms_token"

# New Generic configuration (equivalent)
adapters:
  - name: "librenms_generic"
    source: "GenericRestApi"
    adapter_type: "LibreNMS"  # Optional: maintain the same type name
    settings:
      url: "https://librenms.example.com"
      auth_method: "x-auth-token"
      token: "librenms_token"
      api_endpoint: "/api/v0"
```

### From Observium Adapter:
```yaml
# Old Observium configuration 
adapters:
  - name: "observium"
    source: "Observium"
    settings:
      url: "https://observium.example.com"
      username: "admin"
      password: "password"

# New Generic configuration (equivalent)
adapters:
  - name: "observium_generic"
    source: "GenericRestApi"
    adapter_type: "Observium"  # Optional: maintain the same type name
    settings:
      url: "https://observium.example.com"
      auth_method: "basic"
      username: "admin"
      password: "password"
      api_endpoint: "/api/v0"
```

## Schema Mapping

The generic adapter works with the same schema mapping structure as other adapters:

```yaml
schema_mapping:
  - name: "Device"
    mapping: "devices"  # API endpoint path
    fields:
      - name: "name"
        mapping: "hostname"
      - name: "ip_address"
        mapping: "ip"
      - name: "status"
        static: "active"
```

This flexibility allows the `GenericRestApiAdapter` to work with any REST API that follows common patterns, reducing the need for tool-specific adapters.