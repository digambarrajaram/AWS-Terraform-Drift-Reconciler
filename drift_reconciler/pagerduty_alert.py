import os
import requests

def trigger_pagerduty_alert(
    summary: str,
    severity: str = "error",
    source: str = "Terraform Drift Engine",
    dedup_key: str = None,
    account_label: str = None,
    error_detail: list[str] | None = None,
) -> dict:
    """Trigger a PagerDuty alert.

    When *account_label* is supplied the summary and dedup_key are
    automatically scoped so identical resource addresses in different
    accounts never collide (dedup) and operators can tell at a glance
    which account is affected (summary)."""
    routing_key = ""
    try:
        try:
            from .notification_config import get_notification_secrets
        except ImportError:
            from notification_config import get_notification_secrets
        secrets = get_notification_secrets()
        routing_key = (secrets.get("pagerduty_routing_key") or "").strip()
    except Exception as exc:
        print(f"[pagerduty] failed to load routing key: {exc!r}")
    if not routing_key:
        msg = "PagerDuty routing key not configured (notification_secrets or PAGERDUTY_ROUTING_KEY)"
        print(f"[ERROR] {msg}")
        if error_detail is not None:
            error_detail.append(msg)
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
            msg = f"PagerDuty API HTTP {response.status_code}: {response.text[:300]}"
            print(f"[PagerDuty API Error] {msg}")
            if error_detail is not None:
                error_detail.append(msg)
            return {}
        return response.json()
    except requests.exceptions.RequestException as e:
        msg = f"PagerDuty request failed: {e}"
        print(f"[Network Error] {e}")
        if error_detail is not None:
            error_detail.append(msg)
        return {}