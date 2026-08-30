"""Terraform init / plan helpers."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from drift_reconciler.environment_credentials import _resolve_env_credentials

from terraform_errors import humanize_terraform_error

def _terraform_sub_env_for_scope(scope: str) -> dict:
    """Return a subprocess env with *scope*'s AWS credentials injected
    (role/keys via ``_resolve_env_credentials``), or a plain os.environ
    copy when the environment row can't be fetched — falling back to the
    server's ambient credentials exactly like before."""
    import requests as _requests

    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    env_dict = {}
    if url and key:
        try:
            resp = _requests.get(
                f"{url}/rest/v1/environments?select=*&slug=eq.{scope}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                env_dict = resp.json()[0]
        except _requests.RequestException:
            env_dict = {}
    if not env_dict:
        return os.environ.copy()
    return _resolve_env_credentials(env_dict)


def _ensure_terraform_init(tf_dir: str, env: dict | None = None, backend_config: dict | None = None) -> str:
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
    if os.path.isfile(tfstate_file):
        # Backend mismatch detection: compare cached bucket against new backend_config
        if backend_config and backend_config.get("bucket"):
            new_bucket = backend_config["bucket"]
            try:
                with open(tfstate_file, "r", encoding="utf-8") as f:
                    tfstate = json.load(f)
                    cached_bucket = tfstate.get("backend", {}).get("config", {}).get("bucket")
                    if cached_bucket and cached_bucket != new_bucket:
                        force_reconfigure = True
                        print(f"Backend bucket mismatch: cached={cached_bucket} new={new_bucket} — forcing -reconfigure")
            except (json.JSONDecodeError, IOError):
                pass  # If we can't read tfstate, proceed without forcing reconfigure

        if not force_reconfigure:
            return ""  # already initialized with matching backend — skip re-init cost

    print(f"Step 0: Running 'terraform init' inside: {tf_dir}...")
    try:
        cmd = ["terraform", "init", "-no-color", "-input=false"]
        
        # Add -reconfigure if backend mismatch detected OR if backend_config is being passed
        # (backend_config overrides require -reconfigure to accept the new backend config)
        if force_reconfigure or (backend_config and any(backend_config.values())):
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
    import requests as _requests

    if not os.path.exists(tf_dir):
        return f"Error: The Terraform directory '{tf_dir}' does not exist."

    sub_env = _terraform_sub_env_for_scope(_account_label)
    
    # Fetch environment row to extract backend config (tf_state_bucket, tf_lock_table, region)
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    env_dict = {}
    if url and key:
        try:
            resp = _requests.get(
                f"{url}/rest/v1/environments?select=*&slug=eq.{_account_label}",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                env_dict = resp.json()[0]
        except _requests.RequestException:
            pass  # Fall back to empty backend_config if fetch fails
    
    # Build backend_config dict from environment row
    backend_config = {}
    if env_dict:
        if env_dict.get("tf_state_bucket"):
            backend_config["bucket"] = env_dict["tf_state_bucket"]
        if env_dict.get("tf_lock_table"):
            backend_config["dynamodb_table"] = env_dict["tf_lock_table"]
        if env_dict.get("region"):
            backend_config["region"] = env_dict["region"]
        print(f"Backend config loaded from environment row: {backend_config}")
    else:
        print(f"No environment row found for slug '{_account_label}' (SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY may be missing)")

    init_error = _ensure_terraform_init(tf_dir, env=sub_env, backend_config=backend_config)
    if init_error:
        return init_error

    print(f"Step 1: Running 'terraform plan' inside: {tf_dir}...")
    try:
        subprocess.run(
            ["terraform", "plan", "-no-color", "-out=tfplan"],
            cwd=tf_dir,
            env=sub_env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as e:
        return f"Terraform Plan Failed:\n{e.stderr}"

    print("Step 2: Exporting plan to JSON using Native Python...")
    try:
        show_result = subprocess.run(
            ["terraform", "show", "-no-color", "-json", "tfplan"],
            cwd=tf_dir,
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

    print("Step 3: Processing drift format script...")
    target_plan_json = os.path.join(tf_dir, "plan.json")

    format_script_cmd = [
        "python",
        drift_script_path,
        target_plan_json,
        "--account", _account_label,
    ]
    try:
        result = subprocess.run(
            format_script_cmd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Formatting Drift JSON Script Failed:\n{e.stderr}"

# ==========================================
# 2. LANGGRAPH STRUCTURE
# ==========================================
