"""HTTP handler mixin: notifications + routing rules."""
from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlparse

import requests

from drift_reconciler.utils import mask_secret as _mask

class NotificationsMixin:
    def _serve_routing_rules(self):
        scope = parse_qs(urlparse(self.path).query).get("scope", [""])[0]
        if not self._require_owned_scope(scope):
            return
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            self._json_error(502, "Supabase not configured")
            return
        try:
            resp = requests.get(
                f"{url}/rest/v1/severity_routing_rules"
                f"?select=*&or=(scope.is.null,scope.eq.{scope})",
                headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=10,
            )
            if resp.status_code != 200:
                self._json_error(502, f"Routing rules query failed ({resp.status_code})")
                return
            data = json.dumps(resp.json() or []).encode("utf-8")
        except (requests.RequestException, ValueError) as exc:
            self._json_error(502, f"Supabase unreachable: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_routing_rules_post(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        severity = body.get("severity", "").upper()
        if severity not in ("HIGH", "MEDIUM", "LOW"):
            self._json_error(400, "severity must be HIGH, MEDIUM, or LOW.")
            return

        channel = body.get("channel", "").lower()
        if channel not in ("pagerduty", "slack", "none"):
            self._json_error(400, "channel must be pagerduty, slack, or none.")
            return

        scope = body.get("scope") or None
        if scope is not None and not self._require_owned_scope(scope):
            return

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        table_url = f"{url}/rest/v1/severity_routing_rules"

        # Build match filter.
        filters = f"severity=eq.{severity}"
        if scope:
            filters += f"&scope=eq.{scope}"
        else:
            filters += "&scope=is.null"

        from datetime import datetime, timezone
        payload = {"severity": severity, "channel": channel, "scope": scope, "updated_at": datetime.now(timezone.utc).isoformat()}

        try:
            # Try PATCH existing row first.  With Prefer: return=representation,
            # Supabase returns 200 + [{...}] when a row was matched, or
            # 200 + [] when no rows matched — the body distinguishes them.
            resp = requests.patch(f"{table_url}?{filters}", headers=headers, json=payload, timeout=10)
            patched = resp.status_code == 204 or (
                resp.status_code == 200 and bool(resp.json())
            )
            if not patched:
                # No existing row — INSERT.
                resp = requests.post(table_url, headers=headers, json=payload, timeout=10)
                if resp.status_code not in (200, 201):
                    self._json_error(502, f"Supabase upsert failed ({resp.status_code}): {resp.text[:200]}")
                    return
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")
            return

        data = json.dumps({"success": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_notification_test(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        channel = body.get("channel", "")
        if channel not in ("pagerduty", "slack"):
            self._json_error(400, "channel must be 'pagerduty' or 'slack'.")
            return

        scope = body.get("scope") or None
        if scope is not None and not self._require_owned_scope(scope):
            return

        def _fail(msg):
            data = json.dumps({"success": False, "error": msg}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        if channel == "pagerduty":
            try:
                from drift_reconciler.pagerduty_alert import trigger_pagerduty_alert
                kwargs = {
                    "summary": "Test alert from Drift Reconciler dashboard — please ignore",
                    "severity": "error",
                    "source": "Terraform Drift Engine",
                }
                if scope:
                    kwargs["account_label"] = scope
                result = trigger_pagerduty_alert(**kwargs)
                if not result:
                    _fail("PagerDuty returned empty response — check routing key.")
                    return
            except Exception as e:
                _fail(f"PagerDuty send failed: {e}")
                return
        else:
            try:
                from drift_reconciler.slack_notify import notify_all
                dummy = [{
                    "resource_id": "test.dashboard",
                    "risk_level": "LOW",
                    "drift_summary": "Test alert from Drift Reconciler dashboard — please ignore",
                }]
                acct = scope or "test"
                sent = notify_all(dummy, acct)
                if sent == 0:
                    _fail("Slack returned 0 sent — check webhook URL.")
                    return
            except Exception as e:
                _fail(f"Slack send failed: {e}")
                return

        data = json.dumps({"success": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_notification_settings_post(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        field = body.get("field", "")
        if field not in ("pagerduty_routing_key", "slack_webhook_url"):
            self._json_error(400, "field must be pagerduty_routing_key or slack_webhook_url.")
            return

        value = body.get("value")
        if not value or not str(value).strip():
            self._json_error(400, "value is required and must be non-empty.")
            return

        try:
            from drift_reconciler.notification_config import update_notification_secret
            ok = update_notification_secret(field, str(value).strip())
        except Exception as e:
            self._json_error(502, f"Failed to update: {e}")
            return

        if not ok:
            self._json_error(502, "Failed to update — Supabase may be unreachable.")
            return

        payload = {"success": True, f"{field}_configured": True}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_error(self, status, message, **extra):
        payload = {"error": message}
        payload.update(extra)
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_notification_settings(self):
        try:
            from drift_reconciler.notification_config import get_notification_secrets
            secrets = get_notification_secrets(strict=True)
        except Exception:
            secrets = {}

        pd_key = secrets.get("pagerduty_routing_key")
        slack_url = secrets.get("slack_webhook_url")

        payload = {
            "pagerduty_configured": bool(pd_key),
            "pagerduty_masked": _mask(pd_key),
            "slack_configured": bool(slack_url),
            "slack_masked": _mask(slack_url),
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

