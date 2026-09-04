"""HTTP handler mixin: EnvironmentsMixin."""
from __future__ import annotations

import json
import os
import re
import sys

import requests

from drift_reconciler.utils import is_valid_github_pat, mask_secret as _mask

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
        with NULL secrets, which later breaks token auth with no trace."""
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
                f"{table_url}?select=*&order=created_at" + (f"&user_id=eq.{self.auth_user_id}" if getattr(self, "auth_user_id", None) else ""),
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
                            f"{s_url}?select=environment_id,github_token,webhook_secret&environment_id=in.({ids})",
                            headers=s_headers, timeout=10,
                        )
                        if s_resp.status_code != 200:
                            self._json_error(502, f"Environment secrets query failed ({s_resp.status_code})")
                            return
                        for row in (s_resp.json() or []):
                            secrets_lookup[row["environment_id"]] = row
                    except (requests.RequestException, ValueError) as exc:
                        self._json_error(502, f"Environment secrets unavailable: {exc}")
                        return

                for e in envs:
                    sec = secrets_lookup.get(e["id"], {})
                    tok = sec.get("github_token", "") if isinstance(sec, dict) else ""
                    webhook_sec = sec.get("webhook_secret", "") if isinstance(sec, dict) else ""
                    e["github_token_configured"] = bool(tok)
                    e["github_token_masked"] = _mask(tok)
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
            raw_val = body.get(field)
            if raw_val is not None and not isinstance(raw_val, str):
                self._json_error(400, f"{field} must be a string.")
                return
            val = (raw_val or "").strip()
            if not val:
                self._json_error(400, f"{field} is required.")
                return
            row[field] = val

        if not re.fullmatch(r"\d{12}", row["aws_account_id"]):
            self._json_error(400, "aws_account_id must be a 12-digit AWS account ID.")
            return
        if not re.fullmatch(r"[a-z]{2}(?:-gov|-iso|-isob)?-[a-z]+-\d", row["region"]):
            self._json_error(400, "region must be a valid AWS region.")
            return

        # Optional fields (auth_type is hardcoded to role — not client-chosen)
        for opt in ["aws_profile", "tf_lock_table", "apply_environment_name", "repo_url", "repo_branch", "git_auth_type", "scan_role_arn"]:
            if opt in body and body[opt] is not None and not isinstance(body[opt], str):
                self._json_error(400, f"{opt} must be a string.")
                return
            if body.get(opt):
                row[opt] = body[opt].strip()

        # Role-only: aws_role_arn required; aws_external_id optional.
        role_arn = (body.get("aws_role_arn") or "").strip()
        if not isinstance(body.get("aws_role_arn", ""), (str, type(None))):
            self._json_error(400, "aws_role_arn must be a string.")
            return
        if not role_arn or not re.fullmatch(r"arn:aws[a-z-]*:iam::\d{12}:role/.+", role_arn):
            self._json_error(400, "aws_role_arn is required and must be a valid IAM role ARN.")
            return
        row["aws_role_arn"] = role_arn

        external_id = body.get("aws_external_id")
        if external_id is not None and not isinstance(external_id, str):
            self._json_error(400, "aws_external_id must be a string.")
            return
        external_id = (external_id or "").strip()
        if external_id:
            row["aws_external_id"] = external_id

        # Keep column for schema stability; always stamp role (ignore client).
        row["auth_type"] = "role"

        if row.get("scan_role_arn") and not re.fullmatch(r"arn:aws[a-z-]*:iam::\d{12}:role/.+", row["scan_role_arn"]):
            self._json_error(400, "scan_role_arn must be a valid IAM role ARN.")
            return
        if row.get("repo_url") and not re.match(r"^(https://|git@|ssh://)", row["repo_url"]):
            self._json_error(400, "repo_url must use https, git@, or ssh://.")
            return

        git_auth = (row.get("git_auth_type") or "").strip()
        repo_url = (row.get("repo_url") or "").strip()
        if repo_url and git_auth == "token":
            gh = (body.get("_github_token") or "").strip()
            if not gh:
                self._json_error(400, "github_token is required when repo_url is set and git_auth_type='token'.")
                return
            if not is_valid_github_pat(gh):
                self._json_error(400, "github_token must be a valid GitHub personal access token.")
                return

        # Ownership: always stamp from verified JWT; never trust client body.
        body.pop("user_id", None)
        if not getattr(self, "auth_user_id", None):
            self._json_error(401, "unauthorized")
            return
        row["user_id"] = self.auth_user_id

        table_url, headers = self._env_table()
        try:
            resp = requests.post(table_url, headers=headers, json=row, timeout=10)
            if resp.status_code in (200, 201):
                created = resp.json()
                new_row = created[0] if isinstance(created, list) else created
                env_id = new_row.get("id")
                # Write secrets to environment_secrets if provided.
                secrets_to_write = {}
                for k in ("_github_token", "_webhook_secret"):
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
                # Slug exists — apply the submitted configuration when
                # reactivating a soft-deleted row.
                existing = requests.get(
                    f"{table_url}?select=id&slug=eq.{slug}&is_active=eq.false" + (f"&user_id=eq.{self.auth_user_id}" if getattr(self, "auth_user_id", None) else "") + "&limit=1",
                    headers=headers, timeout=10,
                )
                if existing.status_code != 200 or not existing.json():
                    self._json_error(409, f"slug '{slug}' already exists.")
                    return
                env_id = existing.json()[0]["id"]
                reactivate = requests.patch(
                    f"{table_url}?id=eq.{env_id}&is_active=eq.false",
                    headers=headers,
                    json={**row, "is_active": True, "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()},
                    timeout=10,
                )
                if reactivate.status_code in (200, 204):
                    secrets_to_write = {
                        k.lstrip("_"): (body.get(k) or "").strip()
                        for k in ("_github_token", "_webhook_secret")
                        if isinstance(body.get(k), str) and body.get(k).strip()
                    }
                    if secrets_to_write:
                        self._upsert_env_secret(env_id, secrets_to_write)
                    self.send_response(200)
                    data = json.dumps({"slug": slug, "reactivated": True, "id": env_id}).encode("utf-8")
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

        allowed = {"name", "aws_account_id", "aws_profile", "region", "tf_state_bucket", "tf_lock_table", "tf_directory_path", "apply_environment_name", "is_active", "repo_url", "repo_branch", "git_auth_type", "aws_role_arn", "scan_role_arn", "aws_external_id"}
        updates = {}
        github_token_val = None
        webhook_secret_val = None
        clear_github_token = body.get("git_auth_type") == "none"
        for k, v in body.items():
            if k in ("_aws_access_key_id", "_aws_secret_access_key", "auth_type"):
                continue  # keys auth removed; auth_type hardcoded server-side
            if k == "_github_token":
                github_token_val = (str(v).strip() or None)
            elif k == "_webhook_secret":
                webhook_secret_val = (str(v).strip() or None)
            elif k in allowed:
                if k == "is_active":
                    if not isinstance(v, bool):
                        self._json_error(400, "is_active must be a boolean.")
                        return
                elif v is not None and not isinstance(v, str):
                    self._json_error(400, f"{k} must be a string.")
                    return
                updates[k] = v
        updates.pop("user_id", None)  # never allow client to reassign owner
        # Any non-trivial config patch re-asserts role-only auth.
        if any(k in updates for k in ("aws_role_arn", "aws_external_id", "repo_url", "git_auth_type", "name", "aws_account_id", "region", "tf_state_bucket", "tf_directory_path")):
            updates["auth_type"] = "role"
        if not updates and not github_token_val and not webhook_secret_val:
            self._json_error(400, "No valid fields to update.")
            return

        if "aws_account_id" in updates and (
            not isinstance(updates["aws_account_id"], str)
            or not re.fullmatch(r"\d{12}", updates["aws_account_id"].strip())
        ):
            self._json_error(400, "aws_account_id must be a 12-digit AWS account ID.")
            return
        if "region" in updates and (
            not isinstance(updates["region"], str)
            or not re.fullmatch(r"[a-z]{2}(?:-gov|-iso|-isob)?-[a-z]+-\d", updates["region"].strip())
        ):
            self._json_error(400, "region must be a valid AWS region.")
            return
        if "aws_role_arn" in updates:
            arn = (updates["aws_role_arn"] or "").strip() if isinstance(updates["aws_role_arn"], str) else ""
            if not arn or not re.fullmatch(r"arn:aws[a-z-]*:iam::\d{12}:role/.+", arn):
                self._json_error(400, "aws_role_arn is required and must be a valid IAM role ARN.")
                return
            updates["aws_role_arn"] = arn
        if "aws_external_id" in updates:
            ext = (updates["aws_external_id"] or "").strip() if isinstance(updates["aws_external_id"], str) else ""
            updates["aws_external_id"] = ext or None
        if "scan_role_arn" in updates and updates["scan_role_arn"] and not re.fullmatch(r"arn:aws[a-z-]*:iam::\d{12}:role/.+", updates["scan_role_arn"].strip()):
            self._json_error(400, "scan_role_arn must be a valid IAM role ARN.")
            return
        if "repo_url" in updates and updates["repo_url"] and not re.match(r"^(https://|git@|ssh://)", updates["repo_url"].strip()):
            self._json_error(400, "repo_url must use https, git@, or ssh://.")
            return

        # github_token required when resulting git auth is token + repo_url set.
        # Fetch current row when fields are only partially present in the PATCH.
        need_git_check = (
            "repo_url" in updates
            or "git_auth_type" in updates
            or github_token_val is not None
        )
        if need_git_check:
            cur_repo = updates.get("repo_url")
            cur_git = updates.get("git_auth_type")
            if cur_repo is None or cur_git is None:
                try:
                    look = requests.get(
                        f"{os.environ.get('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/environments"
                        f"?select=repo_url,git_auth_type&id=eq.{env_id}&limit=1",
                        headers={
                            "apikey": os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
                            "Authorization": f"Bearer {os.environ.get('SUPABASE_SERVICE_ROLE_KEY', '').strip()}",
                        },
                        timeout=10,
                    )
                    existing = (look.json() or [{}])[0] if look.status_code == 200 else {}
                except requests.RequestException:
                    existing = {}
                if cur_repo is None:
                    cur_repo = existing.get("repo_url")
                if cur_git is None:
                    cur_git = existing.get("git_auth_type")
            if (cur_repo or "").strip() and (cur_git or "").strip() == "token":
                if not (github_token_val or "").strip():
                    # Allow keep-existing token if one is already stored.
                    have_token = False
                    try:
                        s_url = f"{os.environ.get('SUPABASE_URL', '').strip().rstrip('/')}/rest/v1/environment_secrets"
                        s_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
                        s_resp = requests.get(
                            f"{s_url}?select=github_token&environment_id=eq.{env_id}",
                            headers={"apikey": s_key, "Authorization": f"Bearer {s_key}"},
                            timeout=10,
                        )
                        if s_resp.status_code == 200 and s_resp.json():
                            have_token = bool((s_resp.json()[0].get("github_token") or "").strip())
                    except Exception:
                        pass
                    if not have_token:
                        self._json_error(
                            400,
                            "github_token is required when repo_url is set and git_auth_type='token'.",
                        )
                        return

        if github_token_val and not is_valid_github_pat(github_token_val):
            self._json_error(400, "github_token must be a valid GitHub personal access token.")
            return

        from datetime import datetime, timezone
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        table_url, headers = self._env_table()
        try:
            owner_q = f"&user_id=eq.{self.auth_user_id}" if getattr(self, "auth_user_id", None) else ""
            resp = requests.patch(f"{table_url}?id=eq.{env_id}{owner_q}", headers=headers, json=updates, timeout=10)
            if resp.status_code in (200, 204):
                secrets_to_write = {}
                for k, var in [("github_token", github_token_val), ("webhook_secret", webhook_secret_val)]:
                    if var or (k == "github_token" and clear_github_token):
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
            owner_q = f"&user_id=eq.{self.auth_user_id}" if getattr(self, "auth_user_id", None) else ""
            resp = requests.patch(
                f"{table_url}?id=eq.{env_id}{owner_q}",
                headers=headers,
                json={"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()},
                timeout=10,
            )
            if resp.status_code in (200, 204):
                if resp.status_code == 200 and resp.text:
                    try:
                        deleted_rows = resp.json()
                    except ValueError:
                        deleted_rows = None
                    if isinstance(deleted_rows, list) and len(deleted_rows) == 0:
                        self._json_error(404, "Environment not found.")
                        return
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

