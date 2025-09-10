"""
Data models for Azure DevOps MCP Monitor
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class WorkItemState(str, Enum):
    """Work item states"""
    NEW = "New"
    ACTIVE = "Active"
    RESOLVED = "Resolved"
    CLOSED = "Closed"
    TESTING = "Testing"
    DONE = "Done"


class WorkItemType(str, Enum):
    """Work item types"""
    USER_STORY = "User Story"
    BUG = "Bug"
    TASK = "Task"
    FEATURE = "Feature"
    EPIC = "Epic"


class TestCaseInfo(BaseModel):
    """Test case information extracted from work item"""
    test_id: str
    test_name: str
    test_path: Optional[str] = None
    test_suite: Optional[str] = None


class WorkItem(BaseModel):
    """Azure DevOps work item model"""
    id: int
    title: str
    state: str
    work_item_type: str
    assigned_to: Optional[str] = None
    created_date: datetime
    changed_date: datetime
    board_column: Optional[str] = None
    description: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    test_cases: List[TestCaseInfo] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)
    associated_branch: Optional[str] = None  # GitHub branch to trigger workflow on
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class WorkItemUpdate(BaseModel):
    """Work item update event"""
    work_item_id: int
    previous_column: Optional[str] = None
    current_column: str
    timestamp: datetime
    updated_by: str
    change_type: str = "column_change"


class GitHubWorkflowTrigger(BaseModel):
    """GitHub workflow trigger request"""
    workflow_file: str
    ref: str = "main"
    inputs: Dict[str, Any] = Field(default_factory=dict)


class GitHubWorkflowRun(BaseModel):
    """GitHub workflow run response"""
    id: int
    name: str
    status: str
    conclusion: Optional[str] = None
    html_url: str
    created_at: datetime
    updated_at: datetime


class MonitoringEvent(BaseModel):
    """Monitoring event for logging and tracking"""
    event_type: str
    work_item_id: int
    timestamp: datetime
    data: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error_message: Optional[str] = None
