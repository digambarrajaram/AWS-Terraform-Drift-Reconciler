"""
Read / update the singleton notification-secrets row in Supabase.

Service-role only — the anon key cannot read or write this table.
"""

import os
from datetime import datetime, timezone
from typing import Any

import requests

try:
    from .env_loader import load_env
except ImportError:
    from env_loader import load_env
load_env()

_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_TABLE = "notification_secrets"
_HEADERS = {
    "apikey": _KEY,
    "Authorization": f"Bearer {_KEY}",
}


def get_notification_secrets() -> dict[str, str | None]:
    """Return ``{pagerduty_routing_key, slack_webhook_url}`` from the
    singleton row, or ``{}`` on failure."""
    if not _URL or not _KEY:
        return {}
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/{_TABLE}?select=pagerduty_routing_key,slack_webhook_url&id=eq.1",
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 200:
            rows = resp.json() if resp.text else []
            if rows:
                return {
                    "pagerduty_routing_key": rows[0].get("pagerduty_routing_key"),
                    "slack_webhook_url": rows[0].get("slack_webhook_url"),
                }
        return {}
    except requests.RequestException:
        return {}


def update_notification_secret(field: str, value: str | None) -> bool:
    """Set *field* (``"pagerduty_routing_key"`` or ``"slack_webhook_url"``)
    on the singleton row.  Returns True on success."""
    if field not in ("pagerduty_routing_key", "slack_webhook_url"):
        print(f"  [notif-config] Invalid field: {field}")
        return False
    if not _URL or not _KEY:
        return False
    payload = {
        field: value,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.patch(
            f"{_URL}/rest/v1/{_TABLE}?id=eq.1",
            headers={**_HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"},
            json=payload,
            timeout=10,
        )
        return resp.status_code in (200, 204)
    except requests.RequestException as exc:
        print(f"  [notif-config] Update failed: {exc}")
        return False
