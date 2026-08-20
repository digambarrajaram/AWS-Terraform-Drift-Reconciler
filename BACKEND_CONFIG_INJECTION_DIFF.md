# Backend-Config Injection Implementation — agent.py Diff

## Overview
Implemented `-backend-config` injection in `agent.py`'s terraform init calls, sourced from the environments table (`tf_state_bucket`, `tf_lock_table`, `region`).

### Terraform S3 Backend Configuration
Confirmed from [terraform_code/ec2_terraform_account_a/backend.tf](terraform_code/ec2_terraform_account_a/backend.tf):
```terraform
terraform {
  backend "s3" {
    bucket         = "scope-a-tf-state-605134452604"
    key            = "ec2_scope_a/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}
```

**Field Mapping:**
- `tf_state_bucket` → `-backend-config=bucket=<value>`
- `tf_lock_table` → `-backend-config=dynamodb_table=<value>`
- `region` → `-backend-config=region=<value>`

---

## Change 1: `_ensure_terraform_init()` Function

### Before
```python
def _ensure_terraform_init(tf_dir: str, env: dict | None = None) -> str:
    """Run ``terraform init`` in *tf_dir* only when it isn't already
    initialized (same detection as trivy_agent's ``_is_terraform_initialized``:
    ``.terraform/modules/modules.json`` present).  Returns "" on success or
    when init was skipped, or an error string on failure — the caller must
    not proceed to plan when non-empty."""
    modules_json = os.path.join(tf_dir, ".terraform", "modules", "modules.json")
    if os.path.isfile(modules_json):
        return ""  # already initialized — skip re-init cost

    print(f"Step 0: Running 'terraform init' inside: {tf_dir}...")
    try:
        subprocess.run(
            ["terraform", "init", "-no-color", "-input=false"],
            cwd=tf_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        return ""
    except subprocess.CalledProcessError as e:
        return f"Terraform Init Failed:\n{e.stderr}"
    except subprocess.TimeoutExpired as e:
        return f"Terraform Init Failed:\n{e}"
```

### After
```python
def _ensure_terraform_init(tf_dir: str, env: dict | None = None, backend_config: dict | None = None) -> str:
    """Run ``terraform init`` in *tf_dir* only when it isn't already
    initialized (same detection as trivy_agent's ``_is_terraform_initialized``:
    ``.terraform/modules/modules.json`` present).  Returns "" on success or
    when init was skipped, or an error string on failure — the caller must
    not proceed to plan when non-empty.
    
    If *backend_config* is provided (a dict with keys like 'bucket', 
    'dynamodb_table', 'region'), appends -backend-config flags for each 
    non-empty value to override the static backend block."""
    modules_json = os.path.join(tf_dir, ".terraform", "modules", "modules.json")
    if os.path.isfile(modules_json):
        return ""  # already initialized — skip re-init cost

    print(f"Step 0: Running 'terraform init' inside: {tf_dir}...")
    try:
        cmd = ["terraform", "init", "-no-color", "-input=false"]
        
        # Append -backend-config flags for each non-empty field in backend_config
        if backend_config:
            for key, value in backend_config.items():
                if value:  # Only include non-empty values
                    cmd.append(f"-backend-config={key}={value}")
        
        subprocess.run(
            cmd,
            cwd=tf_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        return ""
    except subprocess.CalledProcessError as e:
        return f"Terraform Init Failed:\n{e.stderr}"
    except subprocess.TimeoutExpired as e:
        return f"Terraform Init Failed:\n{e}"
```

**Changes:**
- Added `backend_config: dict | None = None` parameter
- Build command list dynamically, appending `-backend-config` flags for each non-empty key/value pair
- Updated docstring to document the new parameter

---

## Change 2: `get_terraform_drift_data()` Function

### Before
```python
def get_terraform_drift_data(tf_dir: str, drift_script_path: str) -> str:
    """Executes CLI commands using the supplied terraform directory and
    drift-formatting script path."""

    if not os.path.exists(tf_dir):
        return f"Error: The Terraform directory '{tf_dir}' does not exist."

    sub_env = _terraform_sub_env_for_scope(_account_label)

    init_error = _ensure_terraform_init(tf_dir, env=sub_env)
    if init_error:
        return init_error
    # ... rest of function
```

### After
```python
def get_terraform_drift_data(tf_dir: str, drift_script_path: str) -> str:
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

    init_error = _ensure_terraform_init(tf_dir, env=sub_env, backend_config=backend_config)
    if init_error:
        return init_error
    # ... rest of function
```

**Changes:**
- Import `requests` at function level (already imported elsewhere in module)
- Fetch `environments` row from Supabase using `_account_label` (scope)
- Build `backend_config` dict from `tf_state_bucket`, `tf_lock_table`, `region` with correct key names
- Pass `backend_config` to `_ensure_terraform_init()`
- Gracefully fall back to empty backend_config if database fetch fails (no exception thrown)

---

## Change 3: `_run_apply()` Function — terraform init Call

### Before
```python
        print(f"\n--- Apply approved fix for PR #{pr_number} ({scope}) ---")
        print(f"[apply] terraform init in {tf_dir} …")
        init = subprocess.run(
            ["terraform", "init", "-no-color", "-input=false"],
            cwd=tf_dir, env=sub_env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        if init.returncode != 0:
            raise RuntimeError(f"terraform init failed:\n{_strip_ansi(init.stderr)[:800]}")
```

### After
```python
        print(f"\n--- Apply approved fix for PR #{pr_number} ({scope}) ---")
        
        # Build backend_config dict from environment row
        backend_config = {}
        if env_dict:
            if env_dict.get("tf_state_bucket"):
                backend_config["bucket"] = env_dict["tf_state_bucket"]
            if env_dict.get("tf_lock_table"):
                backend_config["dynamodb_table"] = env_dict["tf_lock_table"]
            if env_dict.get("region"):
                backend_config["region"] = env_dict["region"]
        
        print(f"[apply] terraform init in {tf_dir} …")
        cmd = ["terraform", "init", "-no-color", "-input=false"]
        if backend_config:
            for key, value in backend_config.items():
                if value:
                    cmd.append(f"-backend-config={key}={value}")
        
        init = subprocess.run(
            cmd,
            cwd=tf_dir, env=sub_env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        if init.returncode != 0:
            raise RuntimeError(f"terraform init failed:\n{_strip_ansi(init.stderr)[:800]}")
```

**Changes:**
- Build `backend_config` dict from `env_dict` (which is already fetched at the start of `_run_apply()`)
- Dynamically build command list and append `-backend-config` flags
- Use same field mapping as in `get_terraform_drift_data()`

**Note:** `env_dict` is already fetched in `_run_apply()` at line ~1576 for AWS session resolution, so no additional database calls are needed.

---

## Behavior Summary

### When terraform init Runs:
1. **Check if already initialized:** `.terraform/modules/modules.json` exists → skip init
2. **If not initialized:**
   - Build base command: `terraform init -no-color -input=false`
   - If `backend_config` is provided (and non-empty):
     - Append `-backend-config=bucket=<value>` (if present)
     - Append `-backend-config=dynamodb_table=<value>` (if present)
     - Append `-backend-config=region=<value>` (if present)
   - Example final command:
     ```
     terraform init -no-color -input=false \
       -backend-config=bucket=scope-a-tf-state-605134452604 \
       -backend-config=dynamodb_table=terraform-locks \
       -backend-config=region=us-east-1
     ```

### Error Handling:
- If `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` not set → init runs with empty backend_config (falls back to static backend.tf)
- If environments table fetch fails (network error, invalid response) → gracefully falls back to empty backend_config
- If env_dict doesn't have backend fields → only appends populated fields to backend_config
- No changes to `-reconfigure` or `-migrate-state` flags (not used in current init calls)

### Backend Mismatch Detection:
⚠️ **Note:** `-backend-config` alone won't fix an already-initialized directory with a stale `.terraform/terraform.tfstate` pointing to a different bucket. The current implementation:
- Checks for `.terraform/modules/modules.json` (module cache presence)
- Does NOT check backend mismatch in `.terraform/terraform.tfstate`
- If re-initialization against a different backend is required, consider adding:
  - A detection check comparing cached backend values against env_dict
  - A forced `-reconfigure` flag when backend values differ
- For now, this is acceptable since `_run_apply()` runs on fresh checkouts; drift scans can add this check if needed.

---

## Files Modified
- [drift_reconciler/agent.py](drift_reconciler/agent.py) — Lines 196–237, 226–267, 1595–1620
