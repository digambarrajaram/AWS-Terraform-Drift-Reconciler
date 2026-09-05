"""Active-environment cache and AWS/tf-dir helpers."""
import os
import time as _time

import requests

from drift_reconciler.environment_credentials import resolve_tf_dir

# Per-user cache: bucket key -> list of environment row dicts
_ENV_CACHE: dict[str, list[dict]] = {}
_ENV_CACHE_TS: dict[str, float] = {}
_ALL_USERS_KEY = "__all__"


def _cache_bucket(user_id: str | None) -> str:
    return user_id if user_id else _ALL_USERS_KEY


def _get_active_environments(user_id: str | None = None) -> list[dict]:
    """Return active environments, cached for 30s per *user_id* bucket.

    When *user_id* is set, only that owner's rows are fetched/cached.
    When omitted (legacy/dev paths), all active environments are loaded but
    still keyed by ``(user_id, slug)`` — never by slug alone.
    """
    global _ENV_CACHE, _ENV_CACHE_TS
    bucket = _cache_bucket(user_id)
    now = _time.monotonic()
    cached = _ENV_CACHE.get(bucket)
    ts = _ENV_CACHE_TS.get(bucket, 0.0)
    if cached is not None and (now - ts) < 30:
        return list(cached)

    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if url and key:
        try:
            query = f"{url}/rest/v1/environments?select=*&is_active=eq.true"
            if user_id:
                query += f"&user_id=eq.{user_id}"
            resp = requests.get(
                query,
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                rows = list(resp.json())
                _ENV_CACHE[bucket] = rows
                _ENV_CACHE_TS[bucket] = now
                return list(rows)
        except requests.RequestException:
            if cached is not None:
                return list(cached)
    return list(cached) if cached is not None else []


def _env_for_scope(scope: str, user_id: str | None = None) -> dict | None:
    """Resolve one environment row for *scope*, never crossing owners."""
    if user_id:
        return next(
            (e for e in _get_active_environments(user_id) if e.get("slug") == scope),
            None,
        )
    matches = [e for e in _get_active_environments(None) if e.get("slug") == scope]
    if len(matches) == 1:
        return matches[0]
    # Ambiguous slug across users — refuse to guess.
    return None


def _get_valid_scopes(user_id: str | None = None) -> set[str]:
    return {e["slug"] for e in _get_active_environments(user_id)}


def _get_env_field(slug: str, field: str, default: str = "", user_id: str | None = None) -> str:
    """Return *field* from the environment row for *slug*, or *default*."""
    env = _env_for_scope(slug, user_id)
    if env is None:
        return default
    return env.get(field, default) or default


def _tf_dir_for(scope: str, user_id: str | None = None) -> str:
    env = _env_for_scope(scope, user_id)
    if env is None:
        return f"terraform_code/ec2_terraform_{scope}"  # legacy fallback, unchanged
    try:
        return resolve_tf_dir(env)
    except RuntimeError as exc:
        print(f"  ⚠ resolve_tf_dir failed for scope={scope}: {exc}")
        raise


def _configure_aws_env(env: dict, scope: str) -> None:
    """Strip AWS_PROFILE from spawned agent env.

    Role-only auth: the agent resolves credentials via AssumeRole
    (``get_aws_session``). A stale named profile must not override
    temporary session credentials. Legacy ``auth_type='profile'`` /
    ``keys`` paths have been removed.
    """
    env.pop("AWS_PROFILE", None)
