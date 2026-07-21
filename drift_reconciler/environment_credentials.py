"""
Build a boto3 Session for an environment, resolving credentials from
the new auth_type/aws_role_arn/environment_secrets columns, or falling
back to the legacy aws_profile column for backward compatibility.

Service-role only — the environment_secrets table has zero anon RLS
policies, same pattern as notification_secrets.
"""

import os
import subprocess
from typing import Any

import boto3
import botocore.exceptions
import requests

try:
    from .env_loader import load_env
except ImportError:
    from env_loader import load_env
load_env()

_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_HEADERS = {
    "apikey": _KEY,
    "Authorization": f"Bearer {_KEY}",
}


def _fetch_environment_secrets(environment_id: str) -> dict[str, Any]:
    """Read the ``environment_secrets`` row for *environment_id* via
    service-role GET.  Returns an empty dict if the table is unreachable
    or the row doesn't exist."""
    if not _URL or not _KEY:
        return {}
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/environment_secrets"
            f"?select=aws_access_key_id,aws_secret_access_key,github_token"
            f"&environment_id=eq.{environment_id}",
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code == 200:
            rows = resp.json() if resp.text else []
            return rows[0] if rows else {}
        return {}
    except requests.RequestException:
        return {}


def get_aws_session(environment: dict) -> boto3.Session:
    """Return a boto3 Session for *environment*.

    *environment* must be a dict with the shape of a row from the
    ``environments`` table (all columns returned by a ``select *``
    query, as cached by ``serve.py._get_active_environments``).

    Credential resolution, in order:

    1. ``auth_type == 'role'``  → STS AssumeRole with the stored ARN.
    2. ``auth_type == 'keys'``  → static access key from environment_secrets.
    3. No auth_type (legacy)    → boto3 profile from ``aws_profile`` column.
    """
    slug = environment.get("slug", "unknown")
    region = environment.get("region", "us-east-1")

    auth_type = (environment.get("auth_type") or "").strip()

    if auth_type == "role":
        role_arn = (environment.get("aws_role_arn") or "").strip()
        if not role_arn:
            raise RuntimeError(
                f"Environment '{slug}' has auth_type='role' but aws_role_arn is empty."
            )
        try:
            sts_client = boto3.client("sts", region_name=region)
            assume_kwargs: dict[str, Any] = {
                "RoleArn": role_arn,
                "RoleSessionName": f"drift-reconciler-{slug}",
            }
            external_id = (environment.get("aws_external_id") or "").strip()
            if external_id:
                assume_kwargs["ExternalId"] = external_id
            assumed = sts_client.assume_role(**assume_kwargs)
            creds = assumed["Credentials"]
            return boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
                region_name=region,
            )
        except (botocore.exceptions.ClientError,
                botocore.exceptions.BotoCoreError,
                KeyError) as exc:
            raise RuntimeError(
                f"Failed to assume IAM role for environment '{slug}' "
                f"(auth_type=role, role_arn={role_arn}): {exc}"
            ) from exc

    if auth_type == "keys":
        env_id = environment.get("id")
        if not env_id:
            raise RuntimeError(
                f"Environment '{slug}' has auth_type='keys' but no id — "
                f"cannot look up environment_secrets."
            )
        secrets = _fetch_environment_secrets(env_id)
        access_key = (secrets.get("aws_access_key_id") or "").strip()
        secret_key = (secrets.get("aws_secret_access_key") or "").strip()
        if not access_key or not secret_key:
            raise RuntimeError(
                f"Environment '{slug}' has auth_type='keys' but "
                f"environment_secrets.aws_access_key_id or "
                f"aws_secret_access_key is missing."
            )
        return boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    # -- Transitional fallback: environments created before the auth_type
    #    migration (scope-a, scope-b, scope-c, etc.) rely on the legacy
    #    aws_profile column.  Remove this branch once all environments
    #    have been migrated to an explicit auth_type.
    profile = (environment.get("aws_profile") or "").strip()
    if profile:
        try:
            return boto3.Session(profile_name=profile, region_name=region)
        except botocore.exceptions.ProfileNotFound as exc:
            raise RuntimeError(
                f"AWS named profile '{profile}' not found for environment "
                f"'{slug}'.  Create it in ~/.aws/config, or update this "
                f"environment's auth_type to 'role' or 'keys'."
            ) from exc

    raise RuntimeError(
        f"Environment '{slug}' has no auth_type, no aws_profile, and no "
        f"credential configuration — cannot build an AWS session."
    )


# ── Git clone / tf_dir resolution ────────────────────────────────────

_TOKEN_RE = None  # compiled lazily when first needed


def _scrub_token(text: str) -> str:
    """Return *text* with any ``https://...@github.com`` token redacted."""
    global _TOKEN_RE
    if _TOKEN_RE is None:
        _TOKEN_RE = __import__("re").compile(r"https://[^@]+@")
    return _TOKEN_RE.sub("https://<redacted>@", text)


def resolve_tf_dir(environment: dict) -> str:
    """Return the absolute local path to this environment's Terraform
    directory.  If the environment has a ``repo_url``, the repo is cloned
    (or refreshed) under ``DRIFT_CLONE_BASE`` first.  Otherwise the
    legacy ``tf_directory_path`` is returned unchanged.

    Raises ``RuntimeError`` (never a raw subprocess error) so failures
    flow through the existing ``humanize_terraform_error`` pipeline.
    """
    slug = environment.get("slug", "unknown")

    # -- Transitional fallback: environments created before the git-source
    #    migration (scope-a/b/c) where repo_url is NULL.  Return the
    #    existing tf_directory_path as-is — same as current behavior.
    repo_url = (environment.get("repo_url") or "").strip()
    if not repo_url:
        return (environment.get("tf_directory_path") or "").strip()

    branch = (environment.get("repo_branch") or "main").strip() or "main"
    base = os.environ.get(
        "DRIFT_CLONE_BASE",
        os.path.join(os.path.expanduser("~"), ".drift-clones"),
    )
    clone_path = os.path.join(base, slug)

    os.makedirs(base, exist_ok=True)

    git_dir = os.path.join(clone_path, ".git")
    needs_clone = not os.path.isdir(git_dir)

    # Build the clone URL — inject token if auth_type == 'token'.
    url = repo_url
    git_auth = (environment.get("git_auth_type") or "").strip()
    if git_auth == "token":
        env_id = environment.get("id")
        if not env_id:
            raise RuntimeError(
                f"Environment '{slug}' has git_auth_type='token' but no id."
            )
        secrets = _fetch_environment_secrets(env_id)
        token = (secrets.get("github_token") or "").strip()
        if not token:
            raise RuntimeError(
                f"Environment '{slug}' has git_auth_type='token' but "
                f"environment_secrets.github_token is missing."
            )
        # Insert token into the URL — never logged or printed.
        url = repo_url.replace("https://", f"https://{token}@", 1)

    if needs_clone:
        try:
            result = subprocess.run(
                ["git", "clone", "--branch", branch, url, clone_path],
                capture_output=True,
                encoding="utf-8",
                timeout=300,  # 5 min — repos are small, but first clone may be slow
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"git clone failed for environment '{slug}': "
                    f"{_scrub_token(result.stderr)[:300]}"
                )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"git clone timed out for environment '{slug}' (5 min)"
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git clone failed for environment '{slug}': "
                f"{_scrub_token(str(exc))[:300]}"
            ) from exc
    else:
        # Refresh existing clone — fetch + hard reset, no merge noise.
        try:
            subprocess.run(
                ["git", "fetch", "origin", branch],
                cwd=clone_path,
                capture_output=True,
                encoding="utf-8",
                timeout=120,
                check=True,
            )
            subprocess.run(
                ["git", "reset", "--hard", f"origin/{branch}"],
                cwd=clone_path,
                capture_output=True,
                encoding="utf-8",
                timeout=60,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git refresh failed for environment '{slug}': "
                f"{_scrub_token(str(exc))[:300]}"
            ) from exc
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"git refresh timed out for environment '{slug}'"
            )

    # Return clone_path + tf_directory_path subpath (or clone_path alone).
    sub = (environment.get("tf_directory_path") or "").strip().lstrip("/")
    if sub:
        return os.path.join(clone_path, sub)
    return clone_path
