"""
Read / update the app-wide GitHub token in Supabase.

Service-role only — the anon key cannot read or write this table.
Follows the same singleton pattern as notification_config.py.
"""

import os
from datetime import datetime, timezone

import requests

try:
    from .env_loader import load_env
    from .utils import mask_secret as _mask
except ImportError:
    from env_loader import load_env
    from utils import mask_secret as _mask

load_env()

_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_TABLE = "app_settings"
_HEADERS = {
    "apikey": _KEY,
    "Authorization": f"Bearer {_KEY}",
}


def get_github_token() -> str | None:
    """Return the stored GitHub token, or None if unset / unreachable."""
    if not _URL or not _KEY:
        return None
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/{_TABLE}?select=github_token&id=eq.1",
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 200:
            rows = resp.json() if resp.text else []
            if rows:
                return rows[0].get("github_token") or None
        return None
    except requests.RequestException:
        return None


def update_github_token(value: str) -> bool:
    """Set the GitHub token on the singleton row.  Returns True on success.

    Uses ``return=representation`` so we can distinguish "patched 1 row"
    from "patched 0 rows" (unseeded table).  Both return HTTP 200 —
    only the response body tells them apart."""
    if not _URL or not _KEY:
        return False
    payload = {
        "github_token": value,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        resp = requests.patch(
            f"{_URL}/rest/v1/{_TABLE}?id=eq.1",
            headers={**_HEADERS, "Content-Type": "application/json",
                      "Prefer": "return=representation"},
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            rows = resp.json() if resp.text else []
            return bool(rows)  # non-empty list → 1 row matched
        return False
    except requests.RequestException as exc:
        print(f"  [github-settings] Update failed: {exc}")
        return False


def get_masked_github_token() -> dict:
    """Return ``{configured, masked}`` for the dashboard GET route.

    The raw token is never exposed — only whether it's set and the
    masked last-4 display.  Use ``get_github_token()`` internally when
    the actual token value is needed (PR creation, API calls)."""
    raw = get_github_token()
    return {
        "github_configured": bool(raw),
        "github_masked": _mask(raw),
    }
