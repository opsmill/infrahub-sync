"""Tests for the generic REST API adapter."""
from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pytest

from infrahub_sync import SyncAdapter, SyncConfig
from infrahub_sync.adapters.generic_rest_api import GenericRestApiAdapter


class TestGenericRestApiAdapter:
    """Test cases for GenericRestApiAdapter."""

    def test_init_with_defaults(self):
        """Test adapter initialization with default configuration."""
        # Mock the dependencies
        target = "test_target"
        adapter = SyncAdapter(name="test", settings={})
        config = Mock(spec=SyncConfig)
        
        # Mock the _create_rest_client method to avoid actual API calls
        with patch.object(GenericRestApiAdapter, '_create_rest_client') as mock_client:
            mock_client.return_value = Mock()
            
            generic_adapter = GenericRestApiAdapter(
                target=target,
                adapter=adapter,
                config=config
            )
            
            assert generic_adapter.target == target
            assert generic_adapter.type == "GenericRestApi"
            assert generic_adapter.params == {}
            assert generic_adapter.config == config

    def test_init_with_custom_type(self):
        """Test adapter initialization with custom adapter type."""
        target = "test_target"
        adapter = SyncAdapter(name="test", settings={})
        config = Mock(spec=SyncConfig)
        custom_type = "CustomTool"
        
        with patch.object(GenericRestApiAdapter, '_create_rest_client') as mock_client:
            mock_client.return_value = Mock()
            
            generic_adapter = GenericRestApiAdapter(
                target=target,
                adapter=adapter,
                config=config,
                adapter_type=custom_type
            )
            
            assert generic_adapter.type == custom_type

    def test_create_rest_client_token_auth(self):
        """Test REST client creation with token authentication."""
        settings = {
            "url": "https://api.example.com",
            "auth_method": "token",
            "token": "test_token",
            "api_endpoint": "/api/v1"
        }
        
        adapter = SyncAdapter(name="test", settings=settings)
        config = Mock(spec=SyncConfig)
        
        generic_adapter = GenericRestApiAdapter(
            target="test",
            adapter=adapter,
            config=config
        )
        
        # Verify client was created (we can't easily test the internal state without exposing it)
        assert generic_adapter.client is not None

    def test_create_rest_client_basic_auth(self):
        """Test REST client creation with basic authentication."""
        settings = {
            "url": "https://api.example.com",
            "auth_method": "basic",
            "username": "testuser",
            "password": "testpass"
        }
        
        adapter = SyncAdapter(name="test", settings=settings)
        config = Mock(spec=SyncConfig)
        
        generic_adapter = GenericRestApiAdapter(
            target="test",
            adapter=adapter,
            config=config
        )
        
        assert generic_adapter.client is not None

    def test_create_rest_client_env_vars(self):
        """Test REST client creation using environment variables."""
        settings = {
            "auth_method": "token",
            "url_env_vars": ["TEST_URL"],
            "token_env_vars": ["TEST_TOKEN"]
        }
        
        with patch.dict(os.environ, {"TEST_URL": "https://env.example.com", "TEST_TOKEN": "env_token"}):
            adapter = SyncAdapter(name="test", settings=settings)
            config = Mock(spec=SyncConfig)
            
            generic_adapter = GenericRestApiAdapter(
                target="test",
                adapter=adapter,
                config=config
            )
            
            assert generic_adapter.client is not None

    def test_create_rest_client_missing_url(self):
        """Test that missing URL raises ValueError."""
        settings = {
            "auth_method": "token",
            "token": "test_token"
        }
        
        adapter = SyncAdapter(name="test", settings=settings)
        config = Mock(spec=SyncConfig)
        
        with pytest.raises(ValueError, match="url must be specified"):
            GenericRestApiAdapter(
                target="test",
                adapter=adapter,
                config=config
            )

    def test_create_rest_client_missing_token(self):
        """Test that missing token for token auth raises ValueError."""
        settings = {
            "url": "https://api.example.com",
            "auth_method": "token"
        }
        
        adapter = SyncAdapter(name="test", settings=settings)
        config = Mock(spec=SyncConfig)
        
        with pytest.raises(ValueError, match="Authentication method 'token' requires a valid API token"):
            GenericRestApiAdapter(
                target="test",
                adapter=adapter,
                config=config
            )

    def test_create_rest_client_missing_basic_auth(self):
        """Test that missing username/password for basic auth raises ValueError."""
        settings = {
            "url": "https://api.example.com",
            "auth_method": "basic",
            "username": "testuser"
            # Missing password
        }
        
        adapter = SyncAdapter(name="test", settings=settings)
        config = Mock(spec=SyncConfig)
        
        with pytest.raises(ValueError, match="Basic authentication requires both username and password"):
            GenericRestApiAdapter(
                target="test",
                adapter=adapter,
                config=config
            )

    def test_extract_objects_from_response_list(self):
        """Test extracting objects when response contains a list."""
        settings = {
            "url": "https://api.example.com",
            "auth_method": "token",
            "token": "test_token"
        }
        
        adapter = SyncAdapter(name="test", settings=settings)
        config = Mock(spec=SyncConfig)
        
        generic_adapter = GenericRestApiAdapter(
            target="test",
            adapter=adapter,
            config=config
        )
        
        response_data = {"devices": [{"id": 1, "name": "device1"}, {"id": 2, "name": "device2"}]}
        resource_name = "devices"
        element = Mock()
        
        result = generic_adapter._extract_objects_from_response(
            response_data=response_data,
            resource_name=resource_name,
            element=element
        )
        
        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[1]["id"] == 2

    def test_extract_objects_from_response_dict(self):
        """Test extracting objects when response contains a dict."""
        settings = {
            "url": "https://api.example.com",
            "auth_method": "token",
            "token": "test_token"
        }
        
        adapter = SyncAdapter(name="test", settings=settings)
        config = Mock(spec=SyncConfig)
        
        generic_adapter = GenericRestApiAdapter(
            target="test",
            adapter=adapter,
            config=config
        )
        
        response_data = {
            "devices": {
                "1": {"id": 1, "name": "device1"}, 
                "2": {"id": 2, "name": "device2"}
            }
        }
        resource_name = "devices"
        element = Mock()
        
        result = generic_adapter._extract_objects_from_response(
            response_data=response_data,
            resource_name=resource_name,
            element=element
        )
        
        assert len(result) == 2
        # Order might vary since it's from dict.values()
        ids = [obj["id"] for obj in result]
        assert 1 in ids
        assert 2 in ids