import os
import requests
from datetime import datetime, timedelta

ADO_ORG_URL = os.getenv("ADO_ORG_URL")
ADO_PROJECT = os.getenv("ADO_PROJECT")
ADO_PAT = os.getenv("ADO_PAT")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # Set as a secret if you want to trigger other workflows

# Find work items moved to "Testing" in the last polling interval
def get_testing_cards():
    # Adjust time window as needed (e.g., last 15 minutes)
    time_window = (datetime.utcnow() - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    wiql = {
        "query": f"SELECT [System.Id], [System.BoardColumn], [Custom.BranchName] FROM WorkItems WHERE [System.BoardColumn] = 'Testing' AND [System.ChangedDate] >= '{time_window}'"
    }
    url = f"{ADO_ORG_URL}{ADO_PROJECT}/_apis/wit/wiql?api-version=7.0"
    response = requests.post(url, json=wiql, auth=("", ADO_PAT))
    response.raise_for_status()
    work_items = response.json().get("workItems", [])
    return [item["id"] for item in work_items]

def get_work_item_details(work_item_id):
    url = f"{ADO_ORG_URL}{ADO_PROJECT}/_apis/wit/workitems/{work_item_id}?api-version=7.0"
    response = requests.get(url, auth=("", ADO_PAT))
    response.raise_for_status()
    fields = response.json().get("fields", {})
    branch_name = fields.get("Custom.BranchName", "main")  # Update field name as needed
    return branch_name

def trigger_github_workflow(work_item_id, branch_name):
    repo = "Swapnill-Raut/TestCardMovementTrigger"
    workflow = "github-actions-demo.yml"
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    headers = {
        "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}",
        "Accept": "application/vnd.github+json"
    }
    payload = {
        "ref": branch_name,
        "inputs": {
            "work_item_id": str(work_item_id)
        }
    }
    response = requests.post(url, json=payload, headers=headers)
    print(f"Triggered workflow for work item {work_item_id} on branch {branch_name}: {response.status_code}")

def main():
    testing_cards = get_testing_cards()
    for work_item_id in testing_cards:
        branch_name = get_work_item_details(work_item_id)
        trigger_github_workflow(work_item_id, branch_name)

if __name__ == "__main__":
    main()
