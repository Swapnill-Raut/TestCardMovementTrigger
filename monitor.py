#!/usr/bin/env python3
"""
Azure DevOps Work Item Monitor - Production Version

This script monitors Azure DevOps work items for changes to the specified column
and triggers GitHub Actions workflows with branch-aware execution.
Optimized for both local and cloud (GitHub Actions) execution.

Features:
- Only processes NEW work items moved to the target column (avoids duplicate triggers)
- Automatically detects branch information from work item relations
- Configurable polling interval with runtime limits for cloud execution
- Comprehensive error handling and logging
- Persistent state management for processed items
- Cloud-optimized with graceful shutdown

Usage:
    python monitor.py
    
Configuration:
    Set environment variables in .env file or system environment
"""

import asyncio
import logging
import sys
import signal
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Set
import json

# Add src directory to path for local imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.config import Config
from src.azure_devops_client import AzureDevOpsClient
from src.github_client import GitHubClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('monitor.log')
    ]
)

logger = logging.getLogger(__name__)

class WorkItemMonitor:
    """Main monitor class that coordinates Azure DevOps and GitHub interactions"""
    
    def __init__(self):
        self.config = Config()
        self.azure_client = AzureDevOpsClient()
        self.github_client = GitHubClient()
        self.processed_items_file = Path("processed_items.json")
        self.processed_items: Set[int] = self._load_processed_items()
        self.running = True
        
        # Cloud execution settings
        self.max_runtime = int(os.getenv('MAX_RUNTIME_SECONDS', '0'))  # 0 = unlimited (local mode)
        self.is_cloud_mode = self.max_runtime > 0
        
        if self.is_cloud_mode:
            logger.info(f"Running in cloud mode with {self.max_runtime}s runtime limit")
        else:
            logger.info("Running in local mode (unlimited runtime)")
        
    def _load_processed_items(self) -> Set[int]:
        """Load the set of already processed work item IDs"""
        if self.processed_items_file.exists():
            try:
                with open(self.processed_items_file, 'r') as f:
                    data = json.load(f)
                    return set(data.get('processed_items', []))
            except Exception as e:
                logger.warning(f"Error loading processed items: {e}")
        return set()
    
    def _save_processed_items(self):
        """Save the set of processed work item IDs"""
        try:
            data = {
                'processed_items': list(self.processed_items),
                'last_updated': datetime.now().isoformat()
            }
            with open(self.processed_items_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving processed items: {e}")
    
    async def initialize(self) -> bool:
        """Initialize connections and populate initial cache"""
        try:
            logger.info("Initializing Azure DevOps MCP Monitor")
            logger.info("=" * 60)
            
            # Test Azure DevOps connection
            logger.info("Testing Azure DevOps connection...")
            current_items = await self.azure_client.get_work_items_in_column(self.config.target_column)
            logger.info(f"Azure DevOps connection successful!")
            logger.info(f"Found {len(current_items)} work items in '{self.config.target_column}' column")
            
            # Populate initial cache with existing items to avoid triggering workflows for them
            for item in current_items:
                self.processed_items.add(item.id)
                logger.debug(f"Added existing work item {item.id} to processed cache")
            
            # Save initial cache
            self._save_processed_items()
            logger.info(f"Cached {len(current_items)} existing work items to avoid duplicate triggers")
            
            # Test GitHub connection
            logger.info("Testing GitHub connection...")
            # Test workflow exists
            if await self.github_client.check_workflow_exists():
                logger.info(f"GitHub connection successful!")
                logger.info(f"Repository: {self.config.github_repo}")
                logger.info(f"Workflow file: {self.config.github_workflow_file}")
                logger.info(f"Default branch: {self.config.github_default_branch}")
            else:
                logger.error(f"GitHub workflow '{self.config.github_workflow_file}' not found!")
                return False
            
            logger.info("=" * 60)
            logger.info("Monitor initialized successfully!")
            logger.info(f"Polling interval: {self.config.polling_interval}s")
            logger.info(f"Target column: {self.config.target_column}")
            
            return True
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            return False
    
    async def process_work_item_update(self, work_item):
        """Process a single work item update by triggering the appropriate GitHub workflow"""
        try:
            # Extract branch information from work item GitHub relations
            branch_from_github = await self.azure_client.extract_github_branch(work_item.id)
            
            logger.info(f"Triggering workflow for work item {work_item.id}")
            logger.info(f"  Title: {work_item.title}")
            logger.info(f"  Updated by: {work_item.assigned_to}")
            
            success = False
            trigger_source = f"Azure DevOps MCP Monitor ({'Cloud' if self.is_cloud_mode else 'Local'})"
            
            # Try triggering on work item branch first (if it exists)
            if branch_from_github:
                logger.info(f"  Attempting to trigger on work item branch: {branch_from_github}")
                success = await self.github_client.trigger_workflow(
                    workflow_file=self.config.github_workflow_file,
                    ref=branch_from_github,
                    inputs={
                        "work_item_id": str(work_item.id),
                        "work_item_title": work_item.title,
                        "work_item_type": work_item.work_item_type,
                        "board_column": self.config.target_column,
                        "trigger_source": trigger_source,
                        "target_branch": branch_from_github
                    }
                )
                
                if success:
                    logger.info(f"  Successfully triggered on work item branch: {branch_from_github}")
                else:
                    logger.warning(f"  Failed to trigger on work item branch: {branch_from_github}")
            
            # If no branch or if work item branch failed, fall back to main
            if not success:
                fallback_branch = self.config.github_default_branch
                target_branch = branch_from_github or fallback_branch
                
                logger.info(f"  Attempting to trigger on fallback branch: {fallback_branch}")
                success = await self.github_client.trigger_workflow(
                    workflow_file=self.config.github_workflow_file,
                    ref=fallback_branch,
                    inputs={
                        "work_item_id": str(work_item.id),
                        "work_item_title": work_item.title,
                        "work_item_type": work_item.work_item_type,
                        "board_column": self.config.target_column,
                        "trigger_source": trigger_source,
                        "target_branch": target_branch
                    }
                )
                
                if success:
                    logger.info(f"  Successfully triggered on fallback branch: {fallback_branch}")
                else:
                    logger.error(f"  ❌ Failed to trigger on both branches!")
            
            if success:
                logger.info(f"Successfully triggered workflow for work item {work_item.id}")
                # Mark as processed
                self.processed_items.add(work_item.id)
                self._save_processed_items()
            else:
                logger.error(f"Failed to trigger workflow for work item {work_item.id}")
                
        except Exception as e:
            logger.error(f"Error processing work item {work_item.id}: {e}")
    
    async def check_for_new_items(self):
        """Check for new work items in the target column"""
        # Get current work items in the target column
        current_items = await self.azure_client.get_work_items_in_column(self.config.target_column)
        
        # Find new items (not in processed cache)
        new_items = [item for item in current_items if item.id not in self.processed_items]
        
        if new_items:
            logger.info(f"Found {len(new_items)} new work items in '{self.config.target_column}' column:")
            
            for item in new_items:
                # Extract branch information for logging
                branch_from_github = await self.azure_client.extract_github_branch(item.id)
                branch_info = f" (Branch: {branch_from_github})" if branch_from_github else " (Using default branch)"
                logger.info(f"  - Work Item {item.id}: {item.title}{branch_info}")
                
                # Process each new item
                await self.process_work_item_update(item)
                
                # Small delay between processing items
                await asyncio.sleep(1)
        else:
            logger.debug(f"No new work items found. {len(current_items)} items in column, {len(self.processed_items)} already processed.")
    
    async def cleanup_processed_cache(self):
        """Clean up processed items that are no longer in the target column"""
        current_items = await self.azure_client.get_work_items_in_column(self.config.target_column)
        current_ids = {item.id for item in current_items}
        items_to_remove = [item_id for item_id in self.processed_items if item_id not in current_ids]
        
        for item_id in items_to_remove:
            self.processed_items.discard(item_id)
            logger.debug(f"Removed work item {item_id} from processed cache (no longer in target column)")
        
        if items_to_remove:
            self._save_processed_items()
            logger.debug(f"Cleaned up {len(items_to_remove)} items from processed cache")

    async def monitor_loop(self):
        """Main monitoring loop with cloud runtime management"""
        start_time = datetime.utcnow()
        max_end_time = None
        
        if self.is_cloud_mode:
            max_end_time = start_time + timedelta(seconds=self.max_runtime)
            logger.info(f"Cloud mode: Will stop monitoring at {max_end_time}")
        
        logger.info("Starting monitoring loop...")
        
        while self.running:
            try:
                # Check runtime limit for cloud execution
                if self.is_cloud_mode and datetime.utcnow() >= max_end_time:
                    logger.info("Runtime limit reached, stopping gracefully...")
                    break
                    
                # Check for new work items
                await self.check_for_new_items()
                
                # Clean up old processed items cache if needed
                await self.cleanup_processed_cache()
                
                # Check if we have enough time for another cycle in cloud mode
                if self.is_cloud_mode:
                    time_until_limit = (max_end_time - datetime.utcnow()).total_seconds()
                    if time_until_limit < self.config.polling_interval + 30:  # 30s buffer
                        logger.info("Not enough time for another cycle, stopping gracefully...")
                        break
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                
                # In cloud mode, don't continue on errors to avoid wasting resources
                if self.is_cloud_mode:
                    logger.error("Cloud mode: Stopping due to error")
                    break
                else:
                    logger.info("Local mode: Continuing monitoring after error...")
            
            # Wait before next poll
            await asyncio.sleep(self.config.polling_interval)
        
        # Final save of processed items
        self._save_processed_items()
        
        if self.is_cloud_mode:
            runtime = (datetime.utcnow() - start_time).total_seconds()
            logger.info(f"Cloud execution completed. Runtime: {runtime:.1f}s")
    
    async def run(self):
        """Main entry point to run the monitor"""
        try:
            logger.info("===============================================")
            if self.is_cloud_mode:
                logger.info("Azure DevOps MCP Monitor (Cloud Mode)")
                logger.info(f"Max runtime: {self.max_runtime} seconds")
            else:
                logger.info("Azure DevOps MCP Monitor (Local Mode)")
            logger.info("===============================================")
            
            # Initialize connections
            if not await self.initialize():
                logger.error("Failed to initialize monitor")
                return False
            
            # Start monitoring
            await self.monitor_loop()
            
        except KeyboardInterrupt:
            logger.info("Monitor stopped by user")
            self._save_processed_items()
        except Exception as e:
            logger.error(f"Monitor failed: {e}")
            self._save_processed_items()
            return False
        
        return True

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    sys.exit(0)

async def main():
    """Main function with signal handling"""
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    monitor = WorkItemMonitor()
    success = await monitor.run()
    
    if success:
        logger.info("Monitor completed successfully!")
        sys.exit(0)
    else:
        logger.error("Monitor failed!")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
