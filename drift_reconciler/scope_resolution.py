"""Resolve scope (environment slug) → terraform directory, IAM role, and backend.

Single source of truth for environment row lookup and post-resolve validation.
Callers must not silently fall back to another scope's directory or credentials.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import requests

from drift_reconciler.ownership import owner_user_id_for_scope

logger = logging.getLogger(__name__)

# Workload tokens detected in slug or tf_directory_path (order: longer tokens first).
_WORKLOAD_TOKENS: tuple[str, ...] = ("lambda", "ec2", "vpc")

# At least one of these resource types must appear in .tf files for the workload.
_WORKLOAD_REQUIRED_RESOURCES: dict[str, tuple[str, ...]] = {
    "lambda": ("aws_lambda_function",),
    "ec2": ("aws_instance", "aws_autoscaling_group"),
    "vpc": ("aws_vpc",),
}

_RESOURCE_BLOCK_RE = re.compile(
    r'resource\s+"([^"]+)"\s+"[^"]+"\s*\{',
    re.MULTILINE,
)

# drift-reconciler-apply-EC2 / apply-LAMBDA / apply-vpc (case varies by bootstrap).
_APPLY_ROLE_WORKLOAD_SUFFIX_RE = re.compile(
    r"^(?P<prefix>drift-reconciler-apply-)(?P<token>ec2|lambda|vpc)(?P<rest>.*)$",
    re.IGNORECASE,
)


class ScopeConfigError(RuntimeError):
    """Scope wiring is missing, ambiguous, or inconsistent with terraform / IAM."""


@dataclass(frozen=True)
class ScopeBinding:
    slug: str
    tf_dir: str
    tf_directory_path: str
    role_arn: str
    scan_role_arn: str
    backend_bucket: str
    backend_lock_table: str
    backend_region: str
    repo_url: str
    clone_dir: str
    workload: str | None


def _supabase_environments_query(extra: str) -> tuple[str, dict[str, str]]:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise ScopeConfigError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to resolve scopes."
        )
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    return f"{url}/rest/v1/environments?select=*{extra}", headers


def user_id_for_scope_context(scope: str, run_id: str | None = None) -> str | None:
    """Prefer the user_id stamped on a scan/rollback run, else disambiguate by slug."""
    if run_id:
        uid = _user_id_from_run(run_id)
        if uid:
            return uid
    return owner_user_id_for_scope(scope)


def _user_id_from_run(run_id: str) -> str | None:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        return None
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    for table in ("scan_runs", "rollback_runs"):
        try:
            resp = requests.get(
                f"{url}/rest/v1/{table}?select=user_id&id=eq.{run_id}&limit=1",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200 and resp.text:
                rows = resp.json()
                if rows:
                    uid = rows[0].get("user_id")
                    if isinstance(uid, str) and uid.strip():
                        return uid.strip()
        except requests.RequestException:
            continue
    return None


def fetch_environment_row(scope: str, user_id: str | None = None) -> dict[str, Any]:
    """Return the active environments row for *scope*.

    Raises ScopeConfigError when the row is missing or ambiguous across owners.
    """
    slug = (scope or "").strip()
    if not slug:
        raise ScopeConfigError("Scope slug is required to resolve environment config.")

    uid = user_id or owner_user_id_for_scope(slug)
    if uid:
        query_url, headers = _supabase_environments_query(
            f"&slug=eq.{slug}&is_active=eq.true&user_id=eq.{uid}&limit=1"
        )
    else:
        query_url, headers = _supabase_environments_query(
            f"&slug=eq.{slug}&is_active=eq.true&limit=2"
        )

    try:
        resp = requests.get(query_url, headers=headers, timeout=10)
    except requests.RequestException as exc:
        raise ScopeConfigError(
            f"Failed to load environment for scope '{slug}': {exc}"
        ) from exc

    if resp.status_code != 200:
        raise ScopeConfigError(
            f"Failed to load environment for scope '{slug}' "
            f"(HTTP {resp.status_code})."
        )

    rows = resp.json() if resp.text else []
    if not rows:
        raise ScopeConfigError(
            f"No active environment configured for scope '{slug}'. "
            f"Add or activate a row in the environments table — do not rely on "
            f"implicit terraform_code/ec2_terraform_* paths."
        )

    if not uid and len(rows) > 1:
        owners = {
            r.get("user_id")
            for r in rows
            if isinstance(r.get("user_id"), str) and r.get("user_id")
        }
        if len(owners) != 1:
            raise ScopeConfigError(
                f"Scope '{slug}' is ambiguous across {len(rows)} environment row(s) "
                f"({len(owners)} distinct owners). Pass user_id or ensure a unique owner."
            )

    return rows[0]


def fetch_all_active_environments() -> list[dict[str, Any]]:
    """Return all active environment rows (service role)."""
    query_url, headers = _supabase_environments_query("&is_active=eq.true")
    try:
        resp = requests.get(query_url, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise ScopeConfigError(f"Failed to list active environments: {exc}") from exc
    if resp.status_code != 200:
        raise ScopeConfigError(
            f"Failed to list active environments (HTTP {resp.status_code})."
        )
    return list(resp.json() if resp.text else [])


def detect_workload(slug: str, tf_directory_path: str) -> str | None:
    """Infer a single workload token from slug + configured terraform path."""
    hay = f"{slug} {tf_directory_path or ''}".lower()
    found = [w for w in _WORKLOAD_TOKENS if w in hay]
    if len(found) > 1:
        raise ScopeConfigError(
            f"Scope '{slug}' mixes workload identifiers {found} in slug/path "
            f"({tf_directory_path!r}) — fix slug or tf_directory_path so only one "
            f"workload (lambda, ec2, vpc, …) is implied."
        )
    return found[0] if found else None


def infer_workload(
    slug: str,
    tf_directory_path: str,
    tf_dir: str | None = None,
) -> str | None:
    """Workload from slug/path, else from declared resources under *tf_dir*."""
    from_slug_path = detect_workload(slug, tf_directory_path)
    if from_slug_path:
        return from_slug_path
    if not tf_dir:
        return None
    found = scan_tf_resource_types(tf_dir)
    matches = [
        token
        for token, resources in _WORKLOAD_REQUIRED_RESOURCES.items()
        if any(r in found for r in resources)
    ]
    if len(matches) > 1:
        raise ScopeConfigError(
            f"Scope '{slug}' terraform under {tf_dir} declares multiple workloads "
            f"({matches}) — split stacks or fix tf_directory_path."
        )
    return matches[0] if matches else None


def _workload_token_for_role_name(workload: str, template_token: str) -> str:
    """Match bootstrap casing (EC2, LAMBDA, vpc, …) when rewriting role names."""
    if template_token.isupper():
        return workload.upper()
    if template_token.islower():
        return workload.lower()
    if template_token[:1].isupper() and template_token[1:].islower():
        return workload.capitalize()
    return workload.lower()


def resolve_apply_role_arn(
    role_arn: str,
    slug: str,
    workload: str | None,
) -> str:
    """Return the apply role ARN to assume for *workload*.

    When ``aws_role_arn`` uses the standard ``drift-reconciler-apply-<token>``
    pattern but *token* disagrees with the inferred workload (e.g. EC2 role
    configured for a lambda stack), rewrite the suffix to match *workload*
    so AssumeRole targets the IAM role that was bootstrapped for that stack.
    """
    arn = (role_arn or "").strip()
    if not arn:
        raise ScopeConfigError(
            f"Scope '{slug}' has no aws_role_arn — AssumeRole is required."
        )
    if not workload:
        return arn

    role_name = arn.split("/")[-1]
    match = _APPLY_ROLE_WORKLOAD_SUFFIX_RE.match(role_name)
    if not match:
        validate_role_arn_for_scope(arn, slug, workload)
        return arn

    current = match.group("token").lower()
    if current == workload:
        validate_role_arn_for_scope(arn, slug, workload)
        return arn

    token = _workload_token_for_role_name(workload, match.group("token"))
    corrected_name = (
        f"{match.group('prefix')}{token}{match.group('rest')}"
    )
    corrected = f"{arn.rsplit('/', 1)[0]}/{corrected_name}"
    logger.warning(
        "Scope '%s' workload '%s': aws_role_arn pointed at apply-%s; "
        "assuming %s instead (update environments.aws_role_arn to avoid this warning).",
        slug,
        workload,
        current,
        corrected_name,
    )
    validate_role_arn_for_scope(corrected, slug, workload)
    return corrected


def scan_tf_resource_types(tf_dir: str) -> set[str]:
    """Return Terraform resource type strings declared under *tf_dir*."""
    types: set[str] = set()
    if not os.path.isdir(tf_dir):
        return types
    for path in glob.glob(os.path.join(tf_dir, "**", "*.tf"), recursive=True):
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            types.update(_RESOURCE_BLOCK_RE.findall(text))
    return types


def validate_role_arn_for_scope(
    role_arn: str,
    slug: str,
    workload: str | None,
) -> None:
    """Ensure the apply role name matches the scope workload, not another stack."""
    arn = (role_arn or "").strip()
    if not arn:
        raise ScopeConfigError(
            f"Scope '{slug}' has no aws_role_arn — AssumeRole is required."
        )
    role_name = arn.split("/")[-1].lower()
    if workload:
        if workload not in role_name:
            raise ScopeConfigError(
                f"Scope '{slug}' implies workload '{workload}' but aws_role_arn "
                f"ends with '{role_name}' (expected '{workload}' in the role name). "
                f"Full ARN: {arn}"
            )
        for other in _WORKLOAD_TOKENS:
            if other != workload and other in role_name:
                raise ScopeConfigError(
                    f"Scope '{slug}' workload is '{workload}' but aws_role_arn "
                    f"also references '{other}' ({arn}). Use a {workload}-scoped apply role."
                )
        return
    slug_norm = slug.lower().replace("-", "")
    role_norm = role_name.replace("-", "")
    if slug_norm and slug_norm not in role_norm and slug.lower() not in role_name:
        logger.warning(
            "Scope '%s' role '%s' does not contain the slug; verify IAM wiring.",
            slug,
            role_name,
        )


def validate_tf_dir_for_scope(
    tf_dir: str,
    slug: str,
    workload: str | None,
) -> None:
    """Verify *tf_dir* exists and declares resources expected for *workload*."""
    if not os.path.isdir(tf_dir):
        raise ScopeConfigError(
            f"Terraform directory for scope '{slug}' does not exist: {tf_dir}"
        )
    tf_files = glob.glob(os.path.join(tf_dir, "**", "*.tf"), recursive=True)
    if not tf_files:
        raise ScopeConfigError(
            f"Terraform directory for scope '{slug}' has no .tf files: {tf_dir}"
        )
    if not workload:
        return
    required = _WORKLOAD_REQUIRED_RESOURCES.get(workload, ())
    if not required:
        return
    found = scan_tf_resource_types(tf_dir)
    if not any(r in found for r in required):
        raise ScopeConfigError(
            f"Scope '{slug}' workload '{workload}' requires terraform resources "
            f"{required} but directory {tf_dir} declares {sorted(found) or 'none'}. "
            f"Check tf_directory_path and repo clone subpath."
        )


def validate_environment_scope_config(
    env: dict[str, Any],
    tf_dir: str,
    *,
    check_role: bool = True,
) -> str | None:
    """Validate resolved *tf_dir* and IAM role for *env*. Returns detected workload."""
    slug = (env.get("slug") or "unknown").strip()
    tf_path = (env.get("tf_directory_path") or "").strip()
    workload = infer_workload(slug, tf_path, tf_dir)
    validate_tf_dir_for_scope(tf_dir, slug, workload)
    if check_role:
        resolve_apply_role_arn(
            (env.get("aws_role_arn") or "").strip(),
            slug,
            workload,
        )
    return workload


def backend_config_from_environment(env: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    if env.get("tf_state_bucket"):
        out["bucket"] = env["tf_state_bucket"]
    if env.get("tf_lock_table"):
        out["dynamodb_table"] = env["tf_lock_table"]
    if env.get("region"):
        out["region"] = env["region"]
    return out


def _clone_dir_for_environment(env: dict[str, Any]) -> str:
    from drift_reconciler.environment_credentials import clone_base_dir

    slug = (env.get("slug") or "unknown").strip()
    return os.path.join(clone_base_dir(), slug)


def resolve_scope_binding(env: dict[str, Any], tf_dir: str) -> ScopeBinding:
    slug = (env.get("slug") or "unknown").strip()
    workload = validate_environment_scope_config(env, tf_dir)
    configured_role = (env.get("aws_role_arn") or "").strip()
    role_arn = resolve_apply_role_arn(configured_role, slug, workload)
    repo_url = (env.get("repo_url") or "").strip()
    clone_dir = _clone_dir_for_environment(env) if repo_url else tf_dir
    return ScopeBinding(
        slug=slug,
        tf_dir=os.path.abspath(tf_dir),
        tf_directory_path=(env.get("tf_directory_path") or "").strip(),
        role_arn=role_arn,
        scan_role_arn=(env.get("scan_role_arn") or "").strip(),
        backend_bucket=(env.get("tf_state_bucket") or "").strip(),
        backend_lock_table=(env.get("tf_lock_table") or "").strip(),
        backend_region=(env.get("region") or "").strip(),
        repo_url=repo_url,
        clone_dir=clone_dir,
        workload=workload,
    )


def resolve_and_validate_tf_dir(env: dict[str, Any]) -> str:
    """Clone/refresh if needed, then validate terraform content vs scope."""
    from drift_reconciler.environment_credentials import resolve_tf_dir

    tf_dir = resolve_tf_dir(env)
    validate_environment_scope_config(env, tf_dir)
    return tf_dir


def assert_explicit_tf_dir(tf_dir: str, env: dict[str, Any]) -> None:
    """Fail when an explicit --tf-dir disagrees with the environment row."""
    from drift_reconciler.environment_credentials import resolve_tf_dir

    expected = os.path.abspath(resolve_tf_dir(env))
    actual = os.path.abspath(tf_dir)
    slug = (env.get("slug") or "unknown").strip()
    if actual != expected:
        raise ScopeConfigError(
            f"Terraform directory mismatch for scope '{slug}': "
            f"explicit path {actual} != configured resolve_tf_dir() {expected}. "
            f"Do not pass another scope's clone directory."
        )


def preflight_scope_configurations(
    environments: list[dict[str, Any]] | None = None,
    *,
    resolve_dirs: bool = True,
) -> list[ScopeBinding]:
    """Validate every active scope at startup. Raises ScopeConfigError on failure."""
    envs = environments if environments is not None else fetch_all_active_environments()
    bindings: list[ScopeBinding] = []
    tf_dirs: dict[str, str] = {}
    role_arns: dict[str, str] = {}

    for env in envs:
        slug = (env.get("slug") or "").strip()
        if not slug:
            raise ScopeConfigError("Active environment row is missing slug.")

        if resolve_dirs:
            tf_dir = resolve_and_validate_tf_dir(env)
        else:
            tf_dir = (env.get("tf_directory_path") or "").strip()
            if not tf_dir or not os.path.isdir(tf_dir):
                raise ScopeConfigError(
                    f"Scope '{slug}' terraform path not found locally: {tf_dir!r}"
                )
            validate_environment_scope_config(env, tf_dir)

        binding = resolve_scope_binding(env, tf_dir)
        bindings.append(binding)

        other_slug = tf_dirs.get(binding.tf_dir)
        if other_slug and other_slug != slug:
            raise ScopeConfigError(
                f"Scopes '{other_slug}' and '{slug}' resolve to the same terraform "
                f"directory {binding.tf_dir}."
            )
        tf_dirs[binding.tf_dir] = slug

        if binding.role_arn:
            other_slug = role_arns.get(binding.role_arn)
            if other_slug and other_slug != slug:
                raise ScopeConfigError(
                    f"Scopes '{other_slug}' and '{slug}' share the same aws_role_arn "
                    f"{binding.role_arn}."
                )
            role_arns[binding.role_arn] = slug

        logger.info(
            "Scope preflight OK: slug=%s workload=%s tf_dir=%s role_arn=%s "
            "backend_bucket=%s clone_dir=%s",
            binding.slug,
            binding.workload or "(unspecified)",
            binding.tf_dir,
            binding.role_arn,
            binding.backend_bucket or "(none)",
            binding.clone_dir,
        )

    return bindings
