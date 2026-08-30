"""HTTP handler mixin: EnvironmentsMixin."""
from __future__ import annotations

import json
import os

import requests

from drift_reconciler.utils import mask_secret as _mask

class EnvironmentsMixin:
    def _env_table(self):
        url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        return f"{url}/rest/v1/environments", headers

    def _upsert_env_secret(self, env_id, updates):
        """PATCH or POST to environment_secrets for *env_id*.
        *updates* is a dict of column→value pairs (e.g. ``{"github_token": "..."}``).

        Raises RuntimeError on ANY failed write (non-200 PATCH, failed
        INSERT, or failed value-PATCH) — a silent half-write leaves a row
        with NULL keys, which later breaks auth_type='keys' with no trace."""
        secrets_url = f"{os.environ.get('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/environment_secrets"
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation"}
        from datetime import datetime, timezone
        payload = dict(updates)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        # PATCH existing row.  With return=representation, PostgREST
        # returns [] when no rows match (HTTP 200) vs. [{...}] when a
        # row was updated (HTTP 200).  Both are HTTP 200 — the body
        # distinguishes them.
        resp = requests.patch(f"{secrets_url}?environment_id=eq.{env_id}", headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(f"environment_secrets PATCH failed ({resp.status_code}): {resp.text[:200]}")
        patched_rows = resp.json() if resp.text else None
        if not patched_rows:
            # No row yet — INSERT, then PATCH to set the values.
            post_resp = requests.post(secrets_url, headers=headers, json={"environment_id": env_id}, timeout=10)
            if post_resp.status_code not in (200, 201):
                raise RuntimeError(f"environment_secrets INSERT failed ({post_resp.status_code}): {post_resp.text[:200]}")
            patch_resp = requests.patch(f"{secrets_url}?environment_id=eq.{env_id}", headers=headers, json=payload, timeout=10)
            if patch_resp.status_code != 200:
                # Clean up the just-inserted empty row — leaving it behind
                # is exactly the NULL-keys state that breaks keys auth.
                try:
                    requests.delete(f"{secrets_url}?environment_id=eq.{env_id}", headers=headers, timeout=10)
                except requests.RequestException:
                    pass
                raise RuntimeError(
                    f"environment_secrets value PATCH after INSERT failed "
                    f"({patch_resp.status_code}): {patch_resp.text[:200]}"
                )

    def _serve_environments(self):
        table_url, headers = self._env_table()
        try:
            resp = requests.get(
                f"{table_url}?select=*&order=created_at",
                headers={k: v for k, v in headers.items() if k != "Prefer"},
                timeout=10,
            )
            if resp.status_code == 200:
                envs = resp.json() if resp.text else []

                # Fetch secrets to add masked token field.
                secrets_lookup = {}
                if envs:
                    ids = ",".join(e["id"] for e in envs)
                    s_url = f"{os.environ.get('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/environment_secrets"
                    s_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                    s_headers = {"apikey": s_key, "Authorization": f"Bearer {s_key}"}
                    try:
                        s_resp = requests.get(
                            f"{s_url}?select=environment_id,github_token,aws_access_key_id,aws_secret_access_key,webhook_secret&environment_id=in.({ids})",
                            headers=s_headers, timeout=10,
                        )
                        if s_resp.status_code == 200:
                            for row in (s_resp.json() or []):
                                secrets_lookup[row["environment_id"]] = row
                    except requests.RequestException:
                        pass

                for e in envs:
                    sec = secrets_lookup.get(e["id"], {})
                    tok = sec.get("github_token", "") if isinstance(sec, dict) else ""
                    access_key = sec.get("aws_access_key_id", "") if isinstance(sec, dict) else ""
                    secret_key = sec.get("aws_secret_access_key", "") if isinstance(sec, dict) else ""
                    webhook_sec = sec.get("webhook_secret", "") if isinstance(sec, dict) else ""
                    e["github_token_configured"] = bool(tok)
                    e["github_token_masked"] = _mask(tok)
                    e["aws_access_key_configured"] = bool(access_key)
                    e["aws_access_key_masked"] = _mask(access_key)
                    e["aws_secret_key_configured"] = bool(secret_key)
                    e["aws_secret_key_masked"] = _mask(secret_key)
                    e["webhook_secret_configured"] = bool(webhook_sec)
                    e["webhook_secret_masked"] = _mask(webhook_sec)

                data = json.dumps(envs).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json_error(502, f"Supabase query failed ({resp.status_code})")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

    def _handle_environments_post(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        slug = (body.get("slug") or "").strip()
        if not slug or not re.match(r'^[a-z0-9][a-z0-9-]*$', slug):
            self._json_error(400, "slug is required and must be URL-safe (lowercase alphanumeric and hyphens only).")
            return

        required = ["name", "aws_account_id", "region", "tf_state_bucket", "tf_directory_path"]
        row = {"slug": slug}
        for field in required:
            val = (body.get(field) or "").strip()
            if not val:
                self._json_error(400, f"{field} is required.")
                return
            row[field] = val

        # Optional fields
        for opt in ["aws_profile", "tf_lock_table", "apply_environment_name", "repo_url", "repo_branch", "git_auth_type", "auth_type", "aws_role_arn", "scan_role_arn", "aws_external_id"]:
            if body.get(opt):
                row[opt] = body[opt].strip()

        # Guard: auth_type='keys' requires keys.
        if row.get("auth_type") == "keys":
            keys_in_request = (body.get("_aws_access_key_id") or "").strip() and (body.get("_aws_secret_access_key") or "").strip()
            if not keys_in_request:
                self._json_error(400, "auth_type='keys' requires both aws_access_key_id and aws_secret_access_key.")
                return

        # Guard: auth_type is required for new environments and must be
        # 'role' or 'keys'.  Legacy values ('profile' / NULL) are only
        # permitted on UPDATE for existing environments (scope-a, scope-b).
        at = (row.get("auth_type") or "").strip()
        if at not in ("role", "keys"):
            self._json_error(400, "auth_type is required for new environments and must be 'role' or 'keys'.")
            return

        table_url, headers = self._env_table()
        try:
            resp = requests.post(table_url, headers=headers, json=row, timeout=10)
            if resp.status_code in (200, 201):
                created = resp.json()
                new_row = created[0] if isinstance(created, list) else created
                env_id = new_row.get("id")
                # Write secrets to environment_secrets if provided.
                secrets_to_write = {}
                for k in ("_github_token", "_aws_access_key_id", "_aws_secret_access_key", "_webhook_secret"):
                    val = (body.get(k) or "").strip()
                    if val:
                        secrets_to_write[k.lstrip("_")] = val
                if secrets_to_write and env_id:
                    try:
                        self._upsert_env_secret(env_id, secrets_to_write)
                    except Exception as exc:
                        import traceback
                        print(
                            f"  ✗ environment_secrets write FAILED for env_id={env_id} "
                            f"(slug={row.get('slug')}, keys={sorted(secrets_to_write)}) — "
                            f"the environment row exists but its secrets were NOT saved: {exc}",
                            file=sys.stderr,
                        )
                        traceback.print_exc()
                        self._json_error(502,
                            f"Environment created, but secret write failed for "
                            f"{', '.join(sorted(secrets_to_write))}: {exc}")
                        return
                data = json.dumps(new_row).encode("utf-8")
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif resp.status_code == 409:
                # Slug exists — try reactivating a soft-deleted row.
                reactivate = requests.patch(
                    f"{table_url}?slug=eq.{slug}&is_active=eq.false",
                    headers=headers,
                    json={"is_active": True, "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()},
                    timeout=10,
                )
                if reactivate.status_code in (200, 204):
                    self.send_response(200)
                    data = json.dumps({"slug": slug, "reactivated": True}).encode("utf-8")
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._json_error(409, f"slug '{slug}' already exists.")
            else:
                self._json_error(502, f"Supabase insert failed ({resp.status_code}): {resp.text[:200]}")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

    def _handle_environments_patch(self, env_id):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._json_error(400, "Invalid or empty JSON body")
            return

        allowed = {"name", "aws_account_id", "aws_profile", "region", "tf_state_bucket", "tf_lock_table", "tf_directory_path", "apply_environment_name", "is_active", "repo_url", "repo_branch", "git_auth_type", "auth_type", "aws_role_arn", "scan_role_arn", "aws_external_id"}
        updates = {}
        github_token_val = None
        aws_access_key_val = None
        aws_secret_key_val = None
        webhook_secret_val = None
        for k, v in body.items():
            if k == "_github_token":
                github_token_val = (str(v).strip() or None)
            elif k == "_aws_access_key_id":
                aws_access_key_val = (str(v).strip() or None)
            elif k == "_aws_secret_access_key":
                aws_secret_key_val = (str(v).strip() or None)
            elif k == "_webhook_secret":
                webhook_secret_val = (str(v).strip() or None)
            elif k in allowed:
                updates[k] = v
        if not updates and not github_token_val and not aws_access_key_val and not aws_secret_key_val and not webhook_secret_val:
            self._json_error(400, "No valid fields to update.")
            return

        # Guard: switching to auth_type='keys' requires keys (either in this
        # request or already stored).
        if updates.get("auth_type") == "keys":
            have_new_keys = aws_access_key_val and aws_secret_key_val
            if not have_new_keys:
                # Check if keys already exist in environment_secrets.
                have_existing = False
                try:
                    s_url = f"{os.environ.get('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/environment_secrets"
                    s_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                    s_resp = requests.get(
                        f"{s_url}?select=aws_access_key_id,aws_secret_access_key&environment_id=eq.{env_id}",
                        headers={"apikey": s_key, "Authorization": f"Bearer {s_key}"},
                        timeout=10,
                    )
                    if s_resp.status_code == 200 and s_resp.json():
                        row = s_resp.json()[0]
                        have_existing = bool((row.get("aws_access_key_id") or "").strip()) and bool((row.get("aws_secret_access_key") or "").strip())
                except Exception:
                    pass
                if not have_existing:
                    self._json_error(400, "auth_type='keys' requires both aws_access_key_id and aws_secret_access_key.")
                    return

        from datetime import datetime, timezone
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        table_url, headers = self._env_table()
        try:
            resp = requests.patch(f"{table_url}?id=eq.{env_id}", headers=headers, json=updates, timeout=10)
            if resp.status_code in (200, 204):
                secrets_to_write = {}
                for k, var in [("github_token", github_token_val), ("aws_access_key_id", aws_access_key_val), ("aws_secret_access_key", aws_secret_key_val), ("webhook_secret", webhook_secret_val)]:
                    if var:
                        secrets_to_write[k] = var
                if secrets_to_write:
                    try:
                        self._upsert_env_secret(env_id, secrets_to_write)
                    except Exception as exc:
                        import traceback
                        print(
                            f"  ✗ environment_secrets write FAILED for env_id={env_id} "
                            f"(keys={sorted(secrets_to_write)}) — environment updated, "
                            f"secrets NOT saved: {exc}",
                            file=sys.stderr,
                        )
                        traceback.print_exc()
                        self._json_error(502,
                            f"Environment updated, but secret write failed for "
                            f"{', '.join(sorted(secrets_to_write))}: {exc}")
                        return
                if resp.status_code == 200 and resp.text:
                    data = json.dumps(resp.json()).encode("utf-8")
                else:
                    data = json.dumps({"status": "ok"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json_error(404, "Environment not found.")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

    def _handle_environments_delete(self, env_id):
        table_url, headers = self._env_table()
        from datetime import datetime, timezone
        try:
            resp = requests.patch(
                f"{table_url}?id=eq.{env_id}",
                headers=headers,
                json={"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()},
                timeout=10,
            )
            if resp.status_code in (200, 204):
                data = json.dumps({"status": "ok"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json_error(404, "Environment not found.")
        except requests.RequestException as e:
            self._json_error(502, f"Supabase unreachable: {e}")

