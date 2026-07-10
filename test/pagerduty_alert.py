import os
import requests 
from dotenv import load_dotenv

# 1. Load the environment variables from the .env file
# This must be called at the very top of your script execution
load_dotenv()

def trigger_pagerduty_alert(summary: str, severity: str = "error", source: str = "Terraform Drift Engine", dedup_key: str = None) -> dict:
    routing_key = os.environ.get("PAGERDUTY_ROUTING_KEY", "").strip()
    if not routing_key:
        print("[ERROR] PAGERDUTY_ROUTING_KEY is empty!")
        return {}

    url = "https://events.pagerduty.com/v2/enqueue"
    payload = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {
            "summary": summary,
            "severity": severity,
            "source": source,
            "component": "Infrastructure Drift Monitor"
        }
    }
    if dedup_key:
        payload["dedup_key"] = dedup_key

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code != 202:
            print(f"[PagerDuty API Error] {response.status_code}: {response.text}")
            return {}
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"[Network Error] {e}")
        return {}