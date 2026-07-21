import os
import requests

def trigger_pagerduty_alert(summary: str, severity: str = "error", source: str = "Terraform Drift Engine", dedup_key: str = None, account_label: str = None) -> dict:
    """Trigger a PagerDuty alert.

    When *account_label* is supplied the summary and dedup_key are
    automatically scoped so identical resource addresses in different
    accounts never collide (dedup) and operators can tell at a glance
    which account is affected (summary)."""
    routing_key = ""
    try:
        from notification_config import get_notification_secrets
        secrets = get_notification_secrets()
        key = (secrets.get("pagerduty_routing_key") or "").strip()
        if key:
            routing_key = key
    except Exception:
        pass
    if not routing_key:
        routing_key = os.environ.get("PAGERDUTY_ROUTING_KEY", "").strip()
    if not routing_key:
        print("[ERROR] PAGERDUTY_ROUTING_KEY is empty!")
        return {}

    if account_label:
        summary = f"[{account_label}] {summary}"
        if dedup_key:
            dedup_key = f"{account_label}-{dedup_key}"

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