# Webhook Refactoring: Database-Driven Scope Resolution + Per-Environment Secrets

## Summary

Successfully refactored GitHub webhook handler (`_handle_github_webhook`) in `dashboard/serve.py` to use database-driven scope resolution and per-environment webhook secrets instead of hardcoded PR title regex and a single global GitHub token.

## Changes Made

### 1. Database Migration

**File**: `migrations/add_webhook_secret_to_environment_secrets.sql` (NEW)

Added `webhook_secret` column to `environment_secrets` table:
- Type: TEXT (nullable)
- Default: NULL (backward compatible)
- Behavior: Per-environment secret for GitHub webhook HMAC verification
- Fallback: If NULL, uses global GITHUB_TOKEN from env or app_settings

```sql
alter table environment_secrets
  add column if not exists webhook_secret text;
```

### 2. Webhook Handler Refactoring

**File**: `dashboard/serve.py` → `_handle_github_webhook()` method

**Key Changes**:

#### a. Read Raw Body First
- Moved raw body read to beginning (before any other processing)
- Parse JSON payload early to extract unverified `repository.full_name`

#### b. Resolve Environment by Repository URL
- **Before**: Extracted scope from PR title using regex `[scope-[a-z0-9][a-z0-9-]*]`
- **After**: 
  1. Extract unverified `repository.full_name` from webhook payload
  2. Query `environments` table for all active environments with `repo_url` set
  3. Use `_parse_repo_url()` to normalize each environment's `repo_url`
  4. Match against webhook's repo full_name (case-insensitive)
  5. Return environment record with slug, id, and repo_url

#### c. Per-Environment Secret Verification
- **Before**: Single global webhook secret (GITHUB_TOKEN env or app_settings)
- **After**:
  1. Query `environment_secrets` table for resolved environment's `webhook_secret`
  2. If `webhook_secret` is set (not NULL), use it for HMAC verification
  3. If NULL, fall back to global GITHUB_TOKEN
  4. Return 401 Unauthorized if no secret available

#### d. Unified 401 Response
- **Before**: Different error messages for "no repo found" vs "signature mismatch"
- **After**: Both cases return same 401 Unauthorized (security: no info leak about configured repos)

#### e. Scope from Database
- **Before**: scope hardcoded via regex from PR title
- **After**: scope = `environments.slug` from resolved environment record

#### f. Guards Remain Unchanged
- `action == "closed"` ✅
- `merged == true` ✅
- `"Drift fix"` in PR title ✅
- These act as additional validation after signature verification

### 3. Environment POST Handler Update

**File**: `dashboard/serve.py` → `_handle_environments_post()` method

Added `_webhook_secret` to the list of secret fields:
```python
for k in ("_github_token", "_aws_access_key_id", "_aws_secret_access_key", "_webhook_secret"):
    val = (body.get(k) or "").strip()
    if val:
        secrets_to_write[k.lstrip("_")] = val
```

Now accepts new environment creation with optional per-environment webhook secret.

### 4. Environment PATCH Handler Update

**File**: `dashboard/serve.py` → `_handle_environments_patch()` method

Added `_webhook_secret` to:
- Allowed variables to parse from request body
- Check for "no updates" guard
- secrets_to_write loop for persisting to environment_secrets

Allows updating webhook secret on existing environments without changing other fields.

### 5. Environment GET Handler Update

**File**: `dashboard/serve.py` → `_serve_environments()` method

Enhanced secret fetching and masking:
- Now fetches `webhook_secret` in addition to github_token and AWS credentials
- Adds to response: `webhook_secret_configured` (bool) and `webhook_secret_masked` (string)
- Follows same masking pattern as other secrets (shows last 4 chars if set)

## Flow Diagram

```
GitHub PR closed event
        ↓
POST /api/webhooks/github
        ↓
Read raw body (for HMAC verification)
        ↓
Parse JSON to extract repository.full_name (unverified)
        ↓
Query environments table for active rows with repo_url set
        ↓
For each env: normalize repo_url with _parse_repo_url()
        ↓
Match against webhook's repo_full_name (case-insensitive)
        ↓
Resolved ✓ → continue    │    Not found ✗ → return 401 "Unauthorized"
        ↓
Query environment_secrets for that environment's webhook_secret
        ↓
Have webhook_secret? ✓   │    No (NULL) → fallback to global GITHUB_TOKEN
        ↓
Verify HMAC-SHA256 signature
        ↓
Valid ✓ → continue        │    Invalid ✗ → return 401 "Unauthorized"
        ↓
Validate payload:
  - action == "closed" ✓
  - merged == true ✓
  - "Drift fix" in title ✓
        ↓
All pass ✓ → insert     │    Any fail ✗ → return 204 "No Content"
pending_applies row
with scope = environments.slug
        ↓
Return 200 OK {"ok": true}
```

## Security Improvements

1. **Per-Environment Secrets**: Different repos/environments can have different webhook secrets, enabling key rotation without affecting other environments
2. **No Info Leak**: 401 returned uniformly for both "repo not configured" and "signature invalid" — attacker can't probe for configured repos
3. **Fallback Chain**: Per-env → global GITHUB_TOKEN → error (graceful degradation)
4. **Unverified Early Read**: Extract repo_full_name before signature verification (safe—only reading identifier, not trusting for state changes)

## Backward Compatibility

✅ **Fully backward compatible**:
- Existing environments without `webhook_secret` set automatically fall back to global GITHUB_TOKEN
- No breaking changes to API contracts
- Existing webhooks continue to work with global secret
- Gradual migration: set webhook_secret when ready per-environment

## Testing

PowerShell test command provided in: `WEBHOOK_TEST.ps1`

Setup:
1. Set environment's `repo_url` (e.g., `https://github.com/owner/repo.git`)
2. Set `_webhook_secret` via PATCH to `/api/environments/{id}` or POST to `/api/environments`
3. Run test script with correct `$repo_full_name`, `$webhook_secret`, and `$pr_title`

Expected Results:
- ✅ Valid signature + merged + "Drift fix" + correct repo → 200 OK, pending_applies inserted
- ❌ Invalid signature or repo not configured → 401 Unauthorized
- ⏸️ Unmerged PR or missing "Drift fix" → 204 No Content

## Files Modified

1. `migrations/add_webhook_secret_to_environment_secrets.sql` (NEW)
2. `dashboard/serve.py`:
   - `_handle_github_webhook()` — completely refactored (100+ lines)
   - `_handle_environments_post()` — added webhook_secret field handling
   - `_handle_environments_patch()` — added webhook_secret field handling
   - `_serve_environments()` — added webhook_secret fetching & masking
3. `WEBHOOK_TEST.ps1` (NEW) — PowerShell test command

## Rollback

To revert to hardcoded scope regex + global secret:
1. Revert serve.py changes
2. Run migration to drop `webhook_secret` column (if needed)
3. GitHub webhooks will use global GITHUB_TOKEN again

## Next Steps (Optional)

1. Frontend form to display webhook_secret input field in environment creation/edit UI
2. Add webhook_secret to environment edit dialog (alongside github_token field)
3. Documentation update: "Configuring Per-Environment Webhooks"
4. Audit logging: record when webhook_secret is set/changed
