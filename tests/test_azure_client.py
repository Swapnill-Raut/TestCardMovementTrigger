"""
Tests for Azure DevOps client
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.azure_devops_client import AzureDevOpsClient
from src.models import WorkItem, TestCaseInfo


class TestAzureDevOpsClient:
    """Test Azure DevOps client functionality"""
    
    @pytest.fixture
    def mock_wit_client(self):
        """Mock work item tracking client"""
        client = MagicMock()
        client.query_by_wiql = MagicMock()
        client.get_work_items = MagicMock()
        client.get_work_item = MagicMock()
        return client
    
    @pytest.fixture
    def azure_client(self, mock_config, mock_wit_client):
        """Azure DevOps client with mocked dependencies"""
        with patch('src.azure_devops_client.Connection') as mock_connection:
            mock_connection.return_value.clients.get_work_item_tracking_client.return_value = mock_wit_client
            
            client = AzureDevOpsClient()
            client.wit_client = mock_wit_client
            return client
    
    @pytest.mark.asyncio
    async def test_get_work_items_in_column_empty(self, azure_client, mock_wit_client):
        """Test getting work items when none exist"""
        # Setup mock response
        mock_result = MagicMock()
        mock_result.work_items = None
        mock_wit_client.query_by_wiql.return_value = mock_result
        
        # Execute
        result = await azure_client.get_work_items_in_column("Testing")
        
        # Assert
        assert result == []
        mock_wit_client.query_by_wiql.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_work_items_in_column_with_items(self, azure_client, mock_wit_client):
        """Test getting work items with results"""
        # Setup mock query result
        mock_work_item_ref = MagicMock()
        mock_work_item_ref.id = 12345
        
        mock_query_result = MagicMock()
        mock_query_result.work_items = [mock_work_item_ref]
        mock_wit_client.query_by_wiql.return_value = mock_query_result
        
        # Setup mock work item details
        mock_work_item = MagicMock()
        mock_work_item.id = 12345
        mock_work_item.fields = {
            'System.Title': 'Test Work Item',
            'System.State': 'Active',
            'System.WorkItemType': 'User Story',
            'System.AssignedTo': 'test.user@example.com',
            'System.CreatedDate': datetime(2024, 1, 1, 10, 0, 0),
            'System.ChangedDate': datetime(2024, 1, 1, 11, 0, 0),
            'System.BoardColumn': 'Testing',
            'System.Description': 'Test description',
            'Microsoft.VSTS.Common.AcceptanceCriteria': 'Test criteria'
        }
        
        mock_wit_client.get_work_items.return_value = [mock_work_item]
        
        # Execute
        result = await azure_client.get_work_items_in_column("Testing")
        
        # Assert
        assert len(result) == 1
        assert result[0].id == 12345
        assert result[0].title == 'Test Work Item'
        assert result[0].board_column == 'Testing'
    
    @pytest.mark.asyncio
    async def test_get_work_item_by_id(self, azure_client, mock_wit_client):
        """Test getting a specific work item by ID"""
        # Setup mock work item
        mock_work_item = MagicMock()
        mock_work_item.id = 12345
        mock_work_item.fields = {
            'System.Title': 'Test Work Item',
            'System.State': 'Active',
            'System.WorkItemType': 'User Story',
            'System.CreatedDate': datetime(2024, 1, 1, 10, 0, 0),
            'System.ChangedDate': datetime(2024, 1, 1, 11, 0, 0),
        }
        
        mock_wit_client.get_work_item.return_value = mock_work_item
        
        # Execute
        result = await azure_client.get_work_item_by_id(12345)
        
        # Assert
        assert result is not None
        assert result.id == 12345
        assert result.title == 'Test Work Item'
        mock_wit_client.get_work_item.assert_called_once_with(id=12345, expand="All")
    
    def test_extract_test_cases(self, azure_client):
        """Test test case extraction from work item content"""
        description = "This feature requires test_login and test_user_profile"
        acceptance_criteria = "Must pass test_validation and pytest tests/test_integration.py"
        
        result = azure_client._extract_test_cases(description, acceptance_criteria)
        
        # Should extract test IDs
        test_ids = [tc.test_id for tc in result]
        assert "login" in test_ids
        assert "user_profile" in test_ids
        assert "validation" in test_ids
        assert "integration.py" in test_ids
    
    def test_extract_display_name(self, azure_client):
        """Test display name extraction"""
        # Test dict format
        user_dict = {"displayName": "John Doe"}
        assert azure_client._extract_display_name(user_dict) == "John Doe"
        
        # Test string format
        user_string = "John Doe <john.doe@example.com>"
        assert azure_client._extract_display_name(user_string) == "John Doe"
        
        # Test None
        assert azure_client._extract_display_name(None) is None
    
    def test_parse_azure_date(self, azure_client):
        """Test Azure date parsing"""
        # Test datetime object
        dt = datetime(2024, 1, 1, 10, 0, 0)
        assert azure_client._parse_azure_date(dt) == dt
        
        # Test ISO string
        iso_string = "2024-01-01T10:00:00Z"
        result = azure_client._parse_azure_date(iso_string)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1
        
        # Test None
        result = azure_client._parse_azure_date(None)
        assert isinstance(result, datetime)
