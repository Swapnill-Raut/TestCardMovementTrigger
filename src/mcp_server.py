"""
Main MCP Server for Azure DevOps Work Item Monitoring
"""
import asyncio
import logging
import json
from typing import Any, Dict, List, Optional
from datetime import datetime
import click
import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, Tool, TextContent

from .config import config
from .work_item_monitor import WorkItemMonitor
from .models import MonitoringEvent
from .azure_devops_client import AzureDevOpsClient
from .github_client import GitHubClient

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


class AzureDevOpsMCPServer:
    """MCP Server for Azure DevOps work item monitoring"""
    
    def __init__(self):
        self.monitor = WorkItemMonitor()
        self.mcp_server = Server("azure-devops-monitor")
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Setup MCP server handlers"""
        
        @self.mcp_server.list_resources()
        async def list_resources() -> List[Resource]:
            """List available resources"""
            return [
                Resource(
                    uri="monitoring-status",
                    name="Monitoring Status",
                    description="Current status of work item monitoring",
                    mimeType="application/json"
                ),
                Resource(
                    uri="recent-events",
                    name="Recent Events",
                    description="Recent monitoring events and triggers",
                    mimeType="application/json"
                ),
                Resource(
                    uri="work-items",
                    name="Work Items in Target Column",
                    description=f"Work items currently in {config.target_column} column",
                    mimeType="application/json"
                )
            ]
        
        @self.mcp_server.read_resource()
        async def read_resource(uri: str) -> str:
            """Read a specific resource"""
            if uri == "monitoring-status":
                status = await self.monitor.get_monitoring_status()
                return json.dumps(status, indent=2)
            
            elif uri == "recent-events":
                events = await self.monitor.get_recent_events(24)
                events_data = [
                    {
                        "event_type": event.event_type,
                        "work_item_id": event.work_item_id,
                        "timestamp": event.timestamp.isoformat(),
                        "success": event.success,
                        "data": event.data,
                        "error_message": event.error_message
                    }
                    for event in events
                ]
                return json.dumps(events_data, indent=2)
            
            elif uri == "work-items":
                azure_client = AzureDevOpsClient()
                work_items = await azure_client.get_work_items_in_column(config.target_column)
                work_items_data = [
                    {
                        "id": wi.id,
                        "title": wi.title,
                        "state": wi.state,
                        "work_item_type": wi.work_item_type,
                        "assigned_to": wi.assigned_to,
                        "changed_date": wi.changed_date.isoformat(),
                        "test_cases": [tc.dict() for tc in wi.test_cases]
                    }
                    for wi in work_items
                ]
                return json.dumps(work_items_data, indent=2)
            
            else:
                raise ValueError(f"Unknown resource: {uri}")
        
        @self.mcp_server.list_tools()
        async def list_tools() -> List[Tool]:
            """List available tools"""
            return [
                Tool(
                    name="start_monitoring",
                    description="Start monitoring Azure DevOps work items",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="stop_monitoring",
                    description="Stop monitoring Azure DevOps work items",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="trigger_manual_check",
                    description="Manually trigger a check for work item changes",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                ),
                Tool(
                    name="process_work_item",
                    description="Manually process a specific work item",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "work_item_id": {
                                "type": "integer",
                                "description": "The ID of the work item to process"
                            }
                        },
                        "required": ["work_item_id"]
                    }
                ),
                Tool(
                    name="get_work_item",
                    description="Get details of a specific work item",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "work_item_id": {
                                "type": "integer",
                                "description": "The ID of the work item to retrieve"
                            }
                        },
                        "required": ["work_item_id"]
                    }
                ),
                Tool(
                    name="check_github_workflow",
                    description="Check if GitHub workflow exists and get recent runs",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "description": "Number of recent runs to retrieve",
                                "default": 10
                            }
                        },
                        "required": []
                    }
                )
            ]
        
        @self.mcp_server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
            """Handle tool calls"""
            try:
                if name == "start_monitoring":
                    await self.monitor.start_monitoring()
                    return [TextContent(
                        type="text",
                        text="✅ Monitoring started successfully"
                    )]
                
                elif name == "stop_monitoring":
                    await self.monitor.stop_monitoring()
                    return [TextContent(
                        type="text",
                        text="⏹️ Monitoring stopped"
                    )]
                
                elif name == "trigger_manual_check":
                    updates = await self.monitor.trigger_manual_check()
                    if updates:
                        result = f"🔍 Found {len(updates)} work item updates:\n"
                        for update in updates:
                            result += f"- Work Item {update.work_item_id}: {update.previous_column} → {update.current_column}\n"
                    else:
                        result = "🔍 No work item changes detected"
                    
                    return [TextContent(type="text", text=result)]
                
                elif name == "process_work_item":
                    work_item_id = arguments["work_item_id"]
                    success = await self.monitor.process_specific_work_item(work_item_id)
                    
                    if success:
                        text = f"✅ Successfully processed work item {work_item_id}"
                    else:
                        text = f"❌ Failed to process work item {work_item_id}"
                    
                    return [TextContent(type="text", text=text)]
                
                elif name == "get_work_item":
                    work_item_id = arguments["work_item_id"]
                    azure_client = AzureDevOpsClient()
                    work_item = await azure_client.get_work_item_by_id(work_item_id)
                    
                    if work_item:
                        result = f"""📋 **Work Item {work_item.id}**
**Title**: {work_item.title}
**Type**: {work_item.work_item_type}
**State**: {work_item.state}
**Column**: {work_item.board_column or 'N/A'}
**Assigned To**: {work_item.assigned_to or 'Unassigned'}
**Changed**: {work_item.changed_date.strftime('%Y-%m-%d %H:%M:%S')}

**Test Cases Found**: {len(work_item.test_cases)}
"""
                        for tc in work_item.test_cases:
                            result += f"- {tc.test_name} (ID: {tc.test_id})\n"
                    else:
                        result = f"❌ Work item {work_item_id} not found"
                    
                    return [TextContent(type="text", text=result)]
                
                elif name == "check_github_workflow":
                    limit = arguments.get("limit", 10)
                    github_client = GitHubClient()
                    
                    # Check if workflow exists
                    exists = await github_client.check_workflow_exists()
                    if not exists:
                        return [TextContent(
                            type="text",
                            text=f"❌ GitHub workflow {config.github_workflow_file} not found in {config.github_repo}"
                        )]
                    
                    # Get recent runs
                    runs = await github_client.get_workflow_runs(limit=limit)
                    
                    result = f"✅ **GitHub Workflow**: {config.github_workflow_file}\n"
                    result += f"📊 **Recent Runs** (last {len(runs)}):\n\n"
                    
                    for run in runs:
                        status_emoji = {
                            "completed": "✅" if run.conclusion == "success" else "❌",
                            "in_progress": "🔄",
                            "queued": "⏳"
                        }.get(run.status, "❓")
                        
                        result += f"{status_emoji} **Run {run.id}**: {run.status}"
                        if run.conclusion:
                            result += f" ({run.conclusion})"
                        result += f" - {run.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        result += f"   🔗 {run.html_url}\n\n"
                    
                    return [TextContent(type="text", text=result)]
                
                else:
                    return [TextContent(
                        type="text",
                        text=f"❌ Unknown tool: {name}"
                    )]
            
            except Exception as e:
                logger.error(f"Error in tool {name}: {e}")
                return [TextContent(
                    type="text",
                    text=f"❌ Error executing {name}: {str(e)}"
                )]
    
    async def run(self):
        """Run the MCP server"""
        logger.info("Starting Azure DevOps MCP Monitor")
        
        # Start the monitoring in the background
        asyncio.create_task(self.monitor.start_monitoring())
        
        # Run the MCP server
        async with stdio_server() as streams:
            await self.mcp_server.run(streams[0], streams[1])


@click.command()
@click.option('--log-level', default='INFO', help='Log level (DEBUG, INFO, WARNING, ERROR)')
@click.option('--config-file', default='.env', help='Configuration file path')
def main(log_level: str, config_file: str):
    """Run the Azure DevOps MCP Monitor server"""
    
    # Configure logging level
    logging.basicConfig(level=getattr(logging, log_level.upper()))
    
    # Load configuration
    if config_file != '.env':
        import os
        os.environ['CONFIG_FILE'] = config_file
    
    # Create and run server
    server = AzureDevOpsMCPServer()
    
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise


if __name__ == "__main__":
    main()
