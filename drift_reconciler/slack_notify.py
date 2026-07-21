"""
Post drift findings to a Slack channel via incoming webhook.

Mirrors ``pagerduty_alert.py`` in shape — one environment variable, one
primary function per finding, one batch wrapper.  No new dependencies
beyond ``requests`` (already available).

Usage (standalone test):
    python drift_reconciler/slack_notify.py
"""

import os
from typing import Any

import requests

# Conservative — worst-case payload for 5 findings stays well under
# Slack's 4 000-character text field limit per block.
_MAX_FINDINGS_PER_CARD = 5


def notify_all(findings: list[dict[str, Any]], account_label: str) -> int:
    """Post all *findings* to Slack, batched into messages of at most
    ``_MAX_FINDINGS_PER_CARD`` findings each.

    Returns the number of messages successfully sent."""
    if not findings:
        return 0

    sent = 0
    for i in range(0, len(findings), _MAX_FINDINGS_PER_CARD):
        batch = findings[i : i + _MAX_FINDINGS_PER_CARD]

        fields: list[dict[str, Any]] = []
        for f in batch:
            resource_id = f.get("resource_id", "unknown")
            severity = f.get("risk_level", "LOW")
            summary = f.get("drift_summary", "")
            pr_url = f.get("pr_url", "")
            line = f"`{resource_id}` [{severity}] — {summary[:150] if summary else '(no details)'}"
            if pr_url:
                line += f"  <{pr_url}|PR>"
            fields.append({"type": "mrkdwn", "text": f"• {line}"})

        region = os.environ.get("AWS_REGION", "unknown")
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":red_circle: {len(batch)} drift finding(s) — {account_label} ({region})",
                },
            },
            {"type": "section", "fields": fields},
        ]

        webhook_url = ""
        try:
            from notification_config import get_notification_secrets
            secrets = get_notification_secrets()
            url = (secrets.get("slack_webhook_url") or "").strip()
            if url:
                webhook_url = url
        except Exception:
            pass
        if not webhook_url:
            webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
        if not webhook_url:
            print("[slack] SLACK_WEBHOOK_URL is empty — skipping batch")
            return sent

        try:
            resp = requests.post(webhook_url, json={"blocks": blocks}, timeout=10)
            if resp.status_code == 200 and resp.text.strip() == "ok":
                sent += 1
                print(f"[slack] Sent message {sent} ({len(batch)} findings)")
            else:
                print(f"[slack] Message failed — HTTP {resp.status_code}: {resp.text[:200]}")
        except requests.RequestException as exc:
            print(f"[slack] Message failed — {exc}")

    return sent


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    dummy = [
        {
            "resource_id": "aws_instance.test",
            "risk_level": "LOW",
            "drift_summary": "tags.Name: WebServer → WebServer123",
            "pr_url": "https://github.com/example/pull/1",
        },
        {
            "resource_id": "aws_security_group.test_sg",
            "risk_level": "MEDIUM",
            "drift_summary": "ingress rule added: port 22 from 0.0.0.0/0",
        },
    ]
    print("Testing Slack notification …")
    count = notify_all(dummy, "scope-a")
    print(f"Sent {count} message(s)")
