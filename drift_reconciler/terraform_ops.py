"""Terraform init / plan helpers."""
from __future__ import annotations

import json
import os
import re
import subprocess

from drift_reconciler.environment_credentials import _resolve_env_credentials

from terraform_errors import humanize_terraform_error

def _strip_hardcoded_aws_profile(tf_dir: str) -> None:
    """Remove hardcoded AWS profile settings from Terraform files."""
    profile_line = re.compile(r'^\s*profile\s*=\s*".*"\s*$', re.MULTILINE)
    try:
        file_names = os.listdir(tf_dir)
    except OSError as e:
        print(f"Failed to inspect Terraform directory {tf_dir}: {e}")
        return

    for file_name in file_names:
        filepath = os.path.join(tf_dir, file_name)
        if not file_name.endswith(".tf"):
            continue
        try:
            if not os.path.isfile(filepath):
                continue
            with open(filepath, "r", encoding="utf-8") as f:
                contents = f.read()
            stripped_contents, count = profile_line.subn("", contents)
            if count:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(stripped_contents)
                print(f"Stripped hardcoded AWS profile from {filepath}")
        except (OSError, UnicodeError) as e:
            print(f"Failed to strip hardcoded AWS profile from {filepath}: {e}")


def _terraform_sub_env_for_scope(scope: str, tf_dir: str | None = None) -> dict:
    """Return a subprocess env with *scope*'s AssumeRole credentials injected."""
    from drift_reconciler.scope_resolution import fetch_environment_row

    env_dict = fetch_environment_row(scope)
    return _resolve_env_credentials(env_dict, tf_dir=tf_dir)


def _read_cached_backend_config(tfstate_file: str) -> dict:
    try:
        with open(tfstate_file, "r", encoding="utf-8") as f:
            tfstate = json.load(f)
        cached = tfstate.get("backend", {}).get("config", {})
        return cached if isinstance(cached, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _backend_config_differs(cached: dict, backend_config: dict) -> bool:
    """True when any non-empty *backend_config* value differs from *cached*."""
    for key, value in backend_config.items():
        if not value:
            continue
        if str(cached.get(key, "")) != str(value):
            return True
    return False


def _terraform_plan_needs_reinit(stderr: str) -> bool:
    s = stderr.lower()
    return (
        "backend initialization required" in s
        or "backend configuration block has changed" in s
    )


def _ensure_terraform_init(
    tf_dir: str,
    env: dict | None = None,
    backend_config: dict | None = None,
    force: bool = False,
) -> str:
    """Run ``terraform init`` in *tf_dir* only when it isn't already
    initialized — detected via ``.terraform/terraform.tfstate``, the backend
    cache ``terraform init`` writes for EVERY config, module-less included
    (``modules.json`` only exists when the config has modules).  Returns "" on
    success or when init was skipped, or an error string on failure — the
    caller must not proceed to plan when non-empty.
    
    If *backend_config* is provided (a dict with keys like 'bucket', 
    'dynamodb_table', 'region'), appends -backend-config flags for each 
    non-empty value to override the static backend block.
    
    If .terraform is already initialized but the cached backend differs 
    (detected via .terraform/terraform.tfstate bucket mismatch), forces 
    -reconfigure to re-initialize against the new backend."""
    _strip_hardcoded_aws_profile(tf_dir)
    tfstate_file = os.path.join(tf_dir, ".terraform", "terraform.tfstate")

    # Check if already initialized and whether backend needs reconfiguration.
    # Keyed off .terraform/terraform.tfstate (the backend cache init writes
    # for ALL configs, module or not) — modules.json is NOT a reliable
    # indicator: module-less configs (prod-kyc/prod-cra's ec2_terraform_account_a/)
    # never get it written, so the old check re-ran init on every scan
    # (measured ~21s warm against the real backend).  Verified empirically:
    # real backend init on a module-less layout writes terraform.tfstate +
    # lock.hcl + providers/ but no modules.json.  trivy_agent's
    # _is_terraform_initialized keeps the modules.json probe — it only
    # powers a CLI note, no gate.
    force_reconfigure = False
    providers_dir = os.path.join(tf_dir, ".terraform", "providers")
    providers_ready = False
    if os.path.isdir(providers_dir):
        try:
            with os.scandir(providers_dir) as entries:
                providers_ready = any(entries)
        except OSError:
            providers_ready = False

    if not force and os.path.isfile(tfstate_file):
        # Backend mismatch: compare all -backend-config keys against init cache.
        if backend_config and any(backend_config.values()):
            cached = _read_cached_backend_config(tfstate_file)
            if _backend_config_differs(cached, backend_config):
                force_reconfigure = True
                print(
                    "Backend config mismatch between cache and environment row "
                    f"(cached={cached!r} new={backend_config!r}) — forcing -reconfigure"
                )

        if not force_reconfigure and providers_ready:
            return ""  # already initialized with matching backend — skip re-init cost

    print(f"Step 0: Running 'terraform init' inside: {tf_dir}...")
    try:
        cmd = ["terraform", "init", "-no-color", "-input=false"]
        
        # Add -reconfigure if backend mismatch detected OR if backend_config is being passed
        # (backend_config overrides require -reconfigure to accept the new backend config)
        if force or force_reconfigure or (backend_config and any(backend_config.values())):
            cmd.append("-reconfigure")
        
        # Append -backend-config flags for each non-empty field in backend_config
        if backend_config:
            for key, value in backend_config.items():
                if value:  # Only include non-empty values
                    cmd.append(f"-backend-config={key}={value}")
        
        # Log the actual terraform command being executed
        print(f"  Terraform command: {' '.join(cmd)}")
        
        # Cold-init detection: no cached providers yet → first-ever init for
        # this clone (new environment) must download providers; give it 900s
        # instead of the 300s that fits warm inits with a provider cache.
        cold_init = not os.path.isdir(os.path.join(tf_dir, ".terraform", "providers"))
        import agent as _ag
        _ag.subprocess.run(
            cmd,
            cwd=tf_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900 if cold_init else 300,
        )
        return ""
    except subprocess.CalledProcessError as e:
        return f"Terraform Init Failed:\n{e.stderr}"
    except subprocess.TimeoutExpired:
        return ("Terraform Init timed out — likely cold provider download "
                "for a new environment; retry or increase the init timeout")


def get_terraform_drift_data(tf_dir: str, drift_script_path: str) -> str:
    import agent as _ag
    _account_label = _ag._account_label
    """Executes CLI commands using the supplied terraform directory and
    drift-formatting script path."""
    if not os.path.exists(tf_dir):
        return f"Error: The Terraform directory '{tf_dir}' does not exist."

    sub_env = _terraform_sub_env_for_scope(_account_label, tf_dir=tf_dir)

    from drift_reconciler.scope_resolution import (
        backend_config_from_environment,
        fetch_environment_row,
    )

    env_dict = fetch_environment_row(_account_label)
    backend_config = backend_config_from_environment(env_dict)
    print(f"Backend config loaded from environment row: {backend_config}")

    init_error = _ensure_terraform_init(tf_dir, env=sub_env, backend_config=backend_config)
    if init_error:
        return init_error

    print(f"Step 1: Running 'terraform plan' inside: {tf_dir}...")
    # Drop leftover plan artifacts so we never format a previous scan's tfplan.
    for stale_name in ("tfplan", "plan.json"):
        stale_path = os.path.join(tf_dir, stale_name)
        try:
            os.remove(stale_path)
        except OSError:
            pass
    # Force refresh so live AWS drift is visible even if TF_CLI_ARGS sets
    # -refresh=false. -input=false avoids interactive prompts in containers.
    try:
        plan_proc = subprocess.run(
            [
                "terraform", "plan",
                "-no-color",
                "-input=false",
                "-refresh=true",
                "-out=tfplan",
            ],
            cwd=tf_dir,
            env=sub_env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        plan_stdout = (plan_proc.stdout or "")[-1500:]
        if plan_stdout.strip():
            print(f"  terraform plan (tail):\n{plan_stdout}")
    except subprocess.CalledProcessError as e:
        return f"Terraform Plan Failed:\n{e.stderr}"

    print("Step 2: Exporting plan to JSON using Native Python...")
    try:
        show_result = subprocess.run(
            ["terraform", "show", "-no-color", "-json", "tfplan"],
            cwd=tf_dir,
            env=sub_env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        plan_json_path = os.path.join(tf_dir, "plan.json")
        with open(plan_json_path, "w", encoding="utf-8", newline="") as f:
            f.write(show_result.stdout)

    except subprocess.CalledProcessError as e:
        return f"Exporting plan.json Failed:\n{e.stderr}"
    except Exception as e:
        return f"Writing plan.json file failed:\n{str(e)}"

    print("Step 3: Formatting drift report (in-process)...")
    target_plan_json = os.path.join(tf_dir, "plan.json")
    # Call report_drift in-process — subprocess + PYTHONPATH was a recurring
    # source of silent ModuleNotFoundError → "no_drift" in unmanaged modes.
    try:
        from drift_reconciler.formatting_drift_json import (
            get_prior_state_addresses,
            load_plan,
            report_drift,
        )

        plan_data = load_plan(target_plan_json)
        rd = plan_data.get("resource_drift") or []
        rc = plan_data.get("resource_changes") or []
        non_noop = [
            c for c in rc
            if (c.get("change") or {}).get("actions") not in (None, [], ["no-op"], ["read"])
        ]
        print(
            f"  Plan JSON stats: resource_drift={len(rd)} "
            f"resource_changes={len(rc)} non_noop_changes={len(non_noop)} "
            f"prior_state_addrs={len(get_prior_state_addresses(plan_data))}"
        )
        for c in non_noop[:20]:
            print(
                f"    change: {c.get('address')} "
                f"actions={(c.get('change') or {}).get('actions')}"
            )
        report = report_drift(plan_data, tf_dir=tf_dir, scope=_account_label)
        actionable = [
            r for r in (report.get("resources") or [])
            if r.get("changes") or r.get("status") == "deleted_externally"
        ]
        print(
            f"  Formatted drift report: type={report.get('report_type')!r} "
            f"resources={len(report.get('resources') or [])} "
            f"actionable={len(actionable)} "
            f"suppressed={len(report.get('suppressed_resources') or [])}"
        )
        return json.dumps(report)
    except Exception as e:
        return f"Formatting Drift JSON Failed:\n{e}"

# ==========================================
# 2. LANGGRAPH STRUCTURE
# ==========================================
