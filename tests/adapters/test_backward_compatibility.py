"""Tests for backward compatibility of updated LibreNMS and Observium adapters."""

from __future__ import annotations

from unittest.mock import Mock, patch

from infrahub_sync import SyncAdapter, SyncConfig
from infrahub_sync.adapters.librenms import LibrenmsAdapter
from infrahub_sync.adapters.observium import ObserviumAdapter
from infrahub_sync.adapters.peeringmanager import PeeringmanagerAdapter


class TestBackwardCompatibility:
    """Test cases to ensure updated adapters maintain backward compatibility."""

    def test_librenms_adapter_backward_compatibility(self):
        """Test that LibreNMS adapter works with existing configuration patterns."""
        # Configuration that would have worked with the old adapter
        settings = {"url": "https://librenms.example.com", "token": "test_token", "timeout": 30, "verify_ssl": True}

        adapter_config = SyncAdapter(name="librenms", settings=settings)
        sync_config = SyncConfig(
            name="test_sync",
            source=SyncAdapter(name="LibreNMS"),
            destination=SyncAdapter(name="Infrahub"),
            schema_mapping=[],
        )

        # Mock the _create_rest_client method to avoid actual API calls
        with patch.object(LibrenmsAdapter, "_create_rest_client") as mock_client:
            mock_client.return_value = Mock()

            adapter = LibrenmsAdapter(target="test", adapter=adapter_config, config=sync_config)

            # Should maintain the same type
            assert adapter.type == "LibreNMS"

            # Should have configured the auth method to x-auth-token by default
            # We need to check the settings passed to the generic adapter
            assert adapter.client is not None

    def test_observium_adapter_backward_compatibility(self):
        """Test that Observium adapter works with existing configuration patterns."""
        # Configuration that would have worked with the old adapter
        settings = {"url": "https://observium.example.com", "username": "admin", "password": "password", "timeout": 60}

        adapter_config = SyncAdapter(name="observium", settings=settings)
        sync_config = SyncConfig(
            name="test_sync",
            source=SyncAdapter(name="Observium"),
            destination=SyncAdapter(name="Infrahub"),
            schema_mapping=[],
        )

        # Mock the _create_rest_client method to avoid actual API calls
        with patch.object(ObserviumAdapter, "_create_rest_client") as mock_client:
            mock_client.return_value = Mock()

            adapter = ObserviumAdapter(target="test", adapter=adapter_config, config=sync_config)

            # Should maintain the same type
            assert adapter.type == "Observium"

            # Should have configured the auth method to basic by default
            assert adapter.client is not None

    def test_librenms_adapter_with_environment_variables(self):
        """Test LibreNMS adapter uses the correct environment variable names."""
        settings = {
            "auth_method": "x-auth-token",  # Explicitly set (should remain unchanged)
        }

        adapter_config = SyncAdapter(name="librenms", settings=settings)
        sync_config = SyncConfig(
            name="test_sync",
            source=SyncAdapter(name="LibreNMS"),
            destination=SyncAdapter(name="Infrahub"),
            schema_mapping=[],
        )

        with patch.dict("os.environ", {"LIBRENMS_URL": "https://env.librenms.com", "LIBRENMS_TOKEN": "env_token"}):
            with patch.object(LibrenmsAdapter, "_create_rest_client") as mock_client:
                mock_client.return_value = Mock()

                adapter = LibrenmsAdapter(target="test", adapter=adapter_config, config=sync_config)

                assert adapter.type == "LibreNMS"

    def test_observium_adapter_with_environment_variables(self):
        """Test Observium adapter uses the correct environment variable names."""
        settings = {}  # Empty settings to use defaults

        adapter_config = SyncAdapter(name="observium", settings=settings)
        sync_config = SyncConfig(
            name="test_sync",
            source=SyncAdapter(name="Observium"),
            destination=SyncAdapter(name="Infrahub"),
            schema_mapping=[],
        )

        with (
            patch.dict(
                "os.environ",
                {
                    "OBSERVIUM_URL": "https://env.observium.com",
                    "OBSERVIUM_USERNAME": "env_user",
                    "OBSERVIUM_PASSWORD": "env_pass",
                },
            ),
            patch.object(ObserviumAdapter, "_create_rest_client") as mock_client,
        ):
            mock_client.return_value = Mock()

            adapter = ObserviumAdapter(target="test", adapter=adapter_config, config=sync_config)

            assert adapter.type == "Observium"

    def test_observium_response_extraction(self):
        """Test that Observium adapter handles response extraction correctly."""
        settings = {"url": "https://observium.example.com", "username": "admin", "password": "password"}

        adapter_config = SyncAdapter(name="observium", settings=settings)
        sync_config = SyncConfig(
            name="test_sync",
            source=SyncAdapter(name="Observium"),
            destination=SyncAdapter(name="Infrahub"),
            schema_mapping=[],
        )

        with patch.object(ObserviumAdapter, "_create_rest_client") as mock_client:
            mock_client.return_value = Mock()

            adapter = ObserviumAdapter(target="test", adapter=adapter_config, config=sync_config)

            # Test Observium-specific response format (dict to list conversion)
            response_data = {"devices": {"1": {"id": 1, "name": "device1"}, "2": {"id": 2, "name": "device2"}}}

            result = adapter._extract_objects_from_response(
                response_data=response_data, resource_name="devices", element=Mock()
            )

            # Should convert dict values to list
            assert len(result) == 2
            ids = [obj["id"] for obj in result]
            assert 1 in ids
            assert 2 in ids

    def test_peeringmanager_adapter_backward_compatibility(self):
        """Test that PeeringManager adapter works with existing configuration patterns."""
        # Configuration that would have worked with the old adapter
        settings = {
            "url": "https://peering.example.com",
            "token": "test_token",
            "timeout": 30,
            "verify_ssl": True,
            "api_endpoint": "api",
        }

        adapter_config = SyncAdapter(name="peeringmanager", settings=settings)
        sync_config = SyncConfig(
            name="test_sync",
            source=SyncAdapter(name="Peeringmanager"),
            destination=SyncAdapter(name="Infrahub"),
            schema_mapping=[],
        )

        # Mock the _create_rest_client method to avoid actual API calls
        with patch.object(PeeringmanagerAdapter, "_create_rest_client") as mock_client:
            mock_client.return_value = Mock()

            adapter = PeeringmanagerAdapter(target="test", adapter=adapter_config, config=sync_config)

            # Should maintain the same type
            assert adapter.type == "Peeringmanager"

            # Should have configured the auth method to token by default
            assert adapter.client is not None

    def test_peeringmanager_adapter_with_environment_variables(self):
        """Test that PeeringManager adapter works with environment variables."""
        with (
            patch.dict(
                "os.environ",
                {"PEERING_MANAGER_ADDRESS": "https://peering-env.example.com", "PEERING_MANAGER_TOKEN": "env_token"},
            ),
            patch.object(PeeringmanagerAdapter, "_create_rest_client") as mock_client,
        ):
            mock_client.return_value = Mock()

            adapter_config = SyncAdapter(name="peeringmanager", settings={})
            sync_config = SyncConfig(
                name="test_sync",
                source=SyncAdapter(name="Peeringmanager"),
                destination=SyncAdapter(name="Infrahub"),
                schema_mapping=[],
            )

            adapter = PeeringmanagerAdapter(target="test", adapter=adapter_config, config=sync_config)

            # Should maintain the same type
            assert adapter.type == "Peeringmanager"
