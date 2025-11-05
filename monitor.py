#!/usr/bin/env python3
"""
Thallium ADO Card Movement Monitor - Azure DevOps MCP Server
Model Context Protocol server for monitoring Azure DevOps work items and triggering Thallium test workflows
"""

import os
import sys
import json
import time
import signal
import re
import requests
import logging
import asyncio
from datetime import datetime
from typing import Set, Dict, Any, Optional, List
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
    ListToolsResult,
)

# Configure logging with proper file handler management
log_file_handler = logging.FileHandler('azure_devops_mcp.log')
log_file_handler.setLevel(logging.INFO)
log_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        log_file_handler
    ]
)
logger = logging.getLogger(__name__)

class Config:
    """Configuration management for the monitor"""
    
    def __init__(self):
        self.azure_devops_org = os.getenv('IMAGEN_CUSTOMER_QA_AZURE_DEVOPS_ORG')
        self.azure_devops_project = os.getenv('IMAGEN_CUSTOMER_QA_AZURE_DEVOPS_PROJECT')
        self.azure_devops_team = os.getenv('IMAGEN_CUSTOMER_QA_AZURE_DEVOPS_TEAM')  # New: Team/Board filter
        self.azure_devops_pat = os.getenv('IMAGEN_CUSTOMER_QA_AZURE_DEVOPS_PAT')
        self.github_token = os.getenv('GITHUB_TOKEN')
        self.github_repo = os.getenv('GITHUB_REPO')
        self.github_workflow_file = os.getenv('GITHUB_WORKFLOW_FILE', 'thallium_tests.yml')
        self.target_column = os.getenv('IMAGEN_CUSTOMER_QA_TARGET_COLUMN')
        self.theme_change_passphrase = os.getenv('IMAGEN_CUSTOMER_QA_THEME_CHANGE_PASSPHRASE')  # Theme change passphrase
        polling_env = os.getenv('IMAGEN_CUSTOMER_QA_POLLING_INTERVAL')
        try:
            self.polling_interval = int(polling_env) if polling_env else None
        except ValueError:
            logger.warning(f"Invalid IMAGEN_CUSTOMER_QA_POLLING_INTERVAL value: {polling_env}, using None")
            self.polling_interval = None
        
        try:
            self.max_retries = int(os.getenv('IMAGEN_CUSTOMER_QA_MAX_RETRIES', '3'))
        except ValueError:
            logger.warning("Invalid IMAGEN_CUSTOMER_QA_MAX_RETRIES value, using default: 3")
            self.max_retries = 3
            
        try:
            self.max_runtime_seconds = int(os.getenv('MAX_RUNTIME_SECONDS', '7200'))  # 2 hours for sequential processing
        except ValueError:
            logger.warning("Invalid MAX_RUNTIME_SECONDS value, using default: 7200")
            self.max_runtime_seconds = 7200
        
        # Validate required config
        required_configs = {
            'IMAGEN_CUSTOMER_QA_AZURE_DEVOPS_ORG': self.azure_devops_org,
            'IMAGEN_CUSTOMER_QA_AZURE_DEVOPS_PROJECT': self.azure_devops_project,
            'IMAGEN_CUSTOMER_QA_AZURE_DEVOPS_TEAM': self.azure_devops_team,  # Now required
            'IMAGEN_CUSTOMER_QA_AZURE_DEVOPS_PAT': self.azure_devops_pat,
            'IMAGEN_CUSTOMER_QA_TARGET_COLUMN': self.target_column
        }
        
        missing_configs = [key for key, value in required_configs.items() if not value]
        if missing_configs:
            raise ValueError(f"Required environment variables are missing: {', '.join(missing_configs)}")
        
        if not self.github_token:
            raise ValueError("GITHUB_TOKEN environment variable is required")
        
        # Warn if theme change passphrase is missing (optional but recommended)
        if not self.theme_change_passphrase:
            logger.warning("IMAGEN_CUSTOMER_QA_THEME_CHANGE_PASSPHRASE not set - theme changes may fail")
        
        logger.info(f"Configuration loaded:")
        logger.info(f"  Azure DevOps Org: {self.azure_devops_org}")
        logger.info(f"  Azure DevOps Project: {self.azure_devops_project}")
        logger.info(f"  Azure DevOps Team: {self.azure_devops_team}")
        logger.info(f"  Target Column: {self.target_column}")
        logger.info(f"  GitHub Repo: {self.github_repo}")
        logger.info(f"  Workflow File: {self.github_workflow_file}")
        logger.info(f"  Theme Change PassPhrase: {'***SET***' if self.theme_change_passphrase else 'NOT SET'}")
        logger.info(f"  Max Runtime: {self.max_runtime_seconds}s")
        if self.polling_interval:
            logger.info(f"  Polling Interval: {self.polling_interval}s")

class AzureDevOpsClient:
    """Azure DevOps API client"""
    
    def __init__(self, config: Config):
        self.config = config
        self.base_url = f"https://dev.azure.com/{config.azure_devops_org}/{config.azure_devops_project}"
        self.headers = {'Content-Type': 'application/json'}
        self.auth = requests.auth.HTTPBasicAuth('', config.azure_devops_pat)

    def get_work_items_in_column(self, column: str) -> list:
        """Get work items in a specific board column with specific criteria"""
        try:
            # Sanitize inputs to prevent injection
            safe_project = self.config.azure_devops_project.replace("'", "''")
            safe_team = self.config.azure_devops_team.replace("'", "''")
            safe_column = column.replace("'", "''")
            
            # WIQL query to find Product Backlog Items in specific column with 'Automated Test' in title
            # Removed area path filter to avoid path issues - filtering by project is sufficient
            wiql_query = {
                "query": f"""
                SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType], 
                       [System.AssignedTo], [System.ChangedDate], [System.Tags], [System.AreaPath]
                FROM WorkItems
                WHERE [System.TeamProject] = '{safe_project}'
                AND [System.BoardColumn] = '{safe_column}'
                AND [System.WorkItemType] = 'Product Backlog Item'
                AND [System.Title] CONTAINS 'Automated Test'
                AND [System.State] <> 'Closed'
                AND [System.State] <> 'Removed'
                ORDER BY [System.ChangedDate] DESC
                """
            }
            
            # Use team-specific WIQL endpoint when team is specified
            # Try the standard endpoint first, team filtering is done in the WIQL query
            url = f"{self.base_url}/_apis/wit/wiql?api-version=7.0"
            
            response = requests.post(url, json=wiql_query, headers=self.headers, auth=self.auth, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                work_items = []
                
                if 'workItems' in result and result['workItems']:
                    # Get detailed work item information with relations
                    ids = [str(item['id']) for item in result['workItems']]
                    details_url = f"{self.base_url}/_apis/wit/workitems?ids={','.join(ids)}&$expand=relations&api-version=7.1"
                    details_response = requests.get(details_url, headers=self.headers, auth=self.auth, timeout=30)
                    
                    if details_response.status_code == 200:
                        details_result = details_response.json()
                        work_items = details_result.get('value', [])
                
                logger.info(f"Found {len(work_items)} work items in column '{column}'")
                return work_items
            else:
                logger.error(f"Failed to query work items: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error querying Azure DevOps: {str(e)}")
            return []

class GitHubClient:
    """GitHub API client for workflow triggering"""
    
    def __init__(self, config: Config):
        self.config = config
        self.headers = {
            'Authorization': f'token {config.github_token}',
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        }

    def trigger_workflow(self, work_item: Dict[str, Any]) -> bool:
        """Trigger thallium_tests.yml workflow with work item context and wait for completion"""
        try:
            # Extract relevant information from work item
            work_item_id = work_item.get('id')
            title = work_item.get('fields', {}).get('System.Title', '')
            tags = work_item.get('fields', {}).get('System.Tags', '')
            work_item_type = work_item.get('fields', {}).get('System.WorkItemType', '')
            
            # Parse customer information and credential requirements
            customer_info = self._extract_customer_from_work_item(work_item)
            
            if not customer_info or not customer_info.get('customer'):
                logger.warning(f"Could not determine customer for work item {work_item_id}")
                return False
            
            # Handle different customer detection results
            if customer_info.get('source') in ['github_secrets', 'pytest_markers'] and not customer_info.get('needs_credentials'):
                # Supported customer with existing support (GitHub Secrets or pytest markers)
                customer = customer_info['customer']
                source_type = customer_info.get('source', 'unknown')
                logger.info(f"[SUCCESS] Triggering workflow for supported customer: {customer} (detected via {source_type})")
            
            elif customer_info.get('needs_credentials') or customer_info.get('source') in ['unsupported', 'no_customer_detected']:
                # Unsupported customer or no customer detected - skip workflow
                customer_name = customer_info.get('customer', 'unknown')
                reason = customer_info.get('reason', 'Unknown reason')
                logger.warning(f"[SKIP] Skipping workflow for unsupported customer '{customer_name}'")
                logger.warning(f"   Reason: {reason}")
                logger.info(f"[INFO] Supported customers: Those with pytest.ini markers or config files")
                logger.info(f"[INFO] Use work item title format: {{CUSTOMER}} Description - Automated Test")
                return False
            
            else:
                # Fallback - should not reach here
                logger.error(f"[ERROR] Unexpected customer detection result: {customer_info}")
                return False

            logger.info(f"[TRIGGER] Triggering thallium_tests.yml for customer: {customer}")
            logger.info(f"[CONTEXT] Work Item {work_item_id}: {title}")
            
            # Determine test parameters based on work item
            workflow_inputs = {
                'customer': customer,
                'browser': 'chrome',  # Default browser
                'markers': self._determine_test_markers(work_item),
                'systemUrl': '',  # Let test use default URL from config
                'test_case_ids': '',
                'test_suite_id': '',
                'test_plan_id': '',
                'pass_phrase': self.config.theme_change_passphrase or ''  # Theme change passphrase
            }
            
            # Extract branch information from work item
            logger.info(f"[BRANCH] Extracting branch information from work item {work_item_id}")
            target_branch = self._extract_branch_from_work_item(work_item)
            if not target_branch:
                # Fallback to default branch if no branch specified in work item
                target_branch = 'main'
                if self.config.github_repo:
                    try:
                        repo_url = f"https://api.github.com/repos/{self.config.github_repo}"
                        repo_response = requests.get(repo_url, headers=self.headers, timeout=30)
                        if repo_response.status_code == 200:
                            repo_data = repo_response.json()
                            target_branch = repo_data.get('default_branch', 'main')
                    except Exception as e:
                        logger.warning(f"Could not determine default branch, using 'main': {e}")
                logger.warning(f"[FALLBACK] No branch specified in work item, using fallback branch: {target_branch}")
            else:
                logger.info(f"[SUCCESS] Using branch from work item: {target_branch}")
            
            # Validate GitHub configuration before proceeding
            if not self.config.github_repo:
                logger.error(f"[ERROR] GITHUB_REPO not configured, cannot trigger workflow for work item {work_item_id}")
                return False
            
            # Trigger the workflow
            url = f"https://api.github.com/repos/{self.config.github_repo}/actions/workflows/{self.config.github_workflow_file}/dispatches"
            payload = {
                'ref': target_branch,
                'inputs': workflow_inputs
            }
            
            logger.info(f"[TRIGGER] Triggering workflow for work item {work_item_id} on branch '{target_branch}' with inputs: {workflow_inputs}")
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            if response.status_code == 204:
                logger.info(f"[SUCCESS] Successfully triggered workflow for work item {work_item_id}")
                
                # Wait for workflow to complete
                workflow_completed = self._wait_for_workflow_completion(work_item_id, customer)
                if workflow_completed:
                    logger.info(f"[SUCCESS] Workflow completed for work item {work_item_id}")
                    return True
                else:
                    logger.warning(f"[TIMEOUT] Workflow did not complete within timeout for work item {work_item_id}")
                    return False  # Return False if workflow timed out
            else:
                logger.error(f"[ERROR] Failed to trigger workflow: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"[ERROR] Error triggering GitHub workflow: {str(e)}")
            return False

    def _wait_for_workflow_completion(self, work_item_id: int, customer: str, max_wait_minutes: int = None) -> bool:
        """Wait for the triggered workflow to complete with dynamic monitoring"""
        try:
            # Use default 30 minutes timeout (reasonable for comprehensive test suites)
            if max_wait_minutes is None:
                max_wait_minutes = 30
            
            logger.info(f"[MONITOR] Starting completion monitoring for work item {work_item_id} (customer: {customer})")
            logger.info(f"[MONITOR] Maximum monitoring time: {max_wait_minutes} minutes")
            logger.info(f"[MONITOR] Sequential processing: Next work item will not start until this completes")
            
            start_time = datetime.now()
            max_wait_seconds = max_wait_minutes * 60
            
            # Dynamic polling intervals based on workflow stage
            initial_check_interval = 15  # Check more frequently at start (workflow startup)
            standard_check_interval = 30  # Standard monitoring interval
            final_check_interval = 60  # Less frequent near timeout
            
            progress_interval = 5 * 60  # Report progress every 5 minutes
            last_progress_report = start_time
            
            # Workflow monitoring state
            workflow_found = False
            workflow_started = False
            api_call_count = 0
            consecutive_errors = 0
            max_consecutive_errors = 3
            
            logger.info(f"[MONITOR] Dynamic monitoring started - completion required for sequential processing")
            
            while (datetime.now() - start_time).total_seconds() < max_wait_seconds:
                try:
                    api_call_count += 1
                    elapsed_seconds = (datetime.now() - start_time).total_seconds()
                    elapsed_minutes = elapsed_seconds / 60
                    
                    # Dynamic check interval based on elapsed time
                    if elapsed_minutes < 2:
                        current_check_interval = initial_check_interval  # Frequent checks during startup
                    elif elapsed_minutes < 20:
                        current_check_interval = standard_check_interval  # Standard monitoring
                    else:
                        current_check_interval = final_check_interval  # Less frequent near timeout
                    
                    # Progress reporting
                    if (datetime.now() - last_progress_report).total_seconds() >= progress_interval:
                        remaining_minutes = (max_wait_seconds - elapsed_seconds) / 60
                        logger.info(f"[MONITOR] Progress: {elapsed_minutes:.1f}m elapsed, {remaining_minutes:.1f}m remaining")
                        logger.info(f"[MONITOR] Workflow state: Found={workflow_found}, Started={workflow_started}")
                        last_progress_report = datetime.now()
                    
                    # Get workflow runs from GitHub API
                    runs_url = f"https://api.github.com/repos/{self.config.github_repo}/actions/workflows/{self.config.github_workflow_file}/runs"
                    params = {'per_page': 20, 'page': 1}  # Recent runs only
                    
                    response = requests.get(runs_url, headers=self.headers, params=params, timeout=30)
                    
                    if response.status_code != 200:
                        consecutive_errors += 1
                        logger.warning(f"[MONITOR] GitHub API request failed ({consecutive_errors}/{max_consecutive_errors}): {response.status_code}")
                        
                        if consecutive_errors >= max_consecutive_errors:
                            logger.error(f"[MONITOR] Too many consecutive API errors, assuming completion")
                            return True  # Assume success to avoid blocking sequential processing
                        
                        time.sleep(current_check_interval)
                        continue
                    
                    # Reset error counter on successful API call
                    consecutive_errors = 0
                    runs_data = response.json()
                    workflow_runs = runs_data.get('workflow_runs', [])
                    
                    # Look for recent runs that match our customer and timeframe
                    matching_run = None
                    for run in workflow_runs:
                        run_name = run.get('name', '')
                        created_at = run.get('created_at', '')
                        
                        # Check if this run matches our customer and was created recently
                        if (customer in run_name and 
                            self._is_recent_run(created_at, elapsed_minutes + 2)):  # Add buffer for timing
                            matching_run = run
                            break
                    
                    if matching_run:
                        if not workflow_found:
                            workflow_found = True
                            logger.info(f"[MONITOR] ✅ Found matching workflow run: {matching_run.get('name', 'Unknown')}")
                        
                        run_status = matching_run.get('status', '')
                        run_conclusion = matching_run.get('conclusion', '')
                        
                        if not workflow_started and run_status in ['queued', 'in_progress']:
                            workflow_started = True
                            logger.info(f"[MONITOR] 🚀 Workflow started execution")
                        
                        if run_status == 'completed':
                            # Workflow completed - check conclusion
                            if run_conclusion == 'success':
                                logger.info(f"[MONITOR] ✅ Workflow completed successfully for work item {work_item_id}")
                                logger.info(f"[SEQUENTIAL] Ready to process next work item")
                                return True
                            elif run_conclusion in ['failure', 'cancelled', 'timed_out']:
                                logger.warning(f"[MONITOR] ❌ Workflow finished with status: {run_conclusion}")
                                logger.info(f"[SEQUENTIAL] Workflow completed (failed) - ready to process next work item")
                                return False
                            else:
                                logger.info(f"[MONITOR] ⚠️ Workflow finished with unknown conclusion: {run_conclusion}")
                                logger.info(f"[SEQUENTIAL] Workflow completed (unknown) - ready to process next work item")
                                return False
                        
                        elif run_status in ['queued', 'in_progress']:
                            logger.debug(f"[MONITOR] Workflow in progress: {run_status}")
                        
                        else:
                            logger.debug(f"[MONITOR] Workflow status: {run_status}")
                    
                    else:
                        # No matching run found yet
                        if elapsed_minutes > 3 and not workflow_found:
                            logger.debug(f"[MONITOR] No matching workflow run found yet (elapsed: {elapsed_minutes:.1f}m)")
                    
                    # Dynamic sleep based on current interval
                    time.sleep(current_check_interval)
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.warning(f"[MONITOR] Error during monitoring ({consecutive_errors}/{max_consecutive_errors}): {e}")
                    
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error(f"[MONITOR] Too many consecutive errors, proceeding to next work item")
                        return False
                    
                    time.sleep(current_check_interval)
            
            # Timeout reached
            elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
            logger.warning(f"[MONITOR] ⏰ Monitoring timeout reached after {elapsed_minutes:.1f} minutes")
            logger.info(f"[SEQUENTIAL] Timeout reached - proceeding to next work item")
            return False
            
        except Exception as e:
            logger.error(f"[MONITOR] Error in workflow completion monitoring: {e}")
            logger.info(f"[SEQUENTIAL] Error occurred - proceeding to next work item")
            return False

    def _is_recent_run(self, created_at: str, minutes: int) -> bool:
        """Check if the workflow run was created within the specified minutes"""
        try:
            # Validate input
            if not created_at or not isinstance(created_at, str):
                logger.warning(f"Invalid created_at value: {created_at}")
                return True  # Assume recent if we can't validate
            
            # Parse ISO format datetime (GitHub API format: 2023-10-01T12:00:00Z)
            normalized_time = created_at
            if normalized_time.endswith('Z'):
                normalized_time = normalized_time[:-1] + '+00:00'
            elif 'Z' in normalized_time:
                normalized_time = normalized_time.replace('Z', '+00:00')
            
            # Simple ISO format parsing with better error handling
            try:
                run_time = datetime.fromisoformat(normalized_time)
            except ValueError:
                # Fallback: try parsing without timezone info
                try:
                    base_time = created_at.split('.')[0].split('Z')[0].split('+')[0]
                    run_time = datetime.fromisoformat(base_time)
                except ValueError:
                    logger.warning(f"Could not parse datetime format: {created_at}")
                    return True  # Assume recent if we can't parse
            
            current_time = datetime.now(run_time.tzinfo) if run_time.tzinfo else datetime.utcnow()
            time_diff = (current_time - run_time).total_seconds() / 60
            return time_diff <= minutes
            
        except Exception as e:
            logger.warning(f"Could not parse run time '{created_at}': {e}")
            # If we can't parse the date, assume it's recent to be safe
            return True

    def _extract_customer_from_work_item(self, work_item: Dict[str, Any]) -> Dict[str, Any]:
        """Extract customer information from work item title using bracket notation"""
        title = work_item.get('fields', {}).get('System.Title', '')
        tags = work_item.get('fields', {}).get('System.Tags', '').lower()
        
        # Extract customer from title using {CUSTOMER} pattern
        customer_match = re.search(r'\{([^}]+)\}', title)
        
        if customer_match:
            customer_name = customer_match.group(1).strip()
            customer_key = customer_name.lower()
            
            # Dynamic customer detection based on pytest.ini markers and config files
            supported_customer = self._get_supported_customer_mapping(customer_key)
            
            if supported_customer:
                logger.info(f"Detected supported customer '{supported_customer}' from title: {customer_name}")
                return {
                    'customer': supported_customer,
                    'needs_credentials': False,
                    'source': 'pytest_markers',
                    'reason': f'Customer {customer_name} found in pytest.ini markers or config files'
                }
            else:
                logger.warning(f"Detected unsupported customer '{customer_name}' from title")
                return {
                    'customer': customer_key,
                    'needs_credentials': True,
                    'source': 'unsupported',
                    'reason': f'Customer {customer_name} not found in pytest.ini markers or config files'
                }
        
        # Fallback: Try to find customer in tags or title keywords (dynamic detection)
        title_lower = title.lower()
        
        # Get all available customers from pytest.ini and config files
        available_customers = self._get_all_available_customers()
        
        # Enhanced fallback: Check for customer names at word boundaries
        for customer_info in available_customers:
            customer_key = customer_info['key']
            customer_value = customer_info['value']
            
            # Check tags
            if customer_key in tags:
                logger.info(f"Detected supported customer '{customer_value}' from tags keyword '{customer_key}'")
                return {
                    'customer': customer_value,
                    'needs_credentials': False,
                    'source': 'pytest_markers',
                    'reason': f'Customer {customer_value} detected from tags keyword'
                }
            
            # Check title with word boundary matching for better accuracy
            keyword_pattern = r'\b' + re.escape(customer_key) + r'\b'
            if re.search(keyword_pattern, title_lower, re.IGNORECASE):
                logger.info(f"Detected supported customer '{customer_value}' from title keyword '{customer_key}'")
                return {
                    'customer': customer_value,
                    'needs_credentials': False,
                    'source': 'pytest_markers',
                    'reason': f'Customer {customer_value} detected from title keyword'
                }
        
        # No customer detected
        logger.warning("No customer pattern {CUSTOMER} found in work item title")
        return {
            'customer': 'unknown',
            'needs_credentials': True,
            'source': 'no_customer_detected',
            'reason': 'No {CUSTOMER} pattern found in work item title, workflow will be skipped'
        }

    def _extract_unknown_customer_name(self, work_item: Dict[str, Any]) -> Optional[str]:
        """Extract customer name for unsupported customers from work item"""
        title = work_item.get('fields', {}).get('System.Title', '')
        tags = work_item.get('fields', {}).get('System.Tags', '')
        
        # Look for customer patterns in title (e.g., "Customer ABC Automated Test")
        # Pattern 1: "Customer XYZ Automated Test"
        customer_match = re.search(r'customer\s+([a-zA-Z0-9_-]+)', title, re.IGNORECASE)
        if customer_match:
            return customer_match.group(1).lower()
        
        # Pattern 2: Look in tags for customer: prefix
        customer_tag_match = re.search(r'customer:([a-zA-Z0-9_-]+)', tags, re.IGNORECASE)
        if customer_tag_match:
            return customer_tag_match.group(1).lower()
        
        # Pattern 3: Look for common customer name patterns in title
        # Extract first capitalized word before "Automated Test"
        title_match = re.search(r'([A-Z][a-zA-Z0-9_-]+)\s+.*?automated\s+test', title, re.IGNORECASE)
        if title_match and title_match.group(1).lower() not in ['test', 'automated', 'system']:
            return title_match.group(1).lower()
        
        return None

    def _extract_branch_from_work_item(self, work_item: Dict[str, Any]) -> Optional[str]:
        """Extract target branch information from work item relations, title, description, or tags"""
        # First check for branch information in Azure DevOps relations (GitHub integration)
        branch_from_relations = self._extract_branch_from_relations(work_item)
        if branch_from_relations:
            return branch_from_relations
        
        # Fall back to text-based extraction from title/description/tags
        return self._extract_branch_from_text_fields(work_item)
    
    def _extract_branch_from_relations(self, work_item: Dict[str, Any]) -> Optional[str]:
        """Extract branch information from Azure DevOps work item relations (GitHub integration)"""
        try:
            relations = work_item.get('relations', [])
            if not relations:
                logger.debug("No relations found in work item")
                return None
            
            logger.info(f"[BRANCH] Checking {len(relations)} relations for GitHub branch links")
            
            for relation in relations:
                rel_type = relation.get('rel', '')
                url = relation.get('url', '')
                attributes = relation.get('attributes', {})
                
                # Look for GitHub Branch artifact links
                if (rel_type == 'ArtifactLink' and 
                    'GitHub/Branch/' in url and 
                    attributes.get('name') == 'GitHub Branch'):
                    
                    # Extract branch name from vstfs URL
                    # Format: vstfs:///GitHub/Branch/{repo-guid}%2F{branch-name}
                    try:
                        import urllib.parse
                        # Split on the GUID pattern and take the branch part
                        if '%2F' in url:
                            branch_part = url.split('%2F')[-1]  # Get everything after the last %2F (URL-encoded /)
                            branch_name = urllib.parse.unquote(branch_part)
                            
                            logger.info(f"[SUCCESS] Found GitHub branch from relations: {branch_name}")
                            logger.info(f"[DETAILS] Relation URL: {url}")
                            logger.info(f"[DETAILS] Relation attributes: {attributes}")
                            
                            return branch_name
                    except Exception as e:
                        logger.warning(f"[WARNING] Failed to parse branch from relation URL: {url}, error: {e}")
                        continue
            
            logger.info("[INFO] No GitHub branch relations found in work item")
            return None
            
        except Exception as e:
            logger.error(f"[ERROR] Error extracting branch from relations: {e}")
            return None
    
    def _extract_branch_from_text_fields(self, work_item: Dict[str, Any]) -> Optional[str]:
        """Extract target branch information from work item title, description, or tags (fallback method)"""
        # Log all values of work_item for debugging
        logger.debug("[DEBUG] work_item full content:")
        for key, value in work_item.items():
            logger.debug(f"    {key}: {value}")

        title = work_item.get('fields', {}).get('System.Title', '')
        description = work_item.get('fields', {}).get('System.Description', '')
        tags = work_item.get('fields', {}).get('System.Tags', '')
        
        # Combine all text sources for branch extraction
        all_text = f"{title} {description} {tags}".lower()
        
        # Pattern 1: Look for [BRANCH: branch-name] notation in title
        branch_bracket_match = re.search(r'\[branch:\s*([a-zA-Z0-9_/-]+)\]', title, re.IGNORECASE)
        if branch_bracket_match:
            branch_name = branch_bracket_match.group(1).strip()
            logger.info(f"Found branch in title bracket notation: {branch_name}")
            return branch_name
        
        # Pattern 2: Look for "branch: branch-name" in description or tags
        branch_field_match = re.search(r'branch:\s*([a-zA-Z0-9_/-]+)', all_text, re.IGNORECASE)
        if branch_field_match:
            branch_name = branch_field_match.group(1).strip()
            logger.info(f"Found branch in description/tags: {branch_name}")
            return branch_name
        
        # Pattern 3: Look for ticket-based branch patterns (e.g., "736019-ai-adoption-card-movement-github-action-trigger")
        # Match patterns like "123456-description-with-dashes"
        ticket_branch_match = re.search(r'\b(\d{6}-[a-zA-Z0-9_-]+)\b', all_text, re.IGNORECASE)
        if ticket_branch_match:
            branch_name = ticket_branch_match.group(1)
            logger.info(f"Found ticket-based branch pattern: {branch_name}")
            return branch_name
        
        # Pattern 4: Look for common branch patterns in title/description
        # Match patterns like "feature/123-description", "bugfix/456", "release/1.2.3"
        branch_pattern_match = re.search(r'\b(feature|bugfix|hotfix|release|develop|staging)\/[a-zA-Z0-9_-]+\b', all_text, re.IGNORECASE)
        if branch_pattern_match:
            branch_name = branch_pattern_match.group(0)
            logger.info(f"Found branch pattern in text: {branch_name}")
            return branch_name
        
        # Pattern 5: Look for PR number and infer branch name
        pr_match = re.search(r'pr[#\s]*(\d+)', all_text, re.IGNORECASE)
        if pr_match:
            pr_number = pr_match.group(1)
            # Could potentially query GitHub API to get branch name from PR number
            # For now, log it for manual investigation
            logger.info(f"Found PR reference #{pr_number}, but cannot auto-determine branch")
        
        # Pattern 6: Check custom Azure DevOps fields for branch information
        # Look for custom fields that might contain branch info
        fields = work_item.get('fields', {})
        for field_name, field_value in fields.items():
            if 'branch' in field_name.lower() and isinstance(field_value, str) and field_value.strip():
                logger.info(f"Found branch in custom field {field_name}: {field_value}")
                return field_value.strip()
        
        logger.info("No branch information found in work item text fields")
        return None

    def _get_available_pytest_markers(self) -> List[str]:
        """Parse pytest.ini to get available customer-specific markers"""
        try:
            # Try to find pytest.ini in the repository - corrected paths
            # Note: Monitor runs from Customer_QA/AI_Adoption_POC, so paths are relative to that
            possible_paths = [
                '../Automated_tests/pytest.ini',  # Customer_QA/Automated_tests/pytest.ini
                '../../CustomerQA/Automated_tests/pytest.ini',  # CustomerQA/Automated_tests/pytest.ini
                'pytest.ini',
                'tests/pytest.ini'
            ]
            
            pytest_ini_content = None
            for path in possible_paths:
                try:
                    with open(path, 'r') as f:
                        pytest_ini_content = f.read()
                    break
                except FileNotFoundError:
                    continue
            
            if not pytest_ini_content:
                logger.warning("Could not find pytest.ini file, using default markers")
                return ['smoke']
            
            # Parse pytest.ini content manually (since it's not standard INI format)
            available_markers = []
            lines = pytest_ini_content.split('\n')
            in_markers_section = False
            
            for line in lines:
                line = line.strip()
                if line.startswith('markers'):
                    in_markers_section = True
                    continue
                elif in_markers_section:
                    if line.startswith('[') or (line and not line.startswith(' ') and not line.startswith('regression_') and ':' not in line):
                        # End of markers section
                        break
                    elif ':' in line:
                        # Extract marker name like "smoke: Run the smoke tests" or "regression_FIFA: Run the regression tests for FIFA customer"
                        marker_name = line.split(':')[0].strip()
                        if marker_name:
                            available_markers.append(marker_name)
                            logger.debug(f"Found pytest marker: {marker_name}")
            
            logger.info(f"Discovered pytest markers: {available_markers}")
            return available_markers
            
        except Exception as e:
            logger.warning(f"Error parsing pytest.ini: {str(e)}, using default markers")
            return ['smoke']

    def _notify_manual_credentials_needed(self, work_item_id: int, customer: str, reason: str) -> None:
        """Notify about unsupported customers requiring manual credentials"""
        try:
            available_markers = self._get_available_pytest_markers()
            
            logger.warning(f"MANUAL INTERVENTION REQUIRED:")
            logger.warning(f"  Work Item: {work_item_id}")
            logger.warning(f"  Customer: {customer}")
            logger.warning(f"  Reason: {reason}")
            logger.warning(f"  Action: Please set up credentials manually and re-run tests")
            logger.warning(f"")
            logger.warning(f"Available pytest markers from pytest.ini:")
            
            if available_markers:
                for marker in available_markers:
                    logger.warning(f"  - {marker}")
            else:
                logger.warning(f"  - smoke: Basic smoke tests (no credentials)")
                logger.warning(f"  - regression: Generic regression tests")
            
            logger.warning(f"")
            logger.warning(f"To add support for customer '{customer}':")
            logger.warning(f"  1. Add GitHub Secrets: IMAGEN_CUSTOMER_QA_{customer.upper()}_USERNAME and IMAGEN_CUSTOMER_QA_{customer.upper()}_PASSWORD")
            logger.warning(f"  2. OR add pytest marker: regression_{customer.upper()}: Run regression tests for {customer} customer")
            logger.warning(f"  3. OR provide manual credentials when running the monitor:")
            logger.warning(f"     NEW_CUSTOMER_NAME='{customer}' NEW_CUSTOMER_USERNAME='user' NEW_CUSTOMER_PASSWORD='pass'")
            logger.warning(f"  4. OR run tests manually with existing markers")
            
            # Future enhancements could:
            # 1. Create GitHub issue for manual setup
            # 2. Send notification email/Slack message  
            # 3. Update work item with comment about credential requirements
            # 4. Trigger a separate workflow for credential collection
            
        except Exception as e:
            logger.error(f"Failed to send notification for work item {work_item_id}: {str(e)}")

    def _determine_test_markers(self, work_item: Dict[str, Any]) -> str:
        """Determine appropriate test markers: smoke + customer regression (if available)"""
        tags = work_item.get('fields', {}).get('System.Tags', '').lower()
        
        # Extract customer information
        customer_info = self._extract_customer_from_work_item(work_item)
        customer = customer_info.get('customer') if customer_info else None
        
        # Always include smoke tests as base
        markers = ['smoke']
        
        if customer:
            # Get available pytest markers from pytest.ini
            available_markers = self._get_available_pytest_markers()
            
            # Check if customer has regression marker in pytest.ini
            # Try both lowercase and uppercase formats
            regression_marker_lower = f'regression_{customer.lower()}'
            regression_marker_upper = f'regression_{customer.upper()}'
            
            if regression_marker_lower in available_markers:
                markers.append(regression_marker_lower)
                logger.info(f"Running smoke + {regression_marker_lower} tests for customer '{customer}'")
            elif regression_marker_upper in available_markers:
                markers.append(regression_marker_upper)
                logger.info(f"Running smoke + {regression_marker_upper} tests for customer '{customer}'")
            else:
                # Check if customer has config file but no pytest marker
                if self._check_customer_config_exists(customer):
                    logger.info(f"Customer '{customer}' has config file but no pytest.ini marker - running smoke tests only")
                else:
                    logger.info(f"Customer '{customer}' not found in config files - running smoke tests only")
        else:
            logger.info("No customer detected - running smoke tests only")
        
        # Priority overrides from tags
        if 'smoke_only' in tags:
            logger.info("Tag 'smoke_only' detected, using 'smoke' marker only")
            return 'smoke'
        elif 'regression_only' in tags and customer:
            available_markers = self._get_available_pytest_markers()
            regression_marker_lower = f'regression_{customer.lower()}'
            regression_marker_upper = f'regression_{customer.upper()}'
            
            if regression_marker_lower in available_markers:
                regression_marker = regression_marker_lower
                logger.info(f"Tag 'regression_only' detected, using '{regression_marker}' marker only")
                return regression_marker
            elif regression_marker_upper in available_markers:
                regression_marker = regression_marker_upper
                logger.info(f"Tag 'regression_only' detected, using '{regression_marker}' marker only")
                return regression_marker
        
        # Return combined markers using OR logic
        result = ' or '.join(markers)
        logger.info(f"Final test markers: {result}")
        logger.warning(f"Using 'or' logic - this will run ALL tests with ANY of these markers: {markers}")
        return result

    def _check_customer_config_exists(self, customer: str) -> bool:
        """Check if customer config file exists in Automated_tests/config/"""
        try:
            # Try different possible config file locations and formats
            # Note: Monitor runs from Customer_QA/AI_Adoption_POC, so paths are relative to that
            possible_paths = [
                f'../Automated_tests/config/{customer.lower()}.json',
                f'../../CustomerQA/Automated_tests/config/{customer.lower()}.json',
                f'../Automated_tests/config/{customer}.json',
                f'../../CustomerQA/Automated_tests/config/{customer}.json',
                f'../Automated_tests/config/{customer.upper()}.json',
                f'../../CustomerQA/Automated_tests/config/{customer.upper()}.json'
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    logger.info(f"Found config file for customer '{customer}' at: {path}")
                    return True
            
            logger.info(f"No config file found for customer '{customer}'")
            return False
            
        except Exception as e:
            logger.warning(f"Error checking config file for customer '{customer}': {e}")
            return False

    def _get_supported_customer_mapping(self, customer_key: str) -> Optional[str]:
        """Get supported customer mapping based on pytest.ini markers and config files"""
        try:
            # Get all available customers
            available_customers = self._get_all_available_customers()
            
            # Look for exact match or close match
            for customer_info in available_customers:
                if customer_info['key'] == customer_key:
                    return customer_info['value']
                
                # Handle special cases like j-league -> jleague
                if customer_key in ['j-league', 'jleague'] and customer_info['key'] in ['j-league', 'jleague']:
                    return customer_info['value']
            
            return None
            
        except Exception as e:
            logger.warning(f"Error getting customer mapping for '{customer_key}': {e}")
            return None

    def _get_all_available_customers(self) -> List[Dict[str, str]]:
        """Get all available customers from pytest.ini markers and config files"""
        try:
            available_customers = []
            
            # 1. Get customers from pytest.ini markers
            available_markers = self._get_available_pytest_markers()
            for marker in available_markers:
                if marker.startswith('regression_'):
                    customer_name = marker.replace('regression_', '').lower()
                    available_customers.append({
                        'key': customer_name,
                        'value': customer_name,
                        'source': 'pytest_marker'
                    })
            
            # 2. Get customers from config files
            import glob
            config_paths = [
                '../Automated_tests/config/',
                '../../CustomerQA/Automated_tests/config/'
            ]
            
            for config_path in config_paths:
                if os.path.exists(config_path):
                    config_files = glob.glob(os.path.join(config_path, '*.json'))
                    for config_file in config_files:
                        customer_name = os.path.splitext(os.path.basename(config_file))[0].lower()
                        # Avoid duplicates
                        if not any(c['key'] == customer_name for c in available_customers):
                            available_customers.append({
                                'key': customer_name,
                                'value': customer_name,
                                'source': 'config_file'
                            })
            
            # 3. Add common variations and aliases
            aliases = {
                'j-league': 'jleague',
                'nba': 'sd2'  # Keep this one mapping for backward compatibility
            }
            
            for alias_key, alias_value in aliases.items():
                # Only add if the target customer exists
                if any(c['key'] == alias_value for c in available_customers):
                    if not any(c['key'] == alias_key for c in available_customers):
                        available_customers.append({
                            'key': alias_key,
                            'value': alias_value,
                            'source': 'alias'
                        })
            
            logger.info(f"Found {len(available_customers)} available customers from pytest.ini and config files")
            for customer in available_customers:
                logger.debug(f"  {customer['key']} -> {customer['value']} (from {customer['source']})")
            
            return available_customers
            
        except Exception as e:
            logger.warning(f"Error getting available customers: {e}")
            # Fallback to minimal set if there's an error
            return [
                {'key': 'fifa', 'value': 'fifa', 'source': 'fallback'},
                {'key': 'ioc', 'value': 'ioc', 'source': 'fallback'},
                {'key': 'qc7', 'value': 'qc7', 'source': 'fallback'},
                {'key': 'demo', 'value': 'demo', 'source': 'fallback'}
            ]

class AzureDevOpsMCPServer:
    """Thallium ADO Card Movement Monitor - Azure DevOps MCP Server implementing Model Context Protocol"""
    
    def __init__(self):
        self.config = Config()
        self.azure_client = AzureDevOpsClient(self.config)
        self.github_client = GitHubClient(self.config)
        self.processed_items: Set[int] = set()
        self.server = Server("thallium-ado-card-movement-monitor")
        self._setup_tools()
    
    def _setup_tools(self):
        """Setup MCP tools for Azure DevOps operations"""
        
        # Tool for monitoring work items in column
        @self.server.call_tool()
        async def monitor_work_items_in_column(column: str = "Testing") -> List[CallToolResult]:
            """Monitor work items in specified Azure DevOps board column"""
            try:
                work_items = self.azure_client.get_work_items_in_column(column)
                result = {
                    "column": column,
                    "work_items_found": len(work_items),
                    "work_items": [
                        {
                            "id": item.get("id"),
                            "title": item.get("fields", {}).get("System.Title", ""),
                            "state": item.get("fields", {}).get("System.State", ""),
                            "assigned_to": item.get("fields", {}).get("System.AssignedTo", {}).get("displayName", ""),
                            "changed_date": item.get("fields", {}).get("System.ChangedDate", "")
                        }
                        for item in work_items
                    ]
                }
                return [CallToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])]
            except Exception as e:
                error_result = {"error": f"Failed to monitor work items: {str(e)}"}
                return [CallToolResult(content=[TextContent(type="text", text=json.dumps(error_result, indent=2))], isError=True)]
        
        # Tool for extracting customer from work item
        @self.server.call_tool()
        async def extract_customer_from_work_item(work_item_id: int) -> List[CallToolResult]:
            """Extract customer information from work item using bracket notation and fallback detection"""
            try:
                work_items = self.azure_client.get_work_items_in_column("Testing")
                work_item = next((item for item in work_items if item.get("id") == work_item_id), None)
                
                if not work_item:
                    error_result = {"error": f"Work item {work_item_id} not found"}
                    return [CallToolResult(content=[TextContent(type="text", text=json.dumps(error_result, indent=2))], isError=True)]
                
                customer_info = self.github_client._extract_customer_from_work_item(work_item)
                return [CallToolResult(content=[TextContent(type="text", text=json.dumps(customer_info, indent=2))])]
            except Exception as e:
                error_result = {"error": f"Failed to extract customer: {str(e)}"}
                return [CallToolResult(content=[TextContent(type="text", text=json.dumps(error_result, indent=2))], isError=True)]
        
        # Tool for triggering thallium workflow
        @self.server.call_tool()
        async def trigger_thallium_workflow(work_item_id: int) -> List[CallToolResult]:
            """Trigger thallium_tests.yml workflow for specific work item"""
            try:
                work_items = self.azure_client.get_work_items_in_column("Testing")
                work_item = next((item for item in work_items if item.get("id") == work_item_id), None)
                
                if not work_item:
                    error_result = {"error": f"Work item {work_item_id} not found"}
                    return [CallToolResult(content=[TextContent(type="text", text=json.dumps(error_result, indent=2))], isError=True)]
                
                success = self.github_client.trigger_workflow(work_item)
                result = {
                    "work_item_id": work_item_id,
                    "workflow_triggered": success,
                    "title": work_item.get("fields", {}).get("System.Title", "")
                }
                return [CallToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])]
            except Exception as e:
                error_result = {"error": f"Failed to trigger workflow: {str(e)}"}
                return [CallToolResult(content=[TextContent(type="text", text=json.dumps(error_result, indent=2))], isError=True)]
        
        # Tool for full monitoring cycle
        @self.server.call_tool()
        async def run_monitoring_cycle(column: str = "Testing", max_items: int = 10) -> List[CallToolResult]:
            """Run complete monitoring cycle: check work items, extract customers, trigger workflows"""
            try:
                work_items = self.azure_client.get_work_items_in_column(column)
                results = []
                processed_count = 0
                
                for work_item in work_items[:max_items]:
                    work_item_id = work_item.get('id')
                    title = work_item.get('fields', {}).get('System.Title', '')
                    
                    # Skip if already processed
                    if work_item_id in self.processed_items:
                        continue
                    
                    # Extract customer information
                    customer_info = self.github_client._extract_customer_from_work_item(work_item)
                    
                    # Only trigger workflow for supported customers
                    if not customer_info.get('needs_credentials', True):
                        success = self.github_client.trigger_workflow(work_item)
                        if success:
                            self.processed_items.add(work_item_id)  # Only mark processed after completion
                            processed_count += 1
                        workflow_triggered = success
                    else:
                        workflow_triggered = False
                    
                    results.append({
                        "work_item_id": work_item_id,
                        "title": title,
                        "customer_info": customer_info,
                        "workflow_triggered": workflow_triggered,
                        "processed": work_item_id in self.processed_items
                    })
                
                summary = {
                    "monitoring_cycle_complete": True,
                    "column_monitored": column,
                    "total_work_items": len(work_items),
                    "items_processed_this_cycle": processed_count,
                    "total_items_processed": len(self.processed_items),
                    "results": results
                }
                
                return [CallToolResult(content=[TextContent(type="text", text=json.dumps(summary, indent=2))])]
            except Exception as e:
                error_result = {"error": f"Failed to run monitoring cycle: {str(e)}"}
                return [CallToolResult(content=[TextContent(type="text", text=json.dumps(error_result, indent=2))], isError=True)]
    
    async def list_tools(self) -> ListToolsResult:
        """Return list of available MCP tools"""
        tools = [
            Tool(
                name="monitor_work_items_in_column",
                description="Monitor work items in specified Azure DevOps board column",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "column": {
                            "type": "string",
                            "description": "Column name to monitor (default: Testing)",
                            "default": "Testing"
                        }
                    }
                }
            ),
            Tool(
                name="extract_customer_from_work_item",
                description="Extract customer information from work item using bracket notation and fallback detection",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "work_item_id": {
                            "type": "integer",
                            "description": "Work item ID to extract customer from"
                        }
                    },
                    "required": ["work_item_id"]
                }
            ),
            Tool(
                name="trigger_thallium_workflow",
                description="Trigger thallium_tests.yml workflow for specific work item",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "work_item_id": {
                            "type": "integer",
                            "description": "Work item ID to trigger workflow for"
                        }
                    },
                    "required": ["work_item_id"]
                }
            ),
            Tool(
                name="run_monitoring_cycle",
                description="Run complete monitoring cycle: check work items, extract customers, trigger workflows",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "column": {
                            "type": "string",
                            "description": "Column name to monitor (default: Testing)",
                            "default": "Testing"
                        },
                        "max_items": {
                            "type": "integer",
                            "description": "Maximum number of items to process (default: 10)",
                            "default": 10
                        }
                    }
                }
            )
        ]
        return ListToolsResult(tools=tools)

# Legacy class for backward compatibility with GitHub Actions workflow
class WorkItemMonitor:
    """Legacy monitor class for GitHub Actions compatibility"""
    
    def __init__(self):
        self.config = Config()
        self.azure_client = AzureDevOpsClient(self.config)
        self.github_client = GitHubClient(self.config)
        self.processed_items: Set[int] = set()
        self.running = True
        self.start_time = datetime.now()
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def _should_continue_running(self) -> bool:
        """Check if monitor should continue running based on time limits"""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        return self.running and elapsed < self.config.max_runtime_seconds

    def _validate_work_item_criteria(self, work_item: Dict[str, Any]) -> bool:
        """Validate that work item meets all criteria for test execution"""
        fields = work_item.get('fields', {})
        
        # Get work item details
        work_item_id = work_item.get('id')
        title = fields.get('System.Title', '')
        work_item_type = fields.get('System.WorkItemType', '')
        state = fields.get('System.State', '')
        
        # Criteria 1: Must be Product Backlog Item
        if work_item_type != 'Product Backlog Item':
            logger.info(f"Work item {work_item_id} is type '{work_item_type}', not 'Product Backlog Item' - skipping")
            return False
        
        # Criteria 2: Title must contain 'Automated Test'
        if 'Automated Test' not in title:
            logger.info(f"Work item {work_item_id} title '{title}' does not contain 'Automated Test' - skipping")
            return False
        
        # Criteria 3: Must not be closed or removed (already handled in WIQL query, but double-check)
        if state in ['Closed', 'Removed']:
            logger.info(f"Work item {work_item_id} is in state '{state}' - skipping")
            return False
        
        # All criteria met
        logger.info(f"[SUCCESS] Work item {work_item_id} meets all criteria:")
        logger.info(f"   - Type: {work_item_type}")
        logger.info(f"   - Title: {title}")
        logger.info(f"   - State: {state}")
        
        return True

    def monitor_loop(self):
        """Main monitoring loop"""
        logger.info("Starting Thallium ADO card movement monitoring...")
        logger.info(f"Target column: {self.config.target_column}")
        logger.info(f"Max runtime: {self.config.max_runtime_seconds}s")
        logger.info("[MONITOR] MONITORING CRITERIA:")
        logger.info("   - Work Item Type: Product Backlog Item ONLY")
        logger.info("   - Title must contain: 'Automated Test'")
        logger.info("   - Column: Testing")
        logger.info("   - Excludes: Bugs, Tasks, Issues, and other work item types")
        logger.info("[SCHEDULE] SCHEDULE: Weekdays execution (Mon-Fri, when run via GitHub Actions)")
        logger.info("[SEQUENTIAL] PROCESSING: True sequential execution - each workflow completes before next starts")
        logger.info("[SEQUENTIAL] NO EXPLICIT DELAYS: Uses completion-based monitoring, not time-based waits")
        
        try:
            # Get work items in target column (single check for weekdays run)
            work_items = self.azure_client.get_work_items_in_column(self.config.target_column)
            
            # Process new items with additional validation
            new_items = []
            for item in work_items:
                item_id = item.get('id')
                if item_id and item_id not in self.processed_items:
                    # Additional validation to ensure criteria are met
                    if self._validate_work_item_criteria(item):
                        new_items.append(item)
                        # Don't add to processed_items yet - only after successful completion
                    else:
                        logger.info(f"Work item {item_id} does not meet criteria, skipping")
            
            if new_items:
                logger.info(f"Found {len(new_items)} new qualifying work items to process")
                logger.info("[SEQUENTIAL] Processing work items one at a time - each workflow must complete before starting next")
                logger.info("[SEQUENTIAL] No explicit wait times - using completion-based monitoring")
                
                # Process each work item sequentially with completion monitoring
                for i, item in enumerate(new_items, 1):
                    item_title = item.get('fields', {}).get('System.Title', 'Unknown')
                    item_type = item.get('fields', {}).get('System.WorkItemType', 'Unknown')
                    item_id = item.get('id')
                    
                    logger.info(f"[SEQUENTIAL] Starting work item {i}/{len(new_items)}: {item_id}")
                    logger.info(f"[SEQUENTIAL]   Type: {item_type}")
                    logger.info(f"[SEQUENTIAL]   Title: {item_title}")
                    
                    # Trigger workflow and monitor completion (blocking until done)
                    logger.info(f"[SEQUENTIAL] Triggering workflow for work item {item_id} - will wait for completion")
                    success = self.github_client.trigger_workflow(item)
                    
                    if success:
                        logger.info(f"[SEQUENTIAL] ✅ Work item {item_id} completed successfully")
                        logger.info(f"[SEQUENTIAL] ✅ Ready to process next work item ({i}/{len(new_items)} complete)")
                        self.processed_items.add(item_id)
                    else:
                        logger.error(f"[SEQUENTIAL] ❌ Work item {item_id} failed or timed out")
                        logger.error(f"[SEQUENTIAL] ❌ Work item {item_id} will be retried in next monitor run")
                        logger.info(f"[SEQUENTIAL] ➡️ Continuing to next work item ({i}/{len(new_items)} processed)")
                    
                    # Clear indication that we're moving to next item
                    if i < len(new_items):
                        next_item = new_items[i]
                        next_id = next_item.get('id')
                        logger.info(f"[SEQUENTIAL] 🔄 Previous workflow completed, starting work item {next_id}")
                
                logger.info(f"[SEQUENTIAL] ✅ All {len(new_items)} work items processed sequentially")
            else:
                logger.info("No new qualifying work items found")
                
        except Exception as e:
            logger.error(f"Error in monitoring execution: {str(e)}")
        
        logger.info("Weekdays monitoring cycle completed")

    def save_state(self):
        """Save processed items state with atomic write"""
        try:
            state = {
                'processed_items': list(self.processed_items),
                'last_run': datetime.now().isoformat(),
                'config': {
                    'target_column': self.config.target_column,
                    'azure_devops_project': self.config.azure_devops_project
                }
            }
            
            # Atomic write: write to temp file first, then rename
            temp_file = 'processed_items.json.tmp'
            with open(temp_file, 'w') as f:
                json.dump(state, f, indent=2)
            
            # Atomic rename operation
            if os.path.exists('processed_items.json'):
                os.replace(temp_file, 'processed_items.json')
            else:
                os.rename(temp_file, 'processed_items.json')
            
            logger.info(f"Saved state: {len(self.processed_items)} processed items")
        except Exception as e:
            logger.error(f"Failed to save state: {str(e)}")
            # Clean up temp file if it exists
            if os.path.exists('processed_items.json.tmp'):
                try:
                    os.remove('processed_items.json.tmp')
                except OSError:
                    pass

    def load_state(self):
        """Load processed items state with validation"""
        try:
            if os.path.exists('processed_items.json'):
                with open('processed_items.json', 'r') as f:
                    state = json.load(f)
                
                # Validate state structure
                if not isinstance(state, dict):
                    logger.warning("Invalid state file format, starting fresh")
                    return
                
                processed_items = state.get('processed_items', [])
                if not isinstance(processed_items, list):
                    logger.warning("Invalid processed_items format, starting fresh")
                    return
                
                # Validate that all items are integers (work item IDs)
                valid_items = []
                for item in processed_items:
                    if isinstance(item, int) and item > 0:
                        valid_items.append(item)
                    else:
                        logger.warning(f"Skipping invalid work item ID: {item}")
                
                self.processed_items = set(valid_items)
                logger.info(f"Loaded state: {len(self.processed_items)} processed items")
                
                # Log last run info if available
                if 'last_run' in state:
                    logger.info(f"Last run: {state['last_run']}")
            else:
                logger.info("No existing state file found, starting fresh")
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"Could not load state file, starting fresh: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to load state: {str(e)}")

async def main_mcp():
    """Main function for MCP server mode"""
    logger.info("Starting Thallium ADO Card Movement Monitor - Azure DevOps MCP Server...")
    server_instance = AzureDevOpsMCPServer()
    
    async with stdio_server() as (read_stream, write_stream):
        await server_instance.server.run(
            read_stream, 
            write_stream, 
            server_instance.server.create_initialization_options()
        )

def main():
    """Main function - supports both MCP server mode and legacy monitor mode"""
    # Check if running in MCP server mode
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        asyncio.run(main_mcp())
        return
    
    # Legacy monitor mode for GitHub Actions compatibility
    try:
        logger.info("ADO Card Movement Monitor starting in legacy mode...")
        monitor = WorkItemMonitor()
        monitor.load_state()
        monitor.monitor_loop()
        monitor.save_state()
        logger.info("Thallium ADO card movement monitoring completed successfully")
    except Exception as e:
        logger.error(f"Monitor failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
