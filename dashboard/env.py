"""Active-environment cache and AWS/tf-dir helpers."""
import os
import time as _time

import requests

from drift_reconciler.environment_credentials import resolve_tf_dir

_ENV_CACHE: dict = {}
_ENV_CACHE_TS = 0.0


def _get_active_environments() -> list[dict]:
    """Return all active environments from Supabase, cached for 30s."""
    global _ENV_CACHE, _ENV_CACHE_TS
    now = _time.monotonic()
    if _ENV_CACHE and (now - _ENV_CACHE_TS) < 30:
        return list(_ENV_CACHE.values())  # list of row dicts

    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if url and key:
        try:
            resp = requests.get(
                f"{url}/rest/v1/environments?select=*&is_active=eq.true",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                _ENV_CACHE = {r["slug"]: r for r in resp.json()}
                _ENV_CACHE_TS = now
                return list(_ENV_CACHE.values())
        except requests.RequestException:
            if _ENV_CACHE:
                return list(_ENV_CACHE.values())  # serve stale cache
    # Fallback: serve stale cache (or empty if never populated)
    return list(_ENV_CACHE.values()) if _ENV_CACHE else []


def _get_valid_scopes() -> set[str]:
    return {e["slug"] for e in _get_active_environments()}


def _get_env_field(slug: str, field: str, default: str = "") -> str:
    """Return *field* from the environment row for *slug*, or *default*."""
    for e in _get_active_environments():
        if e["slug"] == slug:
            return e.get(field, default) or default
    return default


def _tf_dir_for(scope: str) -> str:
    env = next((e for e in _get_active_environments() if e["slug"] == scope), None)
    if env is None:
        return f"terraform_code/ec2_terraform_{scope}"  # legacy fallback, unchanged
    try:
        return resolve_tf_dir(env)
    except RuntimeError as exc:
        # Surface git-clone failures clearly instead of letting a bad path
        # silently reach the subprocess and fail later with a confusing
        # "directory not found" error.
        print(f"  ⚠ resolve_tf_dir failed for scope={scope}: {exc}")
        raise


def _aws_profile_for(scope: str) -> str:
    return _get_env_field(scope, "aws_profile") or ("account-a" if scope == "scope-a" else "account-b")


def _configure_aws_env(env: dict, scope: str) -> None:
    """Set AWS_PROFILE in *env* only when the environment's auth_type
    is 'profile' or unset (transitional fallback).  For 'role'/'keys',
    the agent resolves credentials itself — a stale profile would break
    boto3 session creation."""
    auth_type = _get_env_field(scope, "auth_type") or ""
    if not auth_type or auth_type == "profile":
        env["AWS_PROFILE"] = _aws_profile_for(scope)
