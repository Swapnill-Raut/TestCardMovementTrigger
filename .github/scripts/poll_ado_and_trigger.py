import os
import requests
from datetime import datetime, timedelta

ADO_ORG_URL = os.getenv("ADO_ORG_URL")
ADO_PROJECT = os.getenv("ADO_PROJECT")
ADO_PAT = os.getenv("ADO_PAT")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

def get_testing_cards():
    time_window = (datetime.utcnow() - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    wiql = {
        "query": f"SELECT [System.Id] FROM WorkItems WHERE [System.BoardColumn] = 'Testing' AND [System.ChangedDate] >= '{time_window}'"
    }
    url = f"{ADO_ORG_URL}{ADO_PROJECT}/_apis/wit/wiql?api-version=7.0"
    print("WIQL URL:", url)
    print("WIQL Query:", wiql)
    response = requests.post(url, json=wiql, auth=("", ADO_PAT))
    print("Response:", response.text)
    response.raise_for_status()
    work_items = response.json().get("workItems", [])
    return [item["id"] for item in work_items]

def trigger_github_workflow(work_item_id):
    repo = "<your-github-username>/<your-repo-name>"
    workflow = "github-actions-demo.yml"
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    payload = {
        "ref": "main",
        "inputs": {
            "work_item_id": str(work_item_id)
        }
    }
    response = requests.post(url, json=payload, headers=headers)
    print(f"Triggered workflow for work item {work_item_id}: {response.status_code}")

def main():
    testing_cards = get_testing_cards()
    for work_item_id in testing_cards:
        trigger_github_workflow(work_item_id)

if __name__ == "__main__":
    main()
