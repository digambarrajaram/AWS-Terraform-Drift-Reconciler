"""HTTP handler mixin: GitHub settings + webhook."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys

import requests

from dashboard.exceptions_policy import auto_add_exceptions_on_merge
from drift_reconciler.utils import mask_secret as _mask

class GitHubMixin:
    def _serve_github_settings(self):
        try:
            from drift_reconciler.github_settings import get_masked_github_token
            result = get_masked_github_token()
        except Exception:
            result = {"github_configured": False, "github_masked": None}

        data = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_github_settings_post(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        # Only one field exists on this singleton, so we accept
        # github_token directly rather than using the {field, value}
        # indirection the notification-settings endpoint needs (which
        # multiplexes pagerduty_routing_key / slack_webhook_url).
        token = (body.get("github_token") or "").strip()
        if not token:
            self._json_error(400, "github_token is required and must be non-empty.")
            return

        try:
            from drift_reconciler.github_settings import update_github_token
            ok = update_github_token(token)
        except Exception as e:
            self._json_error(502, f"Failed to update: {e}")
            return

        if not ok:
            self._json_error(
                502,
                "Failed to update — Supabase may be unreachable or the "
                "app_settings table has not been seeded yet (run "
                "migrations/create_app_settings_table.sql in the SQL Editor)."
            )
            return

        payload = {"success": True, "github_configured": True}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_github_webhook(self):
        """POST /api/webhooks/github

        Receives pull_request events from GitHub.  A PR that was merged
        (action=closed, merged=true) and has "Drift fix" in the title is
        resolved to an environment by matching the webhook's repository
        full_name against each environment's repo_url.  A matching
        environment's pending_applies row is inserted for dashboard-side
        approval.  Everything else is a silent 204 no-op.

        Authenticated by X-Hub-Signature-256 (HMAC-SHA256 over the raw
        body) using the environment's configured webhook_secret.

        If no environment matches the repo, or signature verification fails,
        returns 401 Unauthorized (same response for both, to avoid leaking
        which repos are configured).

        NOTE: this is a FALLBACK path.  The primary flow is the dashboard
        Approve button calling merge_pr() directly (which spawns apply and
        resolves drift_events itself).  This webhook only matters for
        out-of-band merges done manually on GitHub; in the common dashboard
        case the dedup-guarded pending_applies insert below is a no-op
        because the approve flow already transitioned the row.
        """
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""

        # ── Parse payload (unverified — only reads repo full_name) ─────
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid JSON body")
            return

        # Extract the unverified repo full_name (e.g. "owner/repo")
        repo_full_name = (payload.get("repository") or {}).get("full_name", "").strip()
        if not repo_full_name:
            self._json_error(401, "Unauthorized")  # No repo info = invalid webhook
            return

        # ── Resolve environment by repo_url match ──────────────────────
        # Fetch all active environments with repo_url set.
        url_base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url_base or not key:
            self._json_error(401, "Unauthorized")  # Supabase unavailable
            return

        headers_auth = {"apikey": key, "Authorization": f"Bearer {key}"}
        resolved_env = None
        try:
            resp = requests.get(
                f"{url_base}/rest/v1/environments"
                f"?select=id,slug,repo_url,user_id&is_active=eq.true&repo_url=not.is.null",
                headers=headers_auth,
                timeout=10,
            )
            if resp.status_code == 200:
                envs = resp.json() if resp.text else []
                from drift_reconciler.github_client_utils import _parse_repo_url
                repo_matches = []
                for env in envs:
                    parsed = _parse_repo_url(env.get("repo_url", ""))
                    if parsed and parsed.lower() == repo_full_name.lower():
                        repo_matches.append(env)
                # Disambiguate per-user environments sharing a repo URL by
                # verifying the webhook HMAC against each candidate's secret.
                sig_header = self.headers.get("X-Hub-Signature-256", "")
                if sig_header.startswith("sha256="):
                    received = sig_header[len("sha256="):].strip()
                    for env in repo_matches:
                        env_id = env.get("id")
                        if not env_id:
                            continue
                        try:
                            sec_resp = requests.get(
                                f"{url_base}/rest/v1/environment_secrets"
                                f"?select=webhook_secret&environment_id=eq.{env_id}",
                                headers=headers_auth,
                                timeout=10,
                            )
                            if sec_resp.status_code != 200:
                                continue
                            rows = sec_resp.json() if sec_resp.text else []
                            if not rows:
                                continue
                            webhook_secret = (rows[0].get("webhook_secret") or "").strip()
                            if not webhook_secret:
                                continue
                            expected = hmac.new(
                                webhook_secret.encode("utf-8"), raw, hashlib.sha256,
                            ).hexdigest()
                            if hmac.compare_digest(expected, received):
                                resolved_env = env
                                break
                        except requests.RequestException:
                            continue
        except requests.RequestException:
            pass

        if not resolved_env:
            # No environment found for this repo — return 401 (don't leak)
            self._json_error(401, "Unauthorized")
            return

        # ── Validate payload and extract PR details ─────────────────────
        if payload.get("action") != "closed":
            self._send_no_content()
            return

        pr = payload.get("pull_request") or {}
        title = pr.get("title", "")
        # Guard: title must identify a reconciler-created PR (drift fix,
        # batch, unmanaged, or security — created by github_integration.py).
        if not any(s in title for s in ("Drift fix", "Security fix", "Unmanaged resource")):
            self._send_no_content()
            return

        pr_number = pr.get("number")
        # Use the resolved environment's slug as the scope
        scope = resolved_env.get("slug")
        if not pr_number or not scope:
            self._send_no_content()
            return

        if not pr.get("merged"):
            # Closed without merging (dashboard reject, or an out-of-band
            # close): sync GitHub's PR state into drift_events so its rows
            # never stay open waiting for a job that won't run.  The
            # revert job spawned by a dashboard reject overwrites with
            # 'reverted' when it succeeds.
            try:
                from drift_reconciler.drift_history import sync_pr_state
                sync_pr_state(pr_number, scope)
            except Exception as exc:
                print(f"  [webhook] drift_events sync failed for PR #{pr_number}: {exc}",
                      file=sys.stderr)
            self._send_no_content()
            return

        merged_at = pr.get("merged_at")
        merge_commit_sha = pr.get("merge_commit_sha") or ""
        # Infer pr_type from the title so the queue keeps its type label on
        # out-of-band merges too (matches the drift_events vocabulary).
        if "Security fix" in title:
            pr_type = "security_only"
        elif "Unmanaged resource" in title:
            pr_type = "unmanaged"
        elif "resource(s)" in title:
            pr_type = "batch"
        else:
            pr_type = "fix"

        # ── Insert pending_applies row ──────────────────────────────────
        # Skip if a row already exists for this (user_id, pr_number, scope)
        # in ANY status: the dashboard claim flips awaiting_approval →
        # approved/rejected BEFORE the merged webhook is delivered, so
        # checking only awaiting_approval would let a redelivery insert a
        # duplicate.  A (user_id, pr_number, scope) unique index backs
        # this up for concurrent deliveries (see
        # migrations/pending_applies_user_id_pr_scope_unique.sql).
        owner_user_id = resolved_env.get("user_id")
        headers_body = {"apikey": key, "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json", "Prefer": "return=representation"}
        try:
            existing_q = (
                f"{url_base}/rest/v1/pending_applies"
                f"?select=id&pr_number=eq.{pr_number}&scope=eq.{scope}"
            )
            if owner_user_id:
                existing_q += f"&user_id=eq.{owner_user_id}"
            existing = requests.get(
                f"{existing_q}&limit=1",
                headers=headers_body, timeout=10,
            )
            if existing.status_code == 200 and existing.json():
                self._send_no_content()
                return

            resp = requests.post(
                f"{url_base}/rest/v1/pending_applies",
                headers=headers_body,
                json={
                    "pr_number": pr_number,
                    "scope": scope,
                    "status": "awaiting_approval",
                    "pr_type": pr_type,
                    "merged_at": merged_at,
                    "merge_commit_sha": merge_commit_sha or None,
                    **({"user_id": owner_user_id} if owner_user_id else {}),
                },
                timeout=10,
            )
            if resp.status_code == 409:
                # Unique (user_id, pr_number, scope) violation — a concurrent
                # delivery inserted the row first.  Same as a dedup hit.
                self._send_no_content()
                return
            if resp.status_code not in (200, 201):
                print(f"  [webhook] pending_applies insert failed ({resp.status_code}): "
                      f"{resp.text[:200]}", file=sys.stderr)
                self._json_error(502, "Failed to record pending apply")
                return
        except requests.RequestException as exc:
            print(f"  [webhook] Supabase unreachable: {exc}", file=sys.stderr)
            self._json_error(502, "Supabase unreachable")
            return

        # Out-of-band merges: unmanaged still auto-excepts.  Real-fix
        # security ("Security fix …") must NOT — the .tf patch is the fix.
        # (Dashboard review_only merges already wrote exceptions in the
        # decision handler; those PRs use "Manual review" titles and never
        # reach this webhook branch.)
        if pr_type == "security_only":
            print(f"  [webhook] skipping auto_add for real-fix security "
                  f"PR #{pr_number}", file=sys.stderr)
        else:
            auto_add_exceptions_on_merge(
                pr_number, scope, pr_type,
                approved_by=(payload.get("sender") or {}).get("login") or "webhook",
                user_id=owner_user_id,
            )

        data = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_no_content(self):
        self.send_response(204)
        self.end_headers()

    # ── Live log streaming ──────────────────────────────────────────
