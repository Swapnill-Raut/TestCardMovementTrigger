"""
Work item monitoring logic
"""
import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from models import WorkItem, WorkItemUpdate, MonitoringEvent, TestCaseInfo
from azure_devops_client import AzureDevOpsClient
from github_client import GitHubClient
from config import config

logger = logging.getLogger(__name__)


class WorkItemMonitor:
    """Monitors Azure DevOps work items and triggers GitHub Actions"""
    
    def __init__(self):
        self.azure_client = AzureDevOpsClient()
        self.github_client = GitHubClient()
        self.scheduler = AsyncIOScheduler()
        self.monitoring_events: List[MonitoringEvent] = []
        self._is_running = False
        
    async def start_monitoring(self):
        """Start the monitoring process"""
        if self._is_running:
            logger.warning("Monitoring is already running")
            return
        
        self._is_running = True
        logger.info("Starting work item monitoring...")
        
        # Verify GitHub workflow exists
        workflow_exists = await self.github_client.check_workflow_exists()
        if not workflow_exists:
            logger.error(f"GitHub workflow {config.github_workflow_file} not found!")
            return
        
        # Start scheduled monitoring
        self.scheduler.add_job(
            self._monitor_work_items,
            trigger=IntervalTrigger(seconds=config.polling_interval),
            id="work_item_monitor",
            name="Azure DevOps Work Item Monitor",
            max_instances=1
        )
        
        self.scheduler.start()
        logger.info(f"Monitoring started with {config.polling_interval}s interval")
        
        # Run initial check
        await self._monitor_work_items()
    
    async def stop_monitoring(self):
        """Stop the monitoring process"""
        if not self._is_running:
            return
        
        self._is_running = False
        self.scheduler.shutdown()
        logger.info("Monitoring stopped")
    
    async def _monitor_work_items(self):
        """Main monitoring loop"""
        try:
            logger.debug("Checking for work item changes...")
            
            # Get work item updates
            updates = await self.azure_client.monitor_work_item_changes(
                config.polling_interval
            )
            
            if not updates:
                logger.debug("No work item changes detected")
                return
            
            logger.info(f"Found {len(updates)} work item updates")
            
            # Process each update
            for update in updates:
                await self._process_work_item_update(update)
                
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            await self._record_monitoring_event(
                "monitor_error",
                0,
                {"error": str(e)},
                success=False
            )
    
    async def _process_work_item_update(self, update: WorkItemUpdate):
        """Process a single work item update"""
        try:
            logger.info(f"Processing work item {update.work_item_id} moved to {update.current_column}")
            
            # Get detailed work item information
            work_item = await self.azure_client.get_work_item_by_id(update.work_item_id)
            if not work_item:
                logger.error(f"Could not fetch work item {update.work_item_id}")
                return
            
            # Extract test cases
            test_cases = work_item.test_cases
            if not test_cases:
                logger.warning(f"No test cases found in work item {update.work_item_id}")
                # Create a default test case based on work item
                test_cases = [
                    TestCaseInfo(
                        test_id=f"workitem_{work_item.id}",
                        test_name=f"test_work_item_{work_item.id}",
                        test_path=None,
                        test_suite="default"
                    )
                ]
            
            # Extract branch information
            target_branch = work_item.associated_branch
            if not target_branch:
                # Fallback to custom fields
                target_branch = work_item.custom_fields.get('Associated_Branch')
                if not target_branch:
                    # Try to extract from custom fields with different keys
                    for key in work_item.custom_fields:
                        if 'branch' in key.lower():
                            target_branch = work_item.custom_fields[key]
                            break
            
            # Prepare additional inputs for GitHub workflow
            additional_inputs = {
                "work_item_title": work_item.title,
                "work_item_type": work_item.work_item_type,
                "work_item_state": work_item.state,
                "assigned_to": work_item.assigned_to or "Unassigned",
                "board_column": work_item.board_column or config.target_column,
                "test_command": self.github_client.generate_test_command(test_cases),
                "associated_branch": target_branch or "main"
            }
            
            # Log branch information
            if target_branch:
                logger.info(f"Work item {work_item.id} associated with branch: {target_branch}")
            else:
                logger.info(f"Work item {work_item.id} has no associated branch, using main")
            
            # Trigger GitHub workflow
            workflow_run = await self.github_client.trigger_workflow(
                work_item_id=work_item.id,
                test_cases=test_cases,
                additional_inputs=additional_inputs,
                target_branch=target_branch
            )
            
            if workflow_run:
                logger.info(f"Successfully triggered workflow run {workflow_run.id} for work item {work_item.id}")
                
                # Record successful event
                await self._record_monitoring_event(
                    "workflow_triggered",
                    work_item.id,
                    {
                        "workflow_run_id": workflow_run.id,
                        "workflow_url": workflow_run.html_url,
                        "test_cases_count": len(test_cases),
                        "test_cases": [tc.dict() for tc in test_cases]
                    }
                )
                
                # Optionally create GitHub issue comment
                await self.github_client.create_github_issue_comment(
                    work_item.id,
                    workflow_run
                )
                
            else:
                logger.error(f"Failed to trigger workflow for work item {work_item.id}")
                await self._record_monitoring_event(
                    "workflow_trigger_failed",
                    work_item.id,
                    {"test_cases_count": len(test_cases)},
                    success=False,
                    error_message="Failed to trigger GitHub workflow"
                )
                
        except Exception as e:
            logger.error(f"Error processing work item update {update.work_item_id}: {e}")
            await self._record_monitoring_event(
                "process_update_error",
                update.work_item_id,
                {"error": str(e)},
                success=False,
                error_message=str(e)
            )
    
    async def _record_monitoring_event(
        self,
        event_type: str,
        work_item_id: int,
        data: Dict[str, Any],
        success: bool = True,
        error_message: Optional[str] = None
    ):
        """Record a monitoring event for logging and tracking"""
        event = MonitoringEvent(
            event_type=event_type,
            work_item_id=work_item_id,
            timestamp=datetime.utcnow(),
            data=data,
            success=success,
            error_message=error_message
        )
        
        self.monitoring_events.append(event)
        
        # Keep only last 1000 events to prevent memory issues
        if len(self.monitoring_events) > 1000:
            self.monitoring_events = self.monitoring_events[-1000:]
        
        # Log the event
        if success:
            logger.info(f"Event recorded: {event_type} for work item {work_item_id}")
        else:
            logger.error(f"Error event recorded: {event_type} for work item {work_item_id}: {error_message}")
    
    async def get_monitoring_status(self) -> Dict[str, Any]:
        """Get current monitoring status"""
        recent_events = [
            event for event in self.monitoring_events
            if event.timestamp > datetime.utcnow() - timedelta(hours=24)
        ]
        
        successful_events = [e for e in recent_events if e.success]
        failed_events = [e for e in recent_events if not e.success]
        
        return {
            "is_running": self._is_running,
            "polling_interval": config.polling_interval,
            "target_column": config.target_column,
            "github_repo": config.github_repo,
            "github_workflow": config.github_workflow_file,
            "events_last_24h": len(recent_events),
            "successful_events_last_24h": len(successful_events),
            "failed_events_last_24h": len(failed_events),
            "last_check": recent_events[-1].timestamp.isoformat() if recent_events else None
        }
    
    async def get_recent_events(self, hours: int = 24) -> List[MonitoringEvent]:
        """Get recent monitoring events"""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        return [
            event for event in self.monitoring_events
            if event.timestamp > cutoff_time
        ]
    
    async def trigger_manual_check(self) -> List[WorkItemUpdate]:
        """Manually trigger a check for work item changes"""
        logger.info("Manual check triggered")
        return await self.azure_client.monitor_work_item_changes(0)
    
    async def process_specific_work_item(self, work_item_id: int) -> bool:
        """Manually process a specific work item"""
        try:
            logger.info(f"Manually processing work item {work_item_id}")
            
            work_item = await self.azure_client.get_work_item_by_id(work_item_id)
            if not work_item:
                logger.error(f"Work item {work_item_id} not found")
                return False
            
            # Create a fake update event
            update = WorkItemUpdate(
                work_item_id=work_item_id,
                previous_column="Manual",
                current_column=work_item.board_column or config.target_column,
                timestamp=datetime.utcnow(),
                updated_by="Manual Trigger",
                change_type="manual_trigger"
            )
            
            await self._process_work_item_update(update)
            return True
            
        except Exception as e:
            logger.error(f"Error manually processing work item {work_item_id}: {e}")
            return False
