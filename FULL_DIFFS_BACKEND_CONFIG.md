# Complete Diffs: Backend-Config Injection in agent.py

## Overview
Three functions updated to implement backend-config injection sourced from environments table:
1. **_ensure_terraform_init()** — New parameter with backend mismatch detection
2. **get_terraform_drift_data()** — Fetches environment row and builds backend_config
3. **_run_apply()** — Uses already-fetched env_dict to build backend_config

### Terraform S3 Backend Confirmed
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
- `tf_state_bucket` → `bucket`
- `tf_lock_table` → `dynamodb_table`
- `region` → `region`

---

## DIFF 1: `_ensure_terraform_init()` Function

### BEFORE
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

### AFTER
```python
def _ensure_terraform_init(tf_dir: str, env: dict | None = None, backend_config: dict | None = None) -> str:
    """Run ``terraform init`` in *tf_dir* only when it isn't already
    initialized (same detection as trivy_agent's ``_is_terraform_initialized``:
    ``.terraform/modules/modules.json`` present).  Returns "" on success or
    when init was skipped, or an error string on failure — the caller must
    not proceed to plan when non-empty.
    
    If *backend_config* is provided (a dict with keys like 'bucket', 
    'dynamodb_table', 'region'), appends -backend-config flags for each 
    non-empty value to override the static backend block.
    
    If .terraform is already initialized but the cached backend differs 
    (detected via .terraform/terraform.tfstate bucket mismatch), forces 
    -reconfigure to re-initialize against the new backend."""
    modules_json = os.path.join(tf_dir, ".terraform", "modules", "modules.json")
    tfstate_file = os.path.join(tf_dir, ".terraform", "terraform.tfstate")
    
    # Check if already initialized and whether backend needs reconfiguration
    force_reconfigure = False
    if os.path.isfile(modules_json):
        # Backend mismatch detection: compare cached bucket against new backend_config
        if backend_config and backend_config.get("bucket"):
            new_bucket = backend_config["bucket"]
            if os.path.isfile(tfstate_file):
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

### Key Changes

**1. New Parameter**
```python
backend_config: dict | None = None
```
- Optional dict with keys: `bucket`, `dynamodb_table`, `region`
- None by default (backward compatible)

**2. Backend Mismatch Detection**
```python
modules_json = os.path.join(tf_dir, ".terraform", "modules", "modules.json")
tfstate_file = os.path.join(tf_dir, ".terraform", "terraform.tfstate")

force_reconfigure = False
if os.path.isfile(modules_json):
    # Check if backend bucket differs between cached .terraform/terraform.tfstate and new backend_config
    if backend_config and backend_config.get("bucket"):
        new_bucket = backend_config["bucket"]
        if os.path.isfile(tfstate_file):
            try:
                with open(tfstate_file, "r", encoding="utf-8") as f:
                    tfstate = json.load(f)
                    cached_bucket = tfstate.get("backend", {}).get("config", {}).get("bucket")
                    if cached_bucket and cached_bucket != new_bucket:
                        force_reconfigure = True
                        print(f"Backend bucket mismatch: cached={cached_bucket} new={new_bucket} — forcing -reconfigure")
            except (json.JSONDecodeError, IOError):
                pass
    
    if not force_reconfigure:
        return ""  # Skip init if already initialized with matching backend
```

- Reads `.terraform/terraform.tfstate` to extract cached backend bucket
- Compares against new `backend_config['bucket']`
- Sets `force_reconfigure = True` if they differ
- Prints diagnostic message when mismatch detected

**3. Dynamic Command Building**
```python
cmd = ["terraform", "init", "-no-color", "-input=false"]

# Add -reconfigure if backend mismatch detected OR if backend_config is being passed
if force_reconfigure or (backend_config and any(backend_config.values())):
    cmd.append("-reconfigure")

# Append -backend-config flags for each non-empty field in backend_config
if backend_config:
    for key, value in backend_config.items():
        if value:  # Only include non-empty values
            cmd.append(f"-backend-config={key}={value}")

# Log the actual terraform command being executed
print(f"  Terraform command: {' '.join(cmd)}")
```

- `terraform init -no-color -input=false -reconfigure -backend-config=bucket=... -backend-config=dynamodb_table=... -backend-config=region=...`

---

## DIFF 2: `get_terraform_drift_data()` Function

### BEFORE
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

    print(f"Step 1: Running 'terraform plan' inside: {tf_dir}...")
    # ... rest of function
```

### AFTER
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
        print(f"Backend config loaded from environment row: {backend_config}")
    else:
        print(f"No environment row found for slug '{_account_label}' (SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY may be missing)")

    init_error = _ensure_terraform_init(tf_dir, env=sub_env, backend_config=backend_config)
    if init_error:
        return init_error

    print(f"Step 1: Running 'terraform plan' inside: {tf_dir}...")
    # ... rest of function
```

### Key Changes

**1. Import requests at function level**
```python
import requests as _requests
```

**2. Fetch environment row from Supabase**
```python
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
```

- Uses already-set global `_account_label` (passed from CLI args)
- Queries `environments` table for row with matching `slug`
- Gracefully fails if Supabase not available (backend_config will be empty)

**3. Build backend_config dict**
```python
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
```

- Maps environment table fields to Terraform backend parameter names
- Only includes non-empty fields
- Logs what backend_config was constructed

**4. Pass backend_config to _ensure_terraform_init**
```python
init_error = _ensure_terraform_init(tf_dir, env=sub_env, backend_config=backend_config)
```

---

## DIFF 3: `_run_apply()` Function

### BEFORE
```python
        sub_env = _resolve_env_credentials(env_dict)

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

### AFTER
```python
        sub_env = _resolve_env_credentials(env_dict)

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

### Key Changes

**1. Build backend_config from already-fetched env_dict**
```python
backend_config = {}
if env_dict:
    if env_dict.get("tf_state_bucket"):
        backend_config["bucket"] = env_dict["tf_state_bucket"]
    if env_dict.get("tf_lock_table"):
        backend_config["dynamodb_table"] = env_dict["tf_lock_table"]
    if env_dict.get("region"):
        backend_config["region"] = env_dict["region"]
```

- `env_dict` already fetched at start of `_run_apply()` for AWS credential resolution
- Reuses same pattern as `get_terraform_drift_data()`
- No additional database calls needed

**2. Append -backend-config flags to command**
```python
cmd = ["terraform", "init", "-no-color", "-input=false"]
if backend_config:
    for key, value in backend_config.items():
        if value:
            cmd.append(f"-backend-config={key}={value}")

init = subprocess.run(cmd, ...)
```

- Example command:
  ```
  terraform init -no-color -input=false \
    -backend-config=bucket=scope-a-tf-state-605134452604 \
    -backend-config=dynamodb_table=terraform-locks \
    -backend-config=region=us-east-1
  ```

---

## Backend Mismatch Detection Logic

### When Does -reconfigure Get Added?

1. **Mismatch Detected**: If `.terraform/terraform.tfstate` has a cached backend bucket that differs from `backend_config['bucket']`
   - Action: `force_reconfigure = True`
   - Message: `"Backend bucket mismatch: cached=<old> new=<new> — forcing -reconfigure"`

2. **Backend Config Provided**: If `backend_config` dict has any non-empty values
   - Action: Add `-reconfigure` flag
   - Reason: Terraform requires `-reconfigure` when overriding backend config with `-backend-config` flags

### Command Examples

**Scenario A: Fresh init (no .terraform/)**
```bash
terraform init -no-color -input=false \
  -reconfigure \
  -backend-config=bucket=scope-a-tf-state-605134452604 \
  -backend-config=dynamodb_table=terraform-locks \
  -backend-config=region=us-east-1
```

**Scenario B: Reinit with backend mismatch (old bucket cached)**
```bash
# Detects mismatch, prints:
# "Backend bucket mismatch: cached=wrong-bucket new=scope-a-tf-state-605134452604 — forcing -reconfigure"

terraform init -no-color -input=false \
  -reconfigure \
  -backend-config=bucket=scope-a-tf-state-605134452604 \
  -backend-config=dynamodb_table=terraform-locks \
  -backend-config=region=us-east-1
```

**Scenario C: Already initialized with matching backend**
```bash
# Both modules.json exists AND backend matches
# Returns "" immediately (skips init entirely)
```

---

## Error Handling

- ✅ Missing Supabase credentials → Logs message, proceeds with empty `backend_config`
- ✅ Database query fails → Catches `RequestException`, falls back gracefully
- ✅ Invalid JSON in tfstate → Catches `JSONDecodeError`, proceeds without forcing reconfigure
- ✅ File read error on tfstate → Catches `IOError`, proceeds
- ✅ Terraform init failure → Returns error message with stderr (unchanged behavior)
- ✅ Terraform timeout → Returns timeout error (unchanged behavior)

---

## Testing Scenarios

1. **Fresh clone**: No `.terraform/` directory
   - ✅ Runs with `-reconfigure` and `-backend-config` flags
   - ✅ Initializes against backend from database

2. **Cached with matching backend**:  
   - ✅ Checks modules.json exists
   - ✅ Parses tfstate, bucket matches
   - ✅ Returns "" immediately (skips re-init)

3. **Cached with mismatched backend**:
   - ✅ Checks modules.json exists
   - ✅ Parses tfstate, bucket differs
   - ✅ Prints diagnostic message
   - ✅ Runs with `-reconfigure` to reinitialize against new backend

4. **No Supabase credentials**:
   - ✅ Logs that no environment row found
   - ✅ backend_config is empty dict
   - ✅ Runs `terraform init -no-color -input=false` (uses static backend.tf)

---

## Files Modified

- [drift_reconciler/agent.py](drift_reconciler/agent.py)
  - Lines 196-262: `_ensure_terraform_init()` (67 lines total)
  - Lines 269-305: `get_terraform_drift_data()` (37 lines added)
  - Lines 1600-1626: `_run_apply()` init section (27 lines added)
