#!/usr/bin/env python3
"""
Example script showing how to use the GenericRestApiAdapter.

This script demonstrates configuring and using the generic adapter
to connect to different types of REST APIs.
"""

from infrahub_sync import SyncAdapter, SyncConfig
from infrahub_sync.adapters.generic_rest_api import GenericRestApiAdapter, GenericRestApiModel


def example_librenms_style():
    """Example configuration for LibreNMS-style API."""
    print("=== LibreNMS-style Configuration ===")
    
    # Configuration similar to LibreNMS
    settings = {
        "url": "https://demo.librenms.org",
        "auth_method": "x-auth-token",
        "token": "demo_token",
        "api_endpoint": "/api/v0",
        "url_env_vars": ["LIBRENMS_URL", "LIBRENMS_ADDRESS"],
        "token_env_vars": ["LIBRENMS_TOKEN"],
    }
    
    adapter_config = SyncAdapter(name="librenms_generic", settings=settings)
    sync_config = SyncConfig(
        name="test_sync",
        source=SyncAdapter(name="LibreNMS"),
        destination=SyncAdapter(name="Infrahub"),
        schema_mapping=[]
    )
    
    try:
        # Create the generic adapter with LibreNMS-like configuration
        adapter = GenericRestApiAdapter(
            target="test",
            adapter=adapter_config,
            config=sync_config,
            adapter_type="LibreNMS"  # Optional: set custom type name
        )
        print(f"✓ Created adapter with type: {adapter.type}")
        print(f"✓ Client configured for: {settings['url']}")
        print(f"✓ Authentication method: {settings['auth_method']}")
        
    except Exception as e:
        print(f"✗ Error: {e}")


def example_observium_style():
    """Example configuration for Observium-style API."""
    print("\n=== Observium-style Configuration ===")
    
    # Configuration similar to Observium  
    settings = {
        "url": "https://demo.observium.org",
        "auth_method": "basic",
        "username": "demo_user",
        "password": "demo_pass",
        "api_endpoint": "/api/v0",
        "username_env_vars": ["OBSERVIUM_USERNAME"],
        "password_env_vars": ["OBSERVIUM_PASSWORD"],
    }
    
    adapter_config = SyncAdapter(name="observium_generic", settings=settings)
    sync_config = SyncConfig(
        name="test_sync",
        source=SyncAdapter(name="Observium"),
        destination=SyncAdapter(name="Infrahub"),
        schema_mapping=[]
    )
    
    try:
        # Create the generic adapter with Observium-like configuration
        adapter = GenericRestApiAdapter(
            target="test",
            adapter=adapter_config,
            config=sync_config,
            adapter_type="Observium"  # Optional: set custom type name
        )
        print(f"✓ Created adapter with type: {adapter.type}")
        print(f"✓ Client configured for: {settings['url']}")
        print(f"✓ Authentication method: {settings['auth_method']}")
        
    except Exception as e:
        print(f"✗ Error: {e}")


def example_custom_tool():
    """Example configuration for a custom tool."""
    print("\n=== Custom Tool Configuration ===")
    
    # Configuration for a hypothetical custom monitoring tool
    settings = {
        "url": "https://api.customtool.example.com",
        "auth_method": "api-key",
        "token": "custom_api_key_123",
        "api_endpoint": "/rest/v2",
        "timeout": 60,
        "verify_ssl": True,
        "url_env_vars": ["CUSTOM_TOOL_URL"],
        "token_env_vars": ["CUSTOM_TOOL_API_KEY", "CUSTOM_TOOL_TOKEN"],
        "params": {
            "format": "json",
            "limit": 100
        }
    }
    
    adapter_config = SyncAdapter(name="custom_tool", settings=settings)
    sync_config = SyncConfig(
        name="test_sync",
        source=SyncAdapter(name="CustomTool"),
        destination=SyncAdapter(name="Infrahub"),
        schema_mapping=[]
    )
    
    try:
        # Create the generic adapter for custom tool
        adapter = GenericRestApiAdapter(
            target="test",
            adapter=adapter_config,
            config=sync_config,
            adapter_type="CustomMonitoringTool"
        )
        print(f"✓ Created adapter with type: {adapter.type}")
        print(f"✓ Client configured for: {settings['url']}")
        print(f"✓ Authentication method: {settings['auth_method']}")
        print(f"✓ Extra parameters: {settings['params']}")
        
    except Exception as e:
        print(f"✗ Error: {e}")


def example_response_extraction():
    """Example showing response data extraction."""
    print("\n=== Response Data Extraction Example ===")
    
    settings = {
        "url": "https://api.example.com",
        "auth_method": "token",
        "token": "test_token"
    }
    
    adapter_config = SyncAdapter(name="test_adapter", settings=settings)
    sync_config = SyncConfig(
        name="test_sync",
        source=SyncAdapter(name="TestAPI"),
        destination=SyncAdapter(name="Infrahub"),
        schema_mapping=[]
    )
    
    try:
        adapter = GenericRestApiAdapter(
            target="test",
            adapter=adapter_config,
            config=sync_config
        )
        
        # Example response data in different formats
        
        # Format 1: List response (like LibreNMS)
        response_1 = {"devices": [{"id": 1, "name": "device1"}, {"id": 2, "name": "device2"}]}
        result_1 = adapter._extract_objects_from_response(response_1, "devices", None)
        print(f"✓ List format extraction: {len(result_1)} objects")
        
        # Format 2: Dict response (like Observium)
        response_2 = {"devices": {"1": {"id": 1, "name": "device1"}, "2": {"id": 2, "name": "device2"}}}
        result_2 = adapter._extract_objects_from_response(response_2, "devices", None)
        print(f"✓ Dict format extraction: {len(result_2)} objects")
        
        # Format 3: Direct response
        response_3 = {"device_data": [{"id": 1, "name": "device1"}]}
        result_3 = adapter._extract_objects_from_response(response_3, "device_data", None)
        print(f"✓ Direct format extraction: {len(result_3)} objects")
        
    except Exception as e:
        print(f"✗ Error: {e}")


if __name__ == "__main__":
    print("GenericRestApiAdapter Examples")
    print("=" * 50)
    
    # Run all examples
    example_librenms_style()
    example_observium_style()
    example_custom_tool()
    example_response_extraction()
    
    print("\n" + "=" * 50)
    print("All examples completed!")
    print("\nThe GenericRestApiAdapter provides:")
    print("• Flexible authentication (token, basic, api-key, etc.)")
    print("• Configurable environment variables")
    print("• Multiple response format handling")
    print("• Customizable adapter types")
    print("• Backward compatibility with existing patterns")