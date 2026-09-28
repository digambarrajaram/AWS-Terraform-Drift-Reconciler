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

_TABLE = "notification_secrets"


def _supabase_url() -> str:
    load_env()
    return os.environ.get("SUPABASE_URL", "").strip().rstrip("/")


def _supabase_key() -> str:
    load_env()
    return os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()


def _service_headers() -> dict[str, str]:
    key = _supabase_key()
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _with_env_fallbacks(row: dict[str, Any]) -> dict[str, str | None]:
    """Merge Supabase row with legacy env vars (documented in BACKEND_REFERENCE)."""
    load_env()
    pd = (row.get("pagerduty_routing_key") or "").strip() or None
    slack = (row.get("slack_webhook_url") or "").strip() or None
    if not pd:
        pd = os.environ.get("PAGERDUTY_ROUTING_KEY", "").strip() or None
    if not slack:
        slack = os.environ.get("SLACK_WEBHOOK_URL", "").strip() or None
    return {"pagerduty_routing_key": pd, "slack_webhook_url": slack}


def get_notification_secrets(strict: bool = False) -> dict[str, str | None]:
    """Return ``{pagerduty_routing_key, slack_webhook_url}`` from the
    singleton row, or ``{}`` on failure."""
    url = _supabase_url()
    key = _supabase_key()
    if not url or not key:
        if strict:
            raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        return _with_env_fallbacks({})
    try:
        resp = requests.get(
            f"{url}/rest/v1/{_TABLE}?select=pagerduty_routing_key,slack_webhook_url&id=eq.1",
            headers=_service_headers(),
            timeout=10,
        )
        if resp.status_code == 200:
            rows = resp.json() if resp.text else []
            if rows:
                return _with_env_fallbacks(rows[0])
        if strict:
            raise RuntimeError(f"notification settings query failed ({resp.status_code})")
        return _with_env_fallbacks({})
    except requests.RequestException:
        if strict:
            raise
        return _with_env_fallbacks({})


def update_notification_secret(field: str, value: str | None) -> bool:
    """Set *field* (``"pagerduty_routing_key"`` or ``"slack_webhook_url"``)
    on the singleton row.  Returns True on success."""
    if field not in ("pagerduty_routing_key", "slack_webhook_url"):
        print(f"  [notif-config] Invalid field: {field}")
        return False
    url = _supabase_url()
    if not url or not _supabase_key():
        return False
    payload = {
        "id": 1,
        field: value,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.post(
            f"{url}/rest/v1/{_TABLE}",
            headers={
                **_service_headers(),
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            json=payload,
            timeout=10,
        )
        if resp.status_code in (200, 201, 204):
            return True
        print(
            f"  [notif-config] Update failed ({resp.status_code}): {resp.text}"
        )
        return False
    except requests.RequestException as exc:
        print(f"  [notif-config] Update failed: {exc}")
        return False
