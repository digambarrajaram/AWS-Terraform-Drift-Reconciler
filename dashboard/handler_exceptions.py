"""HTTP handler mixin: exception registry CRUD."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, date
from urllib.parse import parse_qs, urlparse

import requests

from dashboard.exceptions_policy import _validate_exception_entry_local


def _serve_requests():
    from dashboard import serve
    return serve.requests


class ExceptionsMixin:
    def _serve_api_exceptions(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        scope_raw = params.get("scope", [None])[0]
        if not self._require_owned_scope(scope_raw or ""):
            return

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}"}
        base = f"{url}/rest/v1/drift_exception_registry"

        def _fetch(exception_type):
            resp = requests.get(
                f"{base}?select=*&scope=eq.{scope_raw}&exception_type=eq.{exception_type}&active=eq.true&order=created_at.desc",
                headers=headers, timeout=10,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Supabase query failed ({resp.status_code})")
            return resp.json() if resp.text else []

        try:
            payload = {
                "drift_exceptions": _fetch("drift"),
                "unmanaged_exceptions": _fetch("unmanaged"),
                "security_exceptions": _fetch("security"),
            }
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            self._json_error(502, f"Failed to load exceptions: {exc}")
            return
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_api_exceptions_post(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        scope = body.get("scope", "")
        if not self._require_owned_scope(scope):
            return

        exception_type = body.get("exception_type", "")
        if exception_type not in ("drift", "unmanaged", "security"):
            self._json_error(400, "exception_type must be 'drift', 'unmanaged', or 'security'.")
            return

        action = body.get("action", "")
        if action not in ("add", "expire", "delete"):
            self._json_error(400, "action must be 'add', 'expire', or 'delete'.")
            return

        entry = body.get("entry")
        if not isinstance(entry, dict):
            self._json_error(400, "entry must be a JSON object.")
            return

        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        table_url = f"{url}/rest/v1/drift_exception_registry"

        if action == "add":
            ok, err = _validate_exception_entry_local(exception_type, entry)
            if not ok:
                self._json_error(400, err)
                return

            row = {"scope": scope, "exception_type": exception_type, "reason": entry.get("reason", "").strip()}
            if exception_type == "drift":
                row["resource_address"] = (entry.get("resource_address") or "").strip()
                row["drift_type"] = (entry.get("drift_type") or "*").strip()
                row["auto"] = bool(entry.get("auto"))
                expires = (entry.get("expires") or "").strip()
                if expires:
                    row["expires"] = expires
            elif exception_type == "security":
                row["resource_address"] = (entry.get("resource_address") or "").strip()
                row["rule_id"] = (entry.get("rule_id") or "").strip()
                row["auto"] = bool(entry.get("auto"))
                expires = (entry.get("expires") or "").strip()
                if expires:
                    row["expires"] = expires
            else:
                row["resource_type"] = (entry.get("resource_type") or "").strip()
                row["resource_id_pattern"] = (entry.get("resource_id_pattern") or "").strip()
                cost = entry.get("max_monthly_cost_usd")
                if cost is not None and cost != "":
                    try:
                        parsed_cost = float(cost)
                    except (TypeError, ValueError):
                        self._json_error(400, "max_monthly_cost_usd must be a number")
                        return
                    if parsed_cost < 0 or not math.isfinite(parsed_cost):
                        self._json_error(400, "max_monthly_cost_usd must be a finite non-negative number")
                        return
                    row["max_monthly_cost_usd"] = parsed_cost
            if entry.get("approved_by"):
                # Normalize to lowercase — prevents "Digambar",
                # "digambar", and "Digambar R" from becoming 3
                # separate entries in the approved_by column.
                row["approved_by"] = entry["approved_by"].strip().lower()

            try:
                resp = requests.post(table_url, headers=headers, json=row, timeout=10)
                if resp.status_code in (200, 201):
                    created = resp.json()
                    row_id = created[0]["id"] if isinstance(created, list) else created["id"]
                    data = json.dumps({"id": row_id}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json_error(502, f"Supabase insert failed ({resp.status_code}): {resp.text[:200]}")
            except requests.RequestException as e:
                self._json_error(502, f"Supabase unreachable: {e}")

        elif action == "expire":
            expires = (entry.get("expires") or "").strip()
            try:
                if not expires:
                    raise ValueError("expiry date is required")
                if datetime.strptime(expires, "%Y-%m-%d").date() <= date.today():
                    raise ValueError("expiry date must be in the future")
            except ValueError as exc:
                self._json_error(400, str(exc))
                return
            self._do_exception_update(scope, exception_type, entry, headers, table_url, {"expires": expires})

        elif action == "delete":
            self._do_exception_update(scope, exception_type, entry, headers, table_url, {"active": False})

    def _do_exception_update(self, scope, exception_type, entry, headers, table_url, updates):
        requests = _serve_requests()
        """Soft-update (expire / deactivate) one exception row.

        Prefer ``entry.id`` when the dashboard sends it (all three tabs do).
        Fall back to the composite natural key for older clients / scripts.
        """
        row_id = entry.get("id")
        if row_id is not None and str(row_id).strip():
            filter_parts = [
                f"id=eq.{row_id}",
                f"scope=eq.{scope}",
                f"exception_type=eq.{exception_type}",
                "active=eq.true",
            ]
        else:
            filter_parts = [f"scope=eq.{scope}", f"exception_type=eq.{exception_type}", "active=eq.true"]
            if exception_type == "drift":
                addr = (entry.get("resource_address") or "").strip()
                if not addr:
                    self._json_error(400, "resource_address is required.")
                    return
                filter_parts.append(f"resource_address=eq.{addr}")
            elif exception_type == "security":
                addr = (entry.get("resource_address") or "").strip()
                rule_id = (entry.get("rule_id") or "").strip()
                if not addr or not rule_id:
                    self._json_error(400, "resource_address and rule_id are required.")
                    return
                filter_parts.append(f"resource_address=eq.{addr}")
                filter_parts.append(f"rule_id=eq.{rule_id}")
            else:
                rt = (entry.get("resource_type") or "").strip()
                pat = (entry.get("resource_id_pattern") or "").strip()
                if not rt or not pat:
                    self._json_error(400, "resource_type and resource_id_pattern are required.")
                    return
                filter_parts.append(f"resource_type=eq.{rt}")
                filter_parts.append(f"resource_id_pattern=eq.{pat}")

        filter_str = "&".join(filter_parts)
        try:
            resp = requests.patch(f"{table_url}?{filter_str}", headers=headers, json=updates, timeout=10)
            if resp.status_code in (200, 204):
                data = json.dumps({"status": "ok"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json_error(404, "No matching active exception entry found.")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

