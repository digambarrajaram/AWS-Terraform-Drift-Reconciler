import os
import requests 
from dotenv import load_dotenv

# 1. Load the environment variables from the .env file
# This must be called at the very top of your script execution
load_dotenv()

def trigger_pagerduty_alert(summary: str, severity: str = "error", source: str = "Terraform Drift Engine") -> dict:
    """Triggers an incident alert in PagerDuty pulling keys automatically from a .env file."""
    
    # 2. Fetch the variable safely from the environment
    routing_key = os.environ.get("PAGERDUTY_ROUTING_KEY", "").strip()
    
    if not routing_key:
        print("[ERROR] PAGERDUTY_ROUTING_KEY is empty! Ensure it is set correctly in your .env file.")
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
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code != 202:
            print(f"\n[PagerDuty API Error] Status Code: {response.status_code}")
            print(f"[PagerDuty API Response Text]: {response.text}")
            return {}
            
        try:
            return response.json()
        except ValueError:
            print(f"[PagerDuty API Error] Non-JSON response: {response.text}")
            return {}
        
    except requests.exceptions.RequestException as e:
        print(f"\n[Network Error] Failed to connect to PagerDuty endpoint: {e}")
        return {}
