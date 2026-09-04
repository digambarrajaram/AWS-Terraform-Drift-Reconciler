"""Shared GitHub client resolution.

Repo-slug parsing and per-environment credential lookup, used by both
``github_integration`` (PR creation) and ``drift_history`` (PR liveness
checks) — kept here because ``github_integration`` imports
``drift_history`` at module level, so the reverse import would be
circular.
"""

import os

import requests


def _parse_repo_url(repo_url: str) -> str:
    """Convert ``https://github.com/owner/repo.git`` to ``owner/repo``.

    Strips ``.git`` suffix, trailing slashes, and any scheme/host prefix.
    Returns the empty string when the URL isn't parseable."""
    url = (repo_url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://", "git@", "ssh://")):
        # Allow bare "owner/repo" passthrough.
        return url
    path = url.split("://")[-1] if "://" in url else url
    path = path.split("@", 1)[-1] if "@" in path else path  # drop git@ / token@
    if ":" in path and ("/" not in path or path.index(":") < path.index("/")):
        path = path.split(":", 1)[-1]  # scp style: git@host:owner/repo
    elif "/" in path:
        path = path.split("/", 1)[-1]  # https style: host/owner/repo
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path


def resolve_repo_target(account_label: str | None) -> tuple[str, str, str]:
    """Return ``(repo_slug, token, base_branch)`` for *account_label*.

    All GitHub repo and token configuration must be stored on the
    environment row / environment_secrets records. No fallback to root
    .env variables is allowed.
    """
    env_dict: dict = {}
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if account_label and url and key:
        try:
            resp = requests.get(
                f"{url}/rest/v1/environments?select=*&slug=eq.{account_label}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                env_dict = resp.json()[0]
        except requests.RequestException:
            env_dict = {}

    repo_url = (env_dict.get("repo_url") or "").strip()
    branch = (env_dict.get("repo_branch") or "").strip() or "main"
    if repo_url and env_dict.get("id"):
        from drift_reconciler.environment_credentials import _fetch_environment_secrets
        secrets = _fetch_environment_secrets(env_dict["id"])
        token = (secrets.get("github_token") or "").strip()
        slug = _parse_repo_url(repo_url)
        if token and slug:
            return slug, token, branch

    return "", "", "main"
