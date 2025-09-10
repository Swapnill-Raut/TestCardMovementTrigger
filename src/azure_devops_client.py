"""
Azure DevOps API client for work item monitoring
"""
import logging
import re
import urllib.parse
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import asyncio
import aiohttp
import base64
from azure.devops.connection import Connection
from azure.devops.v7_1.work_item_tracking import WorkItemTrackingClient
from msrest.authentication import BasicAuthentication

from .models import WorkItem, WorkItemUpdate
from .config import config

logger = logging.getLogger(__name__)


class AzureDevOpsClient:
    """Azure DevOps API client for work item operations"""
    
    def __init__(self):
        self.organization_url = f"https://dev.azure.com/{config.azure_devops_org}"
        self.project = config.azure_devops_project
        self.pat = config.azure_devops_pat
        
        # Create connection
        credentials = BasicAuthentication('', self.pat)
        self.connection = Connection(
            base_url=self.organization_url,
            creds=credentials
        )
        
        # Get work item tracking client
        self.wit_client: WorkItemTrackingClient = self.connection.clients.get_work_item_tracking_client()
        
        # Cache for tracking work item states
        self._work_item_cache: Dict[int, WorkItem] = {}
        
    async def get_work_items_in_column(self, column_name: str) -> List[WorkItem]:
        """Get all work items currently in a specific column"""
        try:
            # WIQL query to get work items in specific column
            wiql_query = f"""
            SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType],
                   [System.AssignedTo], [System.CreatedDate], [System.ChangedDate],
                   [System.BoardColumn], [System.Description], [Microsoft.VSTS.Common.AcceptanceCriteria]
            FROM WorkItems
            WHERE [System.TeamProject] = '{self.project}'
            AND [System.BoardColumn] = '{column_name}'
            AND [System.State] <> 'Closed'
            ORDER BY [System.ChangedDate] DESC
            """
            
            # Execute query
            query_result = self.wit_client.query_by_wiql(
                wiql={"query": wiql_query}
            )
            
            if not query_result.work_items:
                return []
            
            # Get work item IDs
            work_item_ids = [item.id for item in query_result.work_items]
            
            # Get detailed work item information
            work_items = self.wit_client.get_work_items(
                ids=work_item_ids,
                expand="All"
            )
            
            result = []
            for wi in work_items:
                work_item = self._convert_to_work_item(wi)
                result.append(work_item)
                
            return result
            
        except Exception as e:
            logger.error(f"Error getting work items in column {column_name}: {e}")
            return []
    
    async def get_work_item_by_id(self, work_item_id: int) -> Optional[WorkItem]:
        """Get a specific work item by ID"""
        try:
            wi = self.wit_client.get_work_item(
                id=work_item_id,
                expand="All"
            )
            return self._convert_to_work_item(wi)
            
        except Exception as e:
            logger.error(f"Error getting work item {work_item_id}: {e}")
            return None
    
    async def monitor_work_item_changes(self, polling_interval: int = 30) -> List[WorkItemUpdate]:
        """Monitor work items for column changes"""
        updates = []
        
        try:
            # Get current work items in target column
            current_items = await self.get_work_items_in_column(config.target_column)
            
            # Check for new items in the target column
            for item in current_items:
                if item.id not in self._work_item_cache:
                    # New item in target column
                    update = WorkItemUpdate(
                        work_item_id=item.id,
                        previous_column=None,
                        current_column=item.board_column or config.target_column,
                        timestamp=item.changed_date,
                        updated_by=item.assigned_to or "Unknown",
                        change_type="moved_to_target_column"
                    )
                    updates.append(update)
                    logger.info(f"Work item {item.id} moved to {config.target_column}")
                
                # Update cache
                self._work_item_cache[item.id] = item
            
            # Clean up cache for items no longer in target column
            current_ids = {item.id for item in current_items}
            items_to_remove = [
                item_id for item_id in self._work_item_cache.keys()
                if item_id not in current_ids
            ]
            
            for item_id in items_to_remove:
                del self._work_item_cache[item_id]
                logger.debug(f"Removed work item {item_id} from cache")
            
            return updates
            
        except Exception as e:
            logger.error(f"Error monitoring work item changes: {e}")
            return []
    
    def _convert_to_work_item(self, azure_work_item) -> WorkItem:
        """Convert Azure DevOps work item to our model"""
        fields = azure_work_item.fields
        
        # Extract tags
        tags = []
        if 'System.Tags' in fields and fields['System.Tags']:
            tags = [tag.strip() for tag in fields['System.Tags'].split(';')]
        
        # Extract branch information from various possible fields
        branch_name = self._extract_branch_info(azure_work_item, fields)
        
        work_item = WorkItem(
            id=azure_work_item.id,
            title=fields.get('System.Title', ''),
            state=fields.get('System.State', ''),
            work_item_type=fields.get('System.WorkItemType', ''),
            assigned_to=self._extract_display_name(fields.get('System.AssignedTo')),
            created_date=self._parse_azure_date(fields.get('System.CreatedDate')),
            changed_date=self._parse_azure_date(fields.get('System.ChangedDate')),
            board_column=fields.get('System.BoardColumn'),
            description=fields.get('System.Description', ''),
            acceptance_criteria=fields.get('Microsoft.VSTS.Common.AcceptanceCriteria', ''),
            test_cases=[],  # No test case extraction for now
            tags=tags,
            custom_fields={
                k: v for k, v in fields.items()
                if k.startswith('Custom.') or k.startswith('Microsoft.VSTS.')
            },
            associated_branch=branch_name
        )
        
        # Add branch information to custom fields as well for backward compatibility
        if branch_name:
            work_item.custom_fields['Associated_Branch'] = branch_name
        
        return work_item
    
    def _extract_branch_info(self, azure_work_item, fields) -> Optional[str]:
        """Extract branch information from work item"""
        branch_name = None
        
        # Debug: Log all available fields to understand what's available
        logger.debug(f"Available fields for work item {azure_work_item.id}:")
        for key, value in fields.items():
            if 'branch' in key.lower() or 'git' in key.lower() or 'source' in key.lower():
                logger.debug(f"  {key}: {value}")
        
        # Strategy 1: Check development links (most reliable)
        try:
            if hasattr(azure_work_item, 'relations') and azure_work_item.relations:
                logger.debug(f"Found {len(azure_work_item.relations)} relations for work item {azure_work_item.id}")
                for relation in azure_work_item.relations:
                    logger.debug(f"  Relation: {relation.rel} -> {relation.url}")
                    
                    # Handle GitHub branch links (new format we're seeing)
                    if relation.rel == "ArtifactLink" and "GitHub/Branch/" in relation.url:
                        # Extract branch from GitHub artifact link
                        # Format: vstfs:///GitHub/Branch/e0f53538-4f49-4cc9-a7e0-5c2a7530a4d2%2FTestADO2
                        try:
                            # Split on the last %2F (URL-encoded /) to get the branch name
                            import urllib.parse
                            if '%2F' in relation.url:
                                branch_name = relation.url.split('%2F')[-1]
                                # URL decode the branch name
                                branch_name = urllib.parse.unquote(branch_name)
                                logger.debug(f"✅ Found branch from GitHub relation: {branch_name}")
                                return branch_name
                        except Exception as e:
                            logger.debug(f"Error parsing GitHub branch URL: {e}")
                            continue
                    
                    # Handle traditional Git links
                    elif relation.rel == "ArtifactLink" and "vstfs:///Git/" in relation.url:
                        # Extract branch from Git artifact link
                        # Format: vstfs:///Git/Ref/{project}%2F{repo}%2FGB{branch}
                        if "GB" in relation.url:
                            branch_part = relation.url.split("GB")[-1]
                            branch_name = branch_part.replace("%2F", "/")
                            logger.debug(f"✅ Found branch from Git relation: {branch_name}")
                            return branch_name
        except Exception as e:
            logger.debug(f"Error extracting branch from relations: {e}")
        
        # Strategy 2: Check custom fields for branch info
        if not branch_name:
            branch_fields = [
                'Custom.Branch',
                'Microsoft.VSTS.Common.Branch',
                'System.Branch',
                'Custom.SourceBranch',
                'Custom.FeatureBranch',
                'Microsoft.VSTS.Build.IntegrationBuild',
                'Microsoft.VSTS.Build.FoundIn'
            ]
            
            for field in branch_fields:
                if field in fields and fields[field]:
                    branch_name = str(fields[field]).strip()
                    logger.info(f"Found branch from field {field}: {branch_name}")
                    break
        
        # Strategy 3: Extract from description or acceptance criteria
        if not branch_name:
            content = f"{fields.get('System.Description', '')} {fields.get('Microsoft.VSTS.Common.AcceptanceCriteria', '')}"
            
            # Common branch patterns
            branch_patterns = [
                r'branch[:\s]+([a-zA-Z0-9\-_/]+)',
                r'feature[/\s]+([a-zA-Z0-9\-_/]+)',
                r'refs/heads/([a-zA-Z0-9\-_/]+)',
                r'origin/([a-zA-Z0-9\-_/]+)',
                r'(?:git|github).*?branch.*?([a-zA-Z0-9\-_/]+)',
            ]
            
            for pattern in branch_patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    potential_branch = match.group(1).strip()
                    # Validate branch name format
                    if self._is_valid_branch_name(potential_branch):
                        branch_name = potential_branch
                        logger.info(f"Found branch from content: {branch_name}")
                        break
                if branch_name:
                    break
        
        # Strategy 4: Extract from tags
        if not branch_name and 'System.Tags' in fields and fields['System.Tags']:
            tags = fields['System.Tags'].split(';')
            for tag in tags:
                tag = tag.strip()
                if tag.startswith('branch:') or tag.startswith('feature:'):
                    branch_name = tag.split(':', 1)[1].strip()
                    logger.info(f"Found branch from tag: {branch_name}")
                    break
        
        # Clean up branch name
        if branch_name:
            branch_name = branch_name.strip('/')
            # Remove common prefixes
            for prefix in ['refs/heads/', 'origin/', 'refs/remotes/origin/']:
                if branch_name.startswith(prefix):
                    branch_name = branch_name[len(prefix):]
                    break
        
        if branch_name:
            logger.info(f"✅ Extracted branch for work item {azure_work_item.id}: {branch_name}")
        else:
            logger.debug(f"❌ No branch found for work item {azure_work_item.id}")
            
        return branch_name
    
    def _is_valid_branch_name(self, branch_name: str) -> bool:
        """Validate if a string looks like a valid git branch name"""
        if not branch_name or len(branch_name) > 250:
            return False
        
        # Basic git branch name validation
        invalid_chars = ['..', '~', '^', ':', '?', '*', '[', '\\', ' ']
        for char in invalid_chars:
            if char in branch_name:
                return False
        
        # Should not start or end with certain characters
        if branch_name.startswith('.') or branch_name.endswith('.'):
            return False
        if branch_name.startswith('/') or branch_name.endswith('/'):
            return False
        
        return True
    
    def _extract_display_name(self, user_field) -> Optional[str]:
        """Extract display name from Azure DevOps user field"""
        if not user_field:
            return None
        
        if isinstance(user_field, dict):
            return user_field.get('displayName')
        elif isinstance(user_field, str):
            # Format: "Display Name <email@domain.com>"
            match = re.match(r'^([^<]+)', user_field)
            return match.group(1).strip() if match else user_field
        
        return str(user_field)
    
    def extract_branch_name(self, relations):
        """Extract branch name from work item relations"""
        for rel in relations:
            if rel.get("rel") == "ArtifactLink" and "GitHub/Branch" in rel.get("url", ""):
                encoded_branch = rel["url"].split("%2F")[-1]
                branch_name = urllib.parse.unquote(encoded_branch)
                return branch_name
        return "main"
    
    async def extract_github_branch(self, work_item_id: int) -> Optional[str]:
        """Extract GitHub branch from work item relations"""
        try:
            # Get work item with relations
            work_item = self.wit_client.get_work_item(
                id=work_item_id,
                project=self.project,
                expand="Relations"
            )
            
            if not work_item or not hasattr(work_item, 'relations') or not work_item.relations:
                return None
            
            # Convert relations to dict format for the extract_branch_name method
            relations = []
            for relation in work_item.relations:
                relations.append({
                    "rel": relation.rel,
                    "url": relation.url
                })
            
            branch_name = self.extract_branch_name(relations)
            return branch_name if branch_name != "main" else None
            
        except Exception as e:
            logger.debug(f"Error extracting GitHub branch for work item {work_item_id}: {e}")
            return None

    def _parse_azure_date(self, date_value) -> datetime:
        """Parse Azure DevOps date field"""
        if not date_value:
            return datetime.utcnow()
        
        if isinstance(date_value, datetime):
            return date_value
        
        try:
            # Handle different date formats
            if isinstance(date_value, str):
                # ISO format with timezone
                if 'T' in date_value:
                    # Handle fractional seconds that may have varying precision
                    date_str = date_value.replace('Z', '+00:00')
                    
                    # Fix fractional seconds to 6 digits for fromisoformat compatibility
                    if '.' in date_str and '+' in date_str:
                        parts = date_str.split('.')
                        if len(parts) == 2:
                            fraction_and_tz = parts[1]
                            if '+' in fraction_and_tz:
                                fraction = fraction_and_tz.split('+')[0]
                                timezone = '+' + fraction_and_tz.split('+')[1]
                                # Pad or truncate fractional seconds to 6 digits
                                fraction = fraction.ljust(6, '0')[:6]
                                date_str = f"{parts[0]}.{fraction}{timezone}"
                    
                    return datetime.fromisoformat(date_str)
                else:
                    return datetime.strptime(date_value, '%Y-%m-%d %H:%M:%S')
            
            return datetime.utcnow()
            
        except Exception as e:
            # Silently handle date parsing errors and use current time
            logger.debug(f"Date parsing fallback for {date_value}: {e}")
            return datetime.utcnow()
    
    async def get_work_item_history(self, work_item_id: int, days_back: int = 1) -> List[Dict[str, Any]]:
        """Get work item revision history"""
        try:
            revisions = self.wit_client.get_revisions(
                id=work_item_id,
                project=self.project
            )
            
            # Filter revisions from last N days
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)
            recent_revisions = [
                rev for rev in revisions
                if self._parse_azure_date(rev.fields.get('System.ChangedDate')) > cutoff_date
            ]
            
            return recent_revisions
            
        except Exception as e:
            logger.error(f"Error getting work item history for {work_item_id}: {e}")
            return []
