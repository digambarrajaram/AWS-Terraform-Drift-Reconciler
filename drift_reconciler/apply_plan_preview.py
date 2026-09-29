"""Terraform plan preview for a drift-fix PR (pre-merge apply risk)."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from drift_reconciler.environment_credentials import (
    _resolve_env_credentials,
    refresh_clone,
)
from drift_reconciler.scope_resolution import (
    backend_config_from_environment,
    resolve_and_validate_tf_dir,
)
from drift_reconciler.github_client_utils import resolve_repo_target
from drift_reconciler.plan_analysis import plan_risk_summary
from drift_reconciler.terraform_ops import _ensure_terraform_init, _strip_hardcoded_aws_profile

_FILE_ONLY_PR_TYPES = frozenset({"unmanaged", "security_only"})


def _git_root(tf_dir: str) -> str:
    current = os.path.abspath(tf_dir)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(tf_dir)
        current = parent


def _run_plan_json(tf_dir: str, sub_env: dict, backend_config: dict) -> tuple[dict[str, Any] | None, str | None]:
    _strip_hardcoded_aws_profile(tf_dir)
    init_err = _ensure_terraform_init(tf_dir, env=sub_env, backend_config=backend_config)
    if init_err:
        return None, init_err

    plan = subprocess.run(
        ["terraform", "plan", "-no-color", "-out=tfplan", "-input=false", "-lock-timeout=30s"],
        cwd=tf_dir,
        env=sub_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if plan.returncode != 0:
        return None, (plan.stderr or plan.stdout or "terraform plan failed")[:800]

    for line in (plan.stdout or "").splitlines():
        if "forces replacement" in line or "must be replaced" in line:
            print(f"[apply-preview] {line}")

    show = subprocess.run(
        ["terraform", "show", "-no-color", "-json", "tfplan"],
        cwd=tf_dir,
        env=sub_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if show.returncode != 0:
        return None, (show.stderr or "terraform show failed")[:800]
    try:
        return json.loads(show.stdout), None
    except json.JSONDecodeError:
        return None, "terraform show -json produced unparseable output"


def preview_apply_plan(
    scope: str,
    pr_number: int,
    pr_type: str | None,
    env_dict: dict[str, Any],
) -> dict[str, Any]:
    """Plan with PR head checked out; restore clone branch before return."""
    if pr_type in _FILE_ONLY_PR_TYPES:
        return {"skipped": True, "reason": "file-only PR — no terraform apply"}

    repo_slug, token, branch = resolve_repo_target(scope)
    if not repo_slug or not token:
        return {"error": f"No GitHub client for scope '{scope}'"}

    from github import Auth, Github

    g = Github(auth=Auth.Token(token))
    pr = g.get_repo(repo_slug).get_pull(pr_number)
    head_sha = pr.head.sha

    tf_dir = resolve_and_validate_tf_dir(env_dict)
    git_root = _git_root(tf_dir)
    sub_env = _resolve_env_credentials(env_dict)
    backend_config = backend_config_from_environment(env_dict)

    saved = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    saved_sha = (saved.stdout or "").strip() if saved.returncode == 0 else ""

    preview_ref = f"refs/drift-preview/pr-{pr_number}"
    try:
        fetch = subprocess.run(
            ["git", "fetch", "origin", f"{head_sha}:{preview_ref}"],
            cwd=git_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if fetch.returncode != 0:
            return {"error": f"git fetch PR head failed: {(fetch.stderr or fetch.stdout)[:300]}"}

        co = subprocess.run(
            ["git", "checkout", "--detach", preview_ref],
            cwd=git_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        if co.returncode != 0:
            return {"error": f"git checkout PR head failed: {(co.stderr or co.stdout)[:300]}"}

        plan_json, err = _run_plan_json(tf_dir, sub_env, backend_config)
        if err:
            return {"error": err}
        summary = plan_risk_summary(plan_json)
        summary["pr_head_sha"] = head_sha
        return summary
    finally:
        if saved_sha:
            subprocess.run(
                ["git", "checkout", "--detach", saved_sha],
                cwd=git_root,
                capture_output=True,
                timeout=60,
            )
        refresh_clone(git_root, branch, scope)
