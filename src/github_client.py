"""
GitHub API client for triggering workflows
"""
import logging
from typing import Dict, Any, Optional, List
import asyncio
import aiohttp
import json
from datetime import datetime
from github import Github
from github.GithubException import GithubException

from models import GitHubWorkflowTrigger, GitHubWorkflowRun, TestCaseInfo
from config import config

logger = logging.getLogger(__name__)


class GitHubClient:
    """GitHub API client for workflow operations"""
    
    def __init__(self):
        self.token = config.github_token
        self.repo_name = config.github_repo
        self.workflow_file = config.github_workflow_file
        
        # Initialize GitHub client
        self.github = Github(self.token)
        self.repo = self.github.get_repo(self.repo_name)
        
    async def ensure_workflow_on_branch(self, branch_name: str, workflow_file: str) -> bool:
        """Copy workflow file from main branch to target branch if it doesn't exist"""
        try:
            workflow_path = f".github/workflows/{workflow_file}"
            
            # Check if workflow file already exists on target branch
            try:
                self.repo.get_contents(workflow_path, ref=branch_name)
                logger.info(f"Workflow file {workflow_file} already exists on branch {branch_name}")
                return True
            except GithubException:
                logger.info(f"Workflow file {workflow_file} not found on branch {branch_name}, copying from main")
            
            # Get workflow content from main branch
            try:
                main_workflow = self.repo.get_contents(workflow_path, ref=config.github_default_branch)
                workflow_content = main_workflow.decoded_content.decode('utf-8')
                logger.info(f"Retrieved workflow content from main branch")
            except GithubException as e:
                logger.error(f"Failed to get workflow from main branch: {e}")
                return False
            
            # Copy workflow file to target branch
            try:
                self.repo.create_file(
                    path=workflow_path,
                    message=f"Copy workflow file for branch {branch_name}",
                    content=workflow_content,
                    branch=branch_name
                )
                logger.info(f"Successfully copied workflow file to branch {branch_name}")
                return True
            except GithubException as e:
                logger.error(f"Failed to copy workflow file to branch {branch_name}: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Error ensuring workflow on branch {branch_name}: {e}")
            return False

    async def trigger_workflow(
        self,
        workflow_file: str,
        ref: str,
        inputs: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Trigger GitHub Actions workflow on specific branch"""
        try:
            logger.info(f"Triggering workflow {workflow_file} on branch {ref}")
            
            # Validate branch exists
            try:
                self.repo.get_branch(ref)
                logger.info(f"Branch {ref} exists")
            except GithubException:
                logger.warning(f"Branch {ref} not found, falling back to {config.github_default_branch}")
                ref = config.github_default_branch
            
            # If not the default branch, ensure workflow file exists on target branch
            if ref != config.github_default_branch:
                logger.info(f"Ensuring workflow file exists on branch {ref}")
                if not await self.ensure_workflow_on_branch(ref, workflow_file):
                    logger.warning(f"Failed to ensure workflow on branch {ref}, falling back to {config.github_default_branch}")
                    ref = config.github_default_branch
            
            # Prepare workflow inputs (ensure all values are strings)
            workflow_inputs = {}
            if inputs:
                workflow_inputs = {str(k): str(v) for k, v in inputs.items()}
            
            # Create workflow dispatch event
            workflow = self.repo.get_workflow(workflow_file)
            logger.info(f"Found workflow: {workflow.name} (ID: {workflow.id})")
            
            result = workflow.create_dispatch(
                ref=ref,
                inputs=workflow_inputs
            )
            
            logger.info(f"Dispatch result: {result}")
            if result is False:
                logger.error(f"Workflow dispatch failed - GitHub API returned False")
                logger.error(f"This usually indicates permission issues or workflow problems")
                return False
            
            logger.info(f"Successfully triggered workflow {workflow_file} on branch {ref}")
            logger.info(f"Workflow inputs sent: {workflow_inputs}")
            return True
            
        except GithubException as e:
            logger.error(f"GitHub API error triggering workflow: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Error triggering workflow: {e}")
            return False
    
    async def get_workflow_runs(
        self,
        status: Optional[str] = None,
        limit: int = 10
    ) -> List[GitHubWorkflowRun]:
        """Get recent workflow runs"""
        try:
            workflow = self.repo.get_workflow(self.workflow_file)
            runs = workflow.get_runs()
            
            result = []
            count = 0
            for run in runs:
                if count >= limit:
                    break
                
                if status and run.status != status:
                    continue
                
                workflow_run = GitHubWorkflowRun(
                    id=run.id,
                    name=run.name,
                    status=run.status,
                    conclusion=run.conclusion,
                    html_url=run.html_url,
                    created_at=run.created_at,
                    updated_at=run.updated_at
                )
                result.append(workflow_run)
                count += 1
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting workflow runs: {e}")
            return []
    
    async def get_workflow_run_status(self, run_id: int) -> Optional[GitHubWorkflowRun]:
        """Get status of a specific workflow run"""
        try:
            run = self.repo.get_workflow_run(run_id)
            
            return GitHubWorkflowRun(
                id=run.id,
                name=run.name,
                status=run.status,
                conclusion=run.conclusion,
                html_url=run.html_url,
                created_at=run.created_at,
                updated_at=run.updated_at
            )
            
        except Exception as e:
            logger.error(f"Error getting workflow run {run_id}: {e}")
            return None
    
    async def check_workflow_exists(self) -> bool:
        """Check if the specified workflow file exists"""
        try:
            workflow = self.repo.get_workflow(self.workflow_file)
            return workflow is not None
        except:
            return False
    
    async def check_workflow_exists_on_branch(self, workflow_file: str, branch_name: str) -> bool:
        """Check if the specified workflow file exists on a specific branch"""
        try:
            # Try to get the workflow file content on the specific branch
            content = self.repo.get_contents(f".github/workflows/{workflow_file}", ref=branch_name)
            return content is not None
        except Exception as e:
            logger.debug(f"Workflow file {workflow_file} not found on branch {branch_name}: {e}")
            return False
    
    async def _check_branch_exists(self, branch_name: str) -> bool:
        """Check if a specific branch exists in the repository"""
        try:
            self.repo.get_branch(branch_name)
            return True
        except GithubException:
            return False
        except Exception as e:
            logger.error(f"Error checking branch {branch_name}: {e}")
            return False
    
    async def get_available_branches(self) -> List[str]:
        """Get list of available branches in the repository"""
        try:
            branches = self.repo.get_branches()
            return [branch.name for branch in branches]
        except Exception as e:
            logger.error(f"Error getting branches: {e}")
            return ["main"]
    
    def generate_test_command(self, test_cases: List[TestCaseInfo]) -> str:
        """Generate pytest command for the test cases"""
        if not test_cases:
            return "pytest"
        
        # Extract test paths and names
        test_specs = []
        for tc in test_cases:
            if tc.test_path:
                test_specs.append(f"{tc.test_path}::{tc.test_name}")
            else:
                test_specs.append(f"**/test*{tc.test_id}*")
        
        if test_specs:
            return f"pytest {' '.join(test_specs)} -v"
        else:
            return "pytest -k test"
    
    async def create_github_issue_comment(
        self,
        work_item_id: int,
        workflow_run: GitHubWorkflowRun,
        test_results: Optional[str] = None
    ) -> bool:
        """Create a comment on GitHub issues related to the work item"""
        try:
            # Search for issues that mention the work item ID
            query = f"repo:{self.repo_name} is:issue {work_item_id}"
            issues = self.github.search_issues(query)
            
            comment_body = f"""
🔄 **Automated Test Triggered**

**Work Item ID**: {work_item_id}
**Workflow Run**: [{workflow_run.name}]({workflow_run.html_url})
**Status**: {workflow_run.status}
**Triggered**: {workflow_run.created_at}

This test was automatically triggered when the work item was moved to the Testing column.
"""
            
            if test_results:
                comment_body += f"\n**Test Results**:\n```\n{test_results}\n```"
            
            for issue in issues:
                issue.create_comment(comment_body)
                logger.info(f"Created comment on issue #{issue.number}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error creating GitHub issue comment: {e}")
            return False
