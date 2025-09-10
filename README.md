# Azure DevOps Work Item Monitor

A Python-based solution that monitors Azure DevOps work items and automatically triggers GitHub Actions workflows when items are moved to specific columns. This solution uses the Model Context Protocol (MCP) framework and supports both local and cloud (GitHub Actions) execution.

## Features

✅ **Branch-Aware Workflow Triggering**: Automatically detects and uses the correct branch from work item relations  
✅ **Smart Duplicate Prevention**: Only processes NEW work items moved to the target column  
✅ **Comprehensive Branch Detection**: Extracts branch information from GitHub relations in work items  
✅ **Cloud & Local Execution**: Optimized for both GitHub Actions and local development  
✅ **Robust Error Handling**: Continues monitoring even after individual failures  
✅ **Persistent State Management**: Remembers processed items across restarts  
✅ **Configurable Polling**: Adjustable monitoring intervals  
✅ **Detailed Logging**: Comprehensive logs for monitoring and debugging  

## Deployment Options

### 🚀 **GitHub Actions (Recommended)**
Run the monitor continuously in the cloud without local infrastructure:
- **Zero maintenance**: Automatic execution every 5 minutes
- **No local resources**: Runs entirely on GitHub's infrastructure
- **Scalable**: Handle multiple repositories and work items
- **Secure**: GitHub Secrets for credential management

📖 **See [GitHub Deployment Guide](GITHUB_DEPLOYMENT_GUIDE.md) for step-by-step instructions**

### 💻 **Local Development**
Run the monitor on your local machine for development and testing:

## Architecture

```
Azure DevOps Work Items → MCP Monitor → GitHub Actions
     ↓                         ↓              ↓
  Column Changes         Branch Detection    Workflow Execution
```

## Quick Start (Local Development)

### Prerequisites

- Python 3.10+
- Azure DevOps Personal Access Token (PAT)
- GitHub Personal Access Token
- Work items with GitHub branch relations

### Installation

1. **Clone and setup the environment:**
```bash
cd Azure-devops-mcp-monitor
python -m venv venv
venv\Scripts\activate  # On Windows
pip install -r requirements.txt
```

2. **Configure your environment:**
   - Copy `.env.example` to `.env`
   - Update the configuration values (see Configuration section below)

3. **Start the monitor:**
```bash
python monitor.py
```

## Configuration

Update the `.env` file with your specific settings:

```env
# Azure DevOps Configuration
AZURE_DEVOPS_ORG=your-org-name
AZURE_DEVOPS_PROJECT=your-project-name
AZURE_DEVOPS_PAT=your-azure-devops-pat

# GitHub Configuration
GITHUB_TOKEN=your-github-token
GITHUB_REPO=owner/repository-name
GITHUB_WORKFLOW_FILE=run-tests.yml
GITHUB_DEFAULT_BRANCH=main

# Monitoring Configuration
TARGET_COLUMN=Testing
POLLING_INTERVAL=30
```

### Required Permissions

**Azure DevOps PAT needs:**
- Work Items: Read
- Code: Read (for branch information)

**GitHub Token needs:**
- Contents: Read
- Actions: Write
- Metadata: Read

## How It Works

### 1. Initial Setup
- Monitor connects to Azure DevOps and GitHub
- Caches existing work items in target column to prevent duplicate triggers
- Validates workflow file exists in GitHub repository

### 2. Monitoring Loop
- Polls Azure DevOps every 30 seconds (configurable)
- Identifies NEW work items moved to target column
- Extracts branch information from work item relations
- Triggers GitHub Actions workflow with appropriate branch

### 3. Branch Detection
The monitor uses multiple strategies to detect the correct branch:

1. **GitHub Relations** (Primary): Extracts from `vstfs:///GitHub/Branch/` URLs
2. **Custom Fields**: Checks for branch-related custom fields
3. **Description Parsing**: Looks for branch references in descriptions
4. **Tags**: Searches for branch tags

### 4. Workflow Triggering
Passes the following inputs to your GitHub workflow:
- `work_item_id`: Azure DevOps work item ID
- `work_item_title`: Work item title
- `work_item_url`: Direct link to work item
- `triggered_by`: User who moved the work item
- `branch`: Detected or default branch

## Example GitHub Workflow

Create `.github/workflows/run-tests.yml`:

```yaml
name: Run Tests for Work Item
on:
  workflow_dispatch:
    inputs:
      work_item_id:
        description: 'Azure DevOps Work Item ID'
        required: true
      work_item_title:
        description: 'Work Item Title'
        required: true
      work_item_url:
        description: 'Work Item URL'
        required: true
      triggered_by:
        description: 'User who triggered'
        required: true
      branch:
        description: 'Branch to test'
        required: true
        default: 'main'

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.inputs.branch }}
      
      - name: Setup environment
        run: |
          echo "Testing work item: ${{ github.event.inputs.work_item_title }}"
          echo "Branch: ${{ github.event.inputs.branch }}"
          echo "Work item URL: ${{ github.event.inputs.work_item_url }}"
      
      - name: Run tests
        run: |
          # Your test commands here
          echo "Running tests for branch ${{ github.event.inputs.branch }}"
```

## Monitor Status

When running, you'll see output like:

```
2025-09-09 18:02:25 - INFO - Initializing Azure DevOps MCP Monitor
2025-09-09 18:02:25 - INFO - Testing Azure DevOps connection...
2025-09-09 18:02:25 - INFO - Azure DevOps connection successful!
2025-09-09 18:02:25 - INFO - Found 7 work items in 'Testing' column
2025-09-09 18:02:25 - INFO - Cached 7 existing work items to avoid duplicate triggers
2025-09-09 18:02:25 - INFO - Testing GitHub connection...
2025-09-09 18:02:25 - INFO - GitHub connection successful!
2025-09-09 18:02:25 - INFO - Monitor initialized successfully!
2025-09-09 18:02:25 - INFO - Starting monitoring loop...
```

## Key Benefits Over Service Hooks

1. **Branch Awareness**: Unlike webhooks, automatically detects and uses work item branches
2. **Smart Filtering**: Only triggers for NEW items, not existing ones
3. **Resilient**: Continues working despite individual failures
4. **Configurable**: Easy to adjust polling intervals and target columns
5. **Stateful**: Remembers processed items across restarts

## Troubleshooting

### Common Issues

1. **"No branch detected"**
   - Ensure work items have GitHub branch relations
   - Check if branch names are properly formatted
   - Verify Azure DevOps integration with GitHub

2. **"GitHub workflow not found"**
   - Verify workflow file exists in `.github/workflows/`
   - Check GitHub token permissions
   - Ensure workflow file name matches configuration

3. **Connection failures**
   - Verify PAT tokens are valid and have required permissions
   - Check network connectivity
   - Validate organization and project names

### Logs

- Console output: Real-time monitoring status
- `monitor.log`: Detailed logging for troubleshooting
- `processed_items.json`: Cache of processed work items

## Development

### Project Structure
```
├── src/
│   ├── azure_devops_client.py    # Azure DevOps API integration
│   ├── github_client.py          # GitHub API integration
│   ├── config.py                 # Configuration management
│   └── models.py                 # Data models
├── monitor.py                    # Main monitor script
├── requirements.txt              # Dependencies
└── .env                         # Configuration (create from .env.example)
```

### Testing
- `test_monitor_enhanced.py`: Connection testing and configuration validation
- Use debug logging to troubleshoot branch detection issues

## Support

For issues or questions:
1. Check logs for error details
2. Verify configuration settings
3. Test Azure DevOps and GitHub connections independently
4. Ensure work items have proper branch relations

---

**Note**: This solution provides a robust alternative to Azure DevOps Service Hooks with enhanced branch awareness and duplicate prevention capabilities.
