"""
Zero-dependency .env loader — reads the repo-root .env file once per
process so Supabase / Slack / PagerDuty modules find their credentials
without the python-dotenv package (which isn't installed on CI runners).
"""

import os
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_LOADED = False


def load_env() -> None:
    """Read .env into os.environ (idempotent — only runs once)."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    if not _ENV_PATH.is_file():
        return
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            if key.strip() not in os.environ:
                os.environ[key.strip()] = val.strip()
