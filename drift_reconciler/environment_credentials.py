"""
Build a boto3 Session for an environment via STS AssumeRole only.

Service-role only — the environment_secrets table has zero anon RLS
policies, same pattern as notification_secrets.
"""

import logging
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

logger = logging.getLogger(__name__)


def _backend_aws_credential_mode() -> str:
    """Return the backend AWS credential mode for the STS base identity."""
    access_key = os.environ.get("DRIFT_BACKEND_AWS_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("DRIFT_BACKEND_AWS_SECRET_ACCESS_KEY", "").strip()
    if access_key and secret_key:
        return "explicit backend keys"
    return "default credential chain (instance role or ambient)"


def _build_backend_sts_client(region_name: str):
    """Create the STS client for the backend identity.

    Use explicit static keys when provided; otherwise let boto3 resolve the
    ambient/default credential chain so EC2 instance-role deployments still work
    with zero code changes.
    """
    access_key = os.environ.get("DRIFT_BACKEND_AWS_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("DRIFT_BACKEND_AWS_SECRET_ACCESS_KEY", "").strip()
    if access_key and secret_key:
        return boto3.client(
            "sts",
            region_name=region_name,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
    return boto3.client("sts", region_name=region_name)

_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
_HEADERS = {
    "apikey": _KEY,
    "Authorization": f"Bearer {_KEY}",
}


def _resolve_env_credentials(environment: dict) -> dict:
    """Return a subprocess env dict with *environment*'s AssumeRole
    credentials injected for terraform-CLI subprocess calls.

    Always uses ``get_aws_session()`` (role-only). Temporary session
    credentials are injected as AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
    AWS_SESSION_TOKEN, and AWS_PROFILE is removed so a stale named profile
    cannot override the assumed role.

    Raises RuntimeError when AssumeRole or account verification fails.
    """
    env = os.environ.copy()
    session = get_aws_session(environment)
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError(
            f"get_aws_session returned no credentials for "
            f"environment '{environment.get('slug', 'unknown')}'."
        )
    env["AWS_ACCESS_KEY_ID"] = creds.access_key
    env["AWS_SECRET_ACCESS_KEY"] = creds.secret_key
    if creds.token:
        env["AWS_SESSION_TOKEN"] = creds.token
    env.pop("AWS_PROFILE", None)
    if environment.get("region"):
        env["AWS_REGION"] = environment["region"]
    return env


def _fetch_environment_secrets(environment_id: str) -> dict[str, Any]:
    """Read the ``environment_secrets`` row for *environment_id* via
    service-role GET.  Returns an empty dict if the table is unreachable
    or the row doesn't exist."""
    if not _URL or not _KEY:
        return {}
    try:
        resp = requests.get(
            f"{_URL}/rest/v1/environment_secrets"
            f"?select=github_token"
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
    """Return a boto3 Session for *environment* via STS AssumeRole.

    *environment* must be a dict with the shape of a row from the
    ``environments`` table (``select *``).

    After AssumeRole succeeds, calls ``sts.get_caller_identity()`` with the
    temporary credentials and verifies the Account matches
    ``environments.aws_account_id``. Mismatch raises RuntimeError and blocks
    Terraform execution.

    This is the single credential-resolution entry point — call sites must
    not reimplement AssumeRole.
    """
    slug = environment.get("slug", "unknown")
    region = environment.get("region", "us-east-1")
    expected_account = (environment.get("aws_account_id") or "").strip()
    role_arn = (environment.get("aws_role_arn") or "").strip()
    external_id = (environment.get("aws_external_id") or "").strip()

    if not role_arn:
        raise RuntimeError(
            f"Environment '{slug}' has no aws_role_arn — "
            f"AssumeRole is the only supported AWS auth method."
        )
    if not expected_account:
        raise RuntimeError(
            f"Environment '{slug}' has no aws_account_id — "
            f"cannot verify assumed-role account."
        )

    assume_kwargs: dict[str, str] = {
        "RoleArn": role_arn,
        "RoleSessionName": f"drift-reconciler-{slug}",
    }
    if external_id:
        assume_kwargs["ExternalId"] = external_id

    try:
        sts_client = _build_backend_sts_client(region)
        assumed = sts_client.assume_role(**assume_kwargs)
        creds = assumed["Credentials"]
        session = boto3.Session(
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=region,
        )
    except (botocore.exceptions.ClientError,
            botocore.exceptions.BotoCoreError,
            KeyError) as exc:
        logger.warning(
            "AssumeRole failed for environment '%s' (role_arn=%s): %s",
            slug,
            role_arn,
            exc,
        )
        raise RuntimeError(
            f"Failed to assume IAM role for environment '{slug}'. "
            f"Check the role ARN, trust policy, and external ID (if used)."
        ) from exc

    try:
        identity = session.client("sts", region_name=region).get_caller_identity()
        actual_account = (identity.get("Account") or "").strip()
    except (botocore.exceptions.ClientError,
            botocore.exceptions.BotoCoreError) as exc:
        logger.warning(
            "get_caller_identity failed after AssumeRole for environment '%s': %s",
            slug,
            exc,
        )
        raise RuntimeError(
            f"Assumed role for environment '{slug}' but could not verify the "
            f"AWS account — refusing to run Terraform."
        ) from exc

    if actual_account != expected_account:
        logger.warning(
            "Assumed-role account mismatch for environment '%s': "
            "expected aws_account_id=%s, get_caller_identity returned %s",
            slug,
            expected_account,
            actual_account,
        )
        raise RuntimeError(
            f"Assumed-role account mismatch for environment '{slug}': "
            f"configured aws_account_id does not match the assumed role's account."
        )

    return session


# ── Git clone / tf_dir resolution ────────────────────────────────────

_TOKEN_RE = None  # compiled lazily when first needed


def _scrub_token(text: str) -> str:
    """Return *text* with any ``https://...@github.com`` token redacted."""
    global _TOKEN_RE
    if _TOKEN_RE is None:
        _TOKEN_RE = __import__("re").compile(r"https://[^@]+@")
    return _TOKEN_RE.sub("https://<redacted>@", text)


def refresh_clone(clone_path: str, branch: str, slug: str = "") -> None:
    """Fetch + hard-reset an existing clone so it matches ``origin/branch``.

    Shared by ``resolve_tf_dir`` and agent.py's scan path — a direct CLI
    run with an explicit ``--tf-dir`` skips ``resolve_tf_dir``, so the
    scan path calls this itself when tf_dir lives inside the clone base.
    No-op when the clone is already current (fetch finds nothing new,
    reset is a no-op).  Raises ``RuntimeError`` on failure."""
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

    # Build the clone URL — inject token if git_auth_type == 'token'.
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
        refresh_clone(clone_path, branch, slug)

    # Return clone_path + tf_directory_path subpath (or clone_path alone).
    sub = (environment.get("tf_directory_path") or "").strip().lstrip("/")
    if sub:
        return os.path.join(clone_path, sub)
    return clone_path
