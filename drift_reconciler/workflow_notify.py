"""
Post workflow outcome to Slack.

Usage:
    python drift_reconciler/workflow_notify.py <outcome> <scope> <pr_number> [details]

Requires SLACK_WEBHOOK_URL in the environment.
"""

import os
import sys

import requests

ICONS = {
    "accepted":  ":white_check_mark:",
    "rejected":  ":leftwards_arrow_with_hook:",
    "rollback_blocked": ":x:",
    "failed":    ":x:",
}

MESSAGES = {
    "accepted":         "Drift fix applied — code pushed to AWS",
    "rejected":         "Drift reverted — code kept, AWS restored to original",
    "rollback_blocked": "Rollback aborted — intervening change detected, merge reverted",
    "failed":           "Workflow failed — check logs for details",
}


def main() -> int:
    if len(sys.argv) < 4:
        print("Usage: python drift_reconciler/workflow_notify.py <outcome> <scope> <pr_number> [details]")
        return 2

    outcome = sys.argv[1]
    scope = sys.argv[2]
    pr_number = sys.argv[3]
    details = sys.argv[4] if len(sys.argv) >= 5 else ""

    icon = ICONS.get(outcome, ":grey_question:")
    message = MESSAGES.get(outcome, f"Unknown outcome: {outcome}")

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
        print("[workflow-notify] SLACK_WEBHOOK_URL is empty — skipping")
        return 0

    pr_link = f"<https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/pull/{pr_number}|PR #{pr_number}>"
    run_link = ""
    if os.environ.get("GITHUB_RUN_ID"):
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        run_id = os.environ["GITHUB_RUN_ID"]
        run_link = f"  <https://github.com/{repo}/actions/runs/{run_id}|View run>"

    text = f"{icon} *{message}* — {scope} {pr_link}"
    if details:
        text += f"\n```{details[:500]}```"
    if run_link:
        text += f"\n{run_link}"

    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        ]
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200 and resp.text.strip() == "ok":
            print(f"[workflow-notify] Posted: {outcome} for PR #{pr_number}")
            return 0
        print(f"[workflow-notify] HTTP {resp.status_code}: {resp.text[:200]}")
        return 1
    except requests.RequestException as exc:
        print(f"[workflow-notify] Request failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
