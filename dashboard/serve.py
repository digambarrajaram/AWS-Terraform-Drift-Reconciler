"""
Serve the dashboard with Supabase credentials injected from the repo
.env file.  No hardcoded keys in HTML.

Usage:
    python dashboard/serve.py [--port 8080]
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402  — tests patch dashboard.serve.requests

from dashboard.exceptions_policy import (  # noqa: E402
    auto_add_exceptions_on_merge,
)
from dashboard.handler_approvals import ApprovalsMixin  # noqa: E402
from dashboard.handler_base import HandlerBase  # noqa: E402
from dashboard.handler_config import ConfigMixin  # noqa: E402
from dashboard.handler_environments import EnvironmentsMixin  # noqa: E402
from dashboard.handler_exceptions import ExceptionsMixin  # noqa: E402
from dashboard.handler_github import GitHubMixin  # noqa: E402
from dashboard.handler_notifications import NotificationsMixin  # noqa: E402
from dashboard.handler_runs import RunsMixin  # noqa: E402


class _Handler(
    ApprovalsMixin,
    RunsMixin,
    GitHubMixin,
    NotificationsMixin,
    ExceptionsMixin,
    EnvironmentsMixin,
    ConfigMixin,
    HandlerBase,
):
    """Composed dashboard HTTP handler (mixins + base)."""


def main() -> int:
    from dashboard.startup import main as _startup_main
    return _startup_main()


if __name__ == "__main__":
    raise SystemExit(main())
