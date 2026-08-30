"""Repo and dashboard path constants."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "dashboard"

# Back-compat aliases used across the old serve.py surface
_REPO_ROOT = REPO_ROOT
_DASHBOARD_DIR = DASHBOARD_DIR
