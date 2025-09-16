"""Tests for the Infrahub Sync Python API."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from infrahub_sync.api import (
    SyncError,
    SyncResult,
    list_projects,
    diff,
    sync,
    create_potenda,
)
from infrahub_sync import SyncInstance, SyncAdapter


class TestPythonAPI:
    """Test cases for the Python API."""

    def test_sync_result_initialization(self):
        """Test SyncResult object initialization."""
        result = SyncResult(
            success=True,
            message="Test completed",
            duration=1.5,
            changes_detected=True
        )
        
        assert result.success is True
        assert result.message == "Test completed"
        assert result.duration == 1.5
        assert result.changes_detected is True
        assert result.error is None
        
    def test_sync_result_repr(self):
        """Test SyncResult string representation."""
        result = SyncResult(success=False, message="Error occurred")
        expected = "SyncResult(success=False, message='Error occurred', duration=None)"
        assert repr(result) == expected

    @patch('infrahub_sync.api.get_all_sync')
    def test_list_projects(self, mock_get_all_sync):
        """Test list_projects function."""
        # Mock return value
        mock_sync_instance = Mock(spec=SyncInstance)
        mock_sync_instance.name = "test-sync"
        mock_get_all_sync.return_value = [mock_sync_instance]
        
        # Call the function
        result = list_projects()
        
        # Verify
        mock_get_all_sync.assert_called_once_with(directory=None)
        assert len(result) == 1
        assert result[0].name == "test-sync"
        
    @patch('infrahub_sync.api.get_all_sync')
    def test_list_projects_with_directory(self, mock_get_all_sync):
        """Test list_projects function with directory parameter."""
        mock_get_all_sync.return_value = []
        
        result = list_projects(directory="/custom/path")
        
        mock_get_all_sync.assert_called_once_with(directory="/custom/path")
        assert result == []

    def test_diff_invalid_args(self):
        """Test diff function with invalid arguments."""
        # Test with no arguments
        with pytest.raises(SyncError, match="Please specify exactly one of 'name' or 'config_file'"):
            diff()
            
        # Test with both arguments
        with pytest.raises(SyncError, match="Please specify exactly one of 'name' or 'config_file'"):
            diff(name="test", config_file="config.yml")

    @patch('infrahub_sync.api.get_instance')
    def test_diff_no_instance(self, mock_get_instance):
        """Test diff function when sync instance is not found."""
        mock_get_instance.return_value = None
        
        with pytest.raises(SyncError, match="Failed to load sync instance"):
            diff(name="nonexistent")
    
    @patch('infrahub_sync.api.get_potenda_from_instance')
    @patch('infrahub_sync.api.get_instance')
    def test_diff_success(self, mock_get_instance, mock_get_potenda):
        """Test successful diff operation."""
        # Setup mocks
        mock_sync_instance = Mock(spec=SyncInstance)
        mock_get_instance.return_value = mock_sync_instance
        
        mock_potenda = Mock()
        mock_get_potenda.return_value = mock_potenda
        
        # Mock diff result
        mock_diff = Mock()
        mock_diff.has_diffs.return_value = True
        mock_diff.str.return_value = "Changes detected"
        mock_potenda.diff.return_value = mock_diff
        
        # Call function
        result = diff(name="test-sync")
        
        # Verify calls
        mock_get_instance.assert_called_once_with(name="test-sync", config_file=None, directory=None)
        mock_get_potenda.assert_called_once_with(
            sync_instance=mock_sync_instance, 
            branch=None, 
            show_progress=True
        )
        mock_potenda.source_load.assert_called_once()
        mock_potenda.destination_load.assert_called_once()
        mock_potenda.diff.assert_called_once()
        
        # Verify result
        assert result.success is True
        assert result.message == "Changes detected"
        assert result.changes_detected is True
        assert result.duration is not None

    @patch('infrahub_sync.api.get_potenda_from_instance')
    @patch('infrahub_sync.api.get_instance')
    def test_diff_no_changes(self, mock_get_instance, mock_get_potenda):
        """Test diff operation with no changes."""
        # Setup mocks
        mock_sync_instance = Mock(spec=SyncInstance)
        mock_get_instance.return_value = mock_sync_instance
        
        mock_potenda = Mock()
        mock_get_potenda.return_value = mock_potenda
        
        # Mock diff result with no changes
        mock_diff = Mock()
        mock_diff.has_diffs.return_value = False
        mock_potenda.diff.return_value = mock_diff
        
        # Call function
        result = diff(name="test-sync")
        
        # Verify result
        assert result.success is True
        assert result.message == "No differences found"
        assert result.changes_detected is False

    def test_sync_invalid_args(self):
        """Test sync function with invalid arguments."""
        # Test with no arguments
        with pytest.raises(SyncError, match="Please specify exactly one of 'name' or 'config_file'"):
            sync()

    @patch('infrahub_sync.api.get_potenda_from_instance')
    @patch('infrahub_sync.api.get_instance')
    def test_sync_no_changes(self, mock_get_instance, mock_get_potenda):
        """Test sync operation with no changes."""
        # Setup mocks
        mock_sync_instance = Mock(spec=SyncInstance)
        mock_get_instance.return_value = mock_sync_instance
        
        mock_potenda = Mock()
        mock_get_potenda.return_value = mock_potenda
        
        # Mock diff result with no changes
        mock_diff = Mock()
        mock_diff.has_diffs.return_value = False
        mock_potenda.diff.return_value = mock_diff
        
        # Call function
        result = sync(name="test-sync")
        
        # Verify result
        assert result.success is True
        assert result.message == "No differences found. Nothing to sync."
        assert result.changes_detected is False
        assert result.duration == 0.0
        
        # Verify sync was not called
        mock_potenda.sync.assert_not_called()

    @patch('infrahub_sync.api.get_potenda_from_instance')
    @patch('infrahub_sync.api.get_instance')
    def test_sync_with_changes(self, mock_get_instance, mock_get_potenda):
        """Test sync operation with changes."""
        # Setup mocks
        mock_sync_instance = Mock(spec=SyncInstance)
        mock_get_instance.return_value = mock_sync_instance
        
        mock_potenda = Mock()
        mock_get_potenda.return_value = mock_potenda
        
        # Mock diff result with changes
        mock_diff = Mock()
        mock_diff.has_diffs.return_value = True
        mock_diff.str.return_value = "Changes found"
        mock_potenda.diff.return_value = mock_diff
        
        # Call function
        result = sync(name="test-sync")
        
        # Verify sync was called
        mock_potenda.sync.assert_called_once_with(diff=mock_diff)
        
        # Verify result
        assert result.success is True
        assert "Found differences to sync" in result.message
        assert result.changes_detected is True
        assert result.duration is not None

    @patch('infrahub_sync.api.get_potenda_from_instance')
    @patch('infrahub_sync.api.get_instance')
    def test_create_potenda_success(self, mock_get_instance, mock_get_potenda):
        """Test successful creation of Potenda instance."""
        mock_sync_instance = Mock(spec=SyncInstance)
        mock_get_instance.return_value = mock_sync_instance
        
        mock_potenda_instance = Mock()
        mock_get_potenda.return_value = mock_potenda_instance
        
        result = create_potenda(name="test-sync")
        
        mock_get_instance.assert_called_once_with(name="test-sync", config_file=None, directory=None)
        mock_get_potenda.assert_called_once_with(
            sync_instance=mock_sync_instance,
            branch=None,
            show_progress=True
        )
        assert result == mock_potenda_instance

    def test_create_potenda_invalid_args(self):
        """Test create_potenda with invalid arguments."""
        with pytest.raises(SyncError, match="Please specify exactly one of 'name' or 'config_file'"):
            create_potenda()

    @patch('infrahub_sync.api.get_potenda_from_instance')
    @patch('infrahub_sync.api.get_instance')
    def test_error_handling_load_failure(self, mock_get_instance, mock_get_potenda):
        """Test error handling when loading fails."""
        mock_sync_instance = Mock(spec=SyncInstance)
        mock_get_instance.return_value = mock_sync_instance
        
        mock_potenda = Mock()
        mock_potenda.source_load.side_effect = ValueError("Load error")
        mock_get_potenda.return_value = mock_potenda
        
        with pytest.raises(SyncError, match="Failed to load data: Load error"):
            diff(name="test-sync")

    @patch('infrahub_sync.api.get_instance')
    def test_error_handling_potenda_creation_failure(self, mock_get_instance):
        """Test error handling when Potenda creation fails."""
        mock_sync_instance = Mock(spec=SyncInstance)
        mock_get_instance.return_value = mock_sync_instance
        
        with patch('infrahub_sync.api.get_potenda_from_instance') as mock_get_potenda:
            mock_get_potenda.side_effect = ValueError("Potenda creation failed")
            
            with pytest.raises(SyncError, match="Failed to initialize the Sync Instance: Potenda creation failed"):
                diff(name="test-sync")