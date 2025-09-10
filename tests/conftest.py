"""
Test configuration and fixtures
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from src.models import WorkItem, TestCaseInfo, WorkItemUpdate
from src.config import Config


@pytest.fixture
def mock_config():
    """Mock configuration for testing"""
    config = MagicMock(spec=Config)
    config.azure_devops_org = "test-org"
    config.azure_devops_project = "test-project"
    config.azure_devops_pat = "test-pat"
    config.github_token = "test-token"
    config.github_repo = "test-owner/test-repo"
    config.github_workflow_file = "test-workflow.yml"
    config.target_column = "Testing"
    config.polling_interval = 30
    config.max_retries = 3
    return config


@pytest.fixture
def sample_work_item():
    """Sample work item for testing"""
    return WorkItem(
        id=12345,
        title="Test Work Item",
        state="Active",
        work_item_type="User Story",
        assigned_to="test.user@example.com",
        created_date=datetime(2024, 1, 1, 10, 0, 0),
        changed_date=datetime(2024, 1, 1, 11, 0, 0),
        board_column="Testing",
        description="Test description with test_case_1 and test_case_2",
        acceptance_criteria="Should pass test_validation and test_integration",
        test_cases=[
            TestCaseInfo(
                test_id="test_case_1",
                test_name="test_case_1",
                test_path="tests/test_case_1.py",
                test_suite="unit"
            ),
            TestCaseInfo(
                test_id="test_case_2",
                test_name="test_case_2",
                test_path="tests/test_case_2.py",
                test_suite="integration"
            )
        ],
        tags=["automation", "testing"],
        custom_fields={"Custom.Priority": "High"}
    )


@pytest.fixture
def sample_work_item_update():
    """Sample work item update for testing"""
    return WorkItemUpdate(
        work_item_id=12345,
        previous_column="In Progress",
        current_column="Testing",
        timestamp=datetime(2024, 1, 1, 11, 0, 0),
        updated_by="test.user@example.com",
        change_type="column_change"
    )


@pytest.fixture
def mock_azure_client():
    """Mock Azure DevOps client"""
    client = AsyncMock()
    client.get_work_items_in_column = AsyncMock(return_value=[])
    client.get_work_item_by_id = AsyncMock(return_value=None)
    client.monitor_work_item_changes = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_github_client():
    """Mock GitHub client"""
    client = AsyncMock()
    client.trigger_workflow = AsyncMock(return_value=None)
    client.check_workflow_exists = AsyncMock(return_value=True)
    client.get_workflow_runs = AsyncMock(return_value=[])
    return client


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
