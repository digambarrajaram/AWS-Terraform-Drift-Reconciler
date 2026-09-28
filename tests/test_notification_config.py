"""notification_config: env fallbacks and runtime Supabase credentials."""
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _clear_notif_env(monkeypatch):
    for key in (
        "PAGERDUTY_ROUTING_KEY",
        "SLACK_WEBHOOK_URL",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_env_fallback_when_supabase_empty(monkeypatch):
    monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "pd-key-test")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")

    from drift_reconciler.notification_config import get_notification_secrets

    with patch("drift_reconciler.notification_config.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text="[]", json=lambda: [])
        secrets = get_notification_secrets()

    assert secrets["pagerduty_routing_key"] == "pd-key-test"
    assert secrets["slack_webhook_url"] == "https://hooks.slack.com/services/T/B/X"


def test_supabase_row_merged_with_env_fallback(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/env-fallback")

    from drift_reconciler.notification_config import get_notification_secrets

    with patch("drift_reconciler.notification_config.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            text="[{}]",
            json=lambda: [{"pagerduty_routing_key": "from-db", "slack_webhook_url": None}],
        )
        secrets = get_notification_secrets()

    assert secrets["pagerduty_routing_key"] == "from-db"
    assert secrets["slack_webhook_url"] == "https://hooks.slack.com/env-fallback"
    mock_get.assert_called_once()
    headers = mock_get.call_args.kwargs["headers"]
    assert headers["apikey"] == "service-role"
