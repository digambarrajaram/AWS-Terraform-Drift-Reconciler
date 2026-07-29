# AWS Terraform Drift Reconciler — Backend Reference

**Complete backend documentation for rebuilding the frontend from scratch.**

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Technology Stack](#technology-stack)
3. [Database Schema](#database-schema)
4. [API Reference (serve.py)](#api-reference)
5. [Supabase Direct Queries (Anon Key)](#supabase-direct-queries)
6. [Supabase Realtime (WebSocket)](#supabase-realtime)
7. [Authentication & Authorization](#authentication--authorization)
8. [Feature Modules — Deep Dive](#feature-modules)
9. [LangGraph Pipeline](#langgraph-pipeline)
10. [Third-Party Integrations](#third-party-integrations)
11. [GitHub Actions Workflows](#github-actions-workflows)
12. [Environment Variables](#environment-variables)
13. [Frontend Implementation Guide](#frontend-implementation-guide)

---

## Architecture Overview

The system detects configuration drift in AWS infrastructure managed by Terraform, alerts on findings via PagerDuty/Slack, and creates GitHub PRs with code fixes. A dashboard provides scan triggering, real-time log streaming, rollback management, exception handling, and trend analysis.

```
┌──────────────────────────────────────────────────────────────┐
│  Browser (Dashboard SPA)                                     │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌──────┐ ┌──────────┐ │
│  │Overview │ │  Scan    │ │PR Queue│ │Roll- │ │Trends/   │ │
│  │         │ │          │ │        │ │back  │ │Alerts    │ │
│  └────┬────┘ └────┬─────┘ └───┬────┘ └──┬───┘ └────┬─────┘ │
└───────┼───────────┼──────────┼─────────┼──────────┼────────┘
        │           │          │         │          │
   ┌────▼───────────▼──────────▼─────────▼──────────▼────────┐
   │  serve.py (HTTP :8080)                                   │
   │  - Serves dashboard HTML/JS/CSS                          │
   │  - REST API for scan/rollback/environments/exceptions    │
   │  - Spawns agent.py subprocesses                          │
   │  - Live log streaming via ring buffer + file tail        │
   └──────────┬────────────────────────┬─────────────────────┘
              │                        │
   ┌──────────▼──────────┐   ┌─────────▼─────────────────────┐
   │  agent.py           │   │  Supabase (PostgreSQL)        │
   │  - Terraform plan   │   │  ┌──────────────────────────┐ │
   │  - Drift formatting │   │  │ environments             │ │
   │  - LLM analysis     │   │  │ environment_secrets      │ │
   │  - Trivy security   │   │  │ scan_runs                │ │
   │  - PagerDuty alerts │   │  │ rollback_runs            │ │
   │  - Slack alerts     │   │  │ drift_events             │ │
   │  - GitHub PRs       │   │  │ drift_exception_registry │ │
   └─────────────────────┘   │  │ notification_secrets     │ │
                              │  │ severity_routing_rules   │ │
                              │  │ scope_config (legacy)    │ │
                              │  └──────────────────────────┘ │
                              └──────────────────────────────┘
```

The pipeline runs in two modes:
- **Dashboard-triggered** — serve.py spawns agent.py as a subprocess with `--run-id`
- **CI/CD** — GitHub Actions workflow runs agent.py on PR merge/close events

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend language | Python 3.x |
| LLM | Amazon Nova Pro via Bedrock (`us.amazon.nova-pro-v1:0`) |
| Agent framework | LangGraph (StateGraph) |
| Database | Supabase (PostgreSQL with PostgREST REST API) |
| Dashboard server | Python `http.server` (ThreadingHTTPServer) |
| Security scanner | Trivy (`trivy config`) |
| IaC tooling | Terraform CLI, hcledit |
| Notifications | PagerDuty Events API v2, Slack Incoming Webhooks |
| Version control | GitHub (PyGithub SDK) |
| CI/CD | GitHub Actions with OIDC |

---

## Database Schema

**Verified against live Supabase schema as of 2026-07-28.** The migration files in `migrations/` are a partial history — they show column additions but not the original `CREATE TABLE` statements for some tables. The schema below is the authoritative live schema.

All tables live in the `public` schema on Supabase. RLS is enabled on all tables; the `anon` role has SELECT-only policies where applicable. The `service_role` key bypasses RLS entirely.

### `environments`

Stores configuration for each AWS account/scope being monitored.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | auto-generated |
| `name` | text | Human label, e.g. "Production A" |
| `slug` | text UNIQUE | Machine key, e.g. "scope-a" |
| `aws_account_id` | text | 12-digit AWS account ID |
| `aws_profile` | text | Legacy: AWS named profile |
| `region` | text | e.g. "us-east-1" |
| `tf_state_bucket` | text | S3 bucket for Terraform state |
| `tf_lock_table` | text | DynamoDB lock table (default: "terraform-locks") |
| `tf_directory_path` | text | Repo-relative path to .tf files |
| `scan_role_variable` | text | GitHub Variable name for scan role ARN |
| `apply_role_secret_name` | text | GitHub Secret name for apply role ARN |
| `apply_environment_name` | text | GitHub Environment for approval gate |
| `is_active` | boolean | Soft-delete flag (default true) |
| `auth_type` | text | `'profile'`, `'role'`, or `'keys'`. CHECK: `auth_type = ANY (ARRAY['profile', 'role', 'keys'])` |
| `aws_role_arn` | text | IAM role ARN for OIDC assumption |
| `aws_external_id` | text | Optional external ID for role assumption |
| `repo_url` | text | Git clone URL for the terraform code |
| `repo_branch` | text | Branch to clone (default 'main') |
| `git_auth_type` | text | `'none'` or `'token'`. CHECK: `git_auth_type = ANY (ARRAY['none', 'token'])` |
| `clone_path` | text | Populated after first git clone |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

**RLS**: `anon` can SELECT where `is_active = true`.

### `environment_secrets`

Stores sensitive credentials per environment. **No anon access — service_role only.**

| Column | Type | Notes |
|--------|------|-------|
| `environment_id` | uuid UNIQUE NOT NULL | FK → `environments(id)`. One row per environment. |
| `aws_access_key_id` | text | For `auth_type = 'keys'` |
| `aws_secret_access_key` | text | For `auth_type = 'keys'` |
| `github_token` | text | For `git_auth_type = 'token'` |
| `updated_at` | timestamptz | Default `now()` |

**⚠️ Note**: The FK constraint does **not** have `ON DELETE CASCADE` in the live schema. If you delete an environment via the dashboard, the server does a soft-delete (`is_active = false`), so the secrets row is preserved. Hard-deleting an environment directly in Supabase would fail if a secrets row references it — delete the secrets row first.

### `scan_runs`

Tracks every pipeline invocation from the dashboard.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | auto-generated |
| `scope` | text | Environment slug |
| `unmanaged_flag` | boolean | Whether unmanaged scan was enabled |
| `status` | text | `'running'`, `'complete'`, `'failed'` |
| `current_stage` | text | Latest pipeline stage name |
| `started_at` | timestamptz | |
| `completed_at` | timestamptz | |
| `result_summary` | jsonb | Structured result: mode, drift/unmanaged blocks, alerts_sent, report_path |
| `pr_links` | jsonb | Array of PR URLs (default `[]`) |

**RLS**: `anon` can SELECT.

### `rollback_runs`

Tracks rollback preview and execute operations.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | auto-generated |
| `pr_number` | integer | PR to roll back |
| `scope` | text | Environment slug |
| `mode` | text | `'preview'` or `'execute'` |
| `status` | text | `'running'`, `'complete'`, `'failed'` |
| `current_stage` | text | Latest stage name |
| `started_at` | timestamptz | |
| `completed_at` | timestamptz | |
| `result` | jsonb | For preview: `{diff: [...]}`, for execute: `{pr_url: "..."}` |
| `rollback_pr_url` | text | The rollback PR URL (for execute mode) |

**RLS**: `anon` can SELECT.

### `drift_events`

The central event log. One row per drift finding, written by the pipeline whenever a PR is created.

**⚠️ IMPORTANT**: The `id` column is `bigint GENERATED ALWAYS AS IDENTITY` (an auto-incrementing integer), **not** a UUID. All frontend code must treat event IDs as numbers.

| Column | Type | Notes |
|--------|------|-------|
| `id` | bigint PK | Auto-incrementing identity (GENERATED ALWAYS AS IDENTITY) |
| `created_at` | timestamptz | Default `now()` |
| `account` | text NOT NULL | Environment slug |
| `region` | text | AWS region |
| `resource_id` | text NOT NULL | e.g. "aws_instance.web_server" |
| `severity` | text | Default `'LOW'`; values: `'HIGH'`, `'MEDIUM'`, `'LOW'` |
| `pr_number` | integer | GitHub PR number (nullable for suppressed/auto) |
| `pr_type` | text | Default `'fix'`; values: `'fix'`, `'batch'`, `'rollback'`, `'unmanaged'`, `'manual'` |
| `status` | text | Default `'open'`; values: `'open'`, `'resolved'`, `'suppressed'` |
| `resolution` | text | How the drift was resolved |
| `fields_changed` | jsonb | Default `'[]'`; array of field name strings |
| `drift_summary` | text | Human-readable summary |
| `changes_jsonb` | jsonb | `{field: {before, after}}` — the full change set |
| `file_path` | text | Repo-relative path to the affected .tf file (or "drift-reports/..." for unmanaged) |
| `unmanaged` | boolean | Default `false` |
| `cost_impact` | jsonb | `{hourly_usd, monthly_estimate_usd, accrued_usd, runtime_hours}` |
| `trivy_passed` | boolean | Whether the Trivy security scan passed |
| `trivy_summary` | jsonb | `{trivy_error, trivy_security_fixes, trivy_pre_existing_count, trivy_newly_introduced_count}` |
| `freshness_gate_status` | text | `'pass'` or `'fail'` (from rollback_check.py) |
| `freshness_gate_checked_at` | timestamptz | |
| `rolled_back_from_pr` | integer | Original PR number this rollback was generated from |
| `resolved_at` | timestamptz | When status was changed to `'resolved'` |

**RLS**: `anon` can SELECT.

### `drift_severity_summary` (VIEW)

Pre-aggregated view for dashboard cards.

```sql
SELECT account, severity, COUNT(*) as count
FROM drift_events WHERE status = 'open'
GROUP BY account, severity
```

**Columns**: `account`, `severity`, `count`.

### `drift_exception_registry`

Stores drift/unmanaged exception rules. Replaces the old file-based `drift-exceptions.json`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | |
| `scope` | text | Environment slug |
| `exception_type` | text | `'drift'` or `'unmanaged'` |
| `resource_address` | text | For drift: e.g. "aws_instance.example" |
| `drift_type` | text | For drift: field name or `'*'` |
| `resource_type` | text | For unmanaged: e.g. "aws_security_group" |
| `resource_id_pattern` | text | For unmanaged: substring match on name |
| `reason` | text | **Required.** Human-readable justification |
| `approved_by` | text | |
| `expires` | date | ISO date; null = permanent |
| `auto` | boolean | If true, skip human review for matching drift |
| `max_monthly_cost_usd` | numeric | For unmanaged: suppress only if below this cost |
| `active` | boolean | Soft-delete flag (default true) |
| `created_at` | timestamptz | |

**RLS**: `anon` can SELECT.

### `notification_secrets`

Singleton row (PK = 1) holding PagerDuty and Slack credentials. **No anon access.**

| Column | Type | Notes |
|--------|------|-------|
| `id` | integer PK | Always 1 (CHECK constraint) |
| `pagerduty_routing_key` | text | |
| `slack_webhook_url` | text | |
| `updated_at` | timestamptz | |

### `severity_routing_rules`

Maps severity levels to notification channels. Global rules have `scope = NULL`; scope-specific rules override.

| Column | Type | Notes |
|--------|------|-------|
| `id` | integer PK | auto-generated identity |
| `severity` | text | `'HIGH'`, `'MEDIUM'`, `'LOW'` |
| `channel` | text | `'pagerduty'`, `'slack'`, `'none'` |
| `scope` | text | NULL = global default |
| `updated_at` | timestamptz | |

**RLS**: `anon` can SELECT.

### `scope_config` (legacy)

Earlier configuration table, largely superseded by `environments`. Still present in the database but no longer actively used by serve.py.

| Column | Type | Notes |
|--------|------|-------|
| `scope` | text PK | e.g. "scope-a" |
| `region_variable` | text NOT NULL | GitHub Variable name for region |
| `scan_role_variable` | text NOT NULL | GitHub Variable name for scan role ARN |
| `apply_role_secret_name` | text NOT NULL | GitHub Secret name for apply role ARN |
| `apply_environment_name` | text NOT NULL | GitHub Environment name for apply gate |
| `tf_state_bucket` | text NOT NULL | S3 bucket for terraform state |
| `aws_account_id` | text | AWS account ID |
| `updated_at` | timestamptz | |

**RLS**: `anon` can SELECT.

### Supabase RPC Functions

The following PostgreSQL functions are callable by `anon` via `POST /rest/v1/rpc/{fn}`:

| Function | Params | Returns |
|----------|--------|---------|
| `get_most_drifted` | `p_account text, p_days int` | `TABLE(resource_id text, drift_count bigint)` — top 15 |
| `get_mttr_by_severity` | `p_account text, p_days int` | `TABLE(severity text, avg_hours numeric, count bigint)` |
| `get_drift_volume_daily` | `p_account text, p_days int` | `TABLE(day date, count bigint)` |

---

## API Reference

All endpoints are served by `dashboard/serve.py` on `http://localhost:8080`.

### Authentication

When `API_ACCESS_TOKEN` is set in `.env`, all `/api/*` endpoints require the header:
```
X-Api-Access-Token: <token>
```

The comparison uses `hmac.compare_digest` (constant-time). When `API_ACCESS_TOKEN` is empty/unset, auth is disabled (a warning is printed at startup).

**Frontend behavior**: `auth.js` prompts the user once for the token, stores it in `localStorage` under key `drift_api_token`, and exposes `window._authHeaders()` which returns `{}` or `{"X-Api-Access-Token": token}`.

### Static / Page Serving

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` or `/index.html` | Overview dashboard (injects `__SUPABASE_URL__` and `__SUPABASE_ANON_KEY__` into HTML) |
| GET | `/scan` or `/scan.html` | Scan trigger page |
| GET | `/pr-queue` or `/pr-queue.html` | PR queue page |
| GET | `/rollback` or `/rollback.html` | Rollback management page |
| GET | `/trends` or `/trends.html` | Trends dashboard page |
| GET | `/exceptions` or `/exceptions.html` | Exception registry page |
| GET | `/alerts` or `/alerts.html` | Alerts/notification settings page |
| GET | `/environments` or `/environments.html` | Environment configuration page |
| GET | `/explorer` or `/explorer.html` | Drift explorer page |
| GET | `*.js`, `*.css`, `*.png` | Static assets with `Cache-Control: public, max-age=86400` |

All HTML pages have `__SUPABASE_URL__` and `__SUPABASE_ANON_KEY__` replaced at serve time with values from `.env`.

---

### 1. GET `/api/environments`

Returns all environments (including inactive ones) with masked secrets.

**Auth**: API access token required.

**Response** (200):
```json
[
  {
    "id": "uuid",
    "name": "Production A",
    "slug": "scope-a",
    "aws_account_id": "605134452604",
    "aws_profile": "account-a",
    "region": "us-east-1",
    "tf_state_bucket": "scope-a-tf-state-...",
    "tf_lock_table": "terraform-locks",
    "tf_directory_path": "terraform_code/ec2_terraform_account_a",
    "scan_role_variable": "SCOPE_A_SCAN_ROLE_ARN",
    "apply_role_secret_name": "SCOPE_A_APPLY_ROLE_ARN",
    "apply_environment_name": "scope-a-apply",
    "is_active": true,
    "auth_type": "profile",
    "aws_role_arn": null,
    "aws_external_id": null,
    "repo_url": null,
    "repo_branch": "main",
    "git_auth_type": null,
    "clone_path": null,
    "github_token_configured": false,
    "github_token_masked": null,
    "aws_access_key_configured": false,
    "aws_access_key_masked": null,
    "aws_secret_key_configured": false,
    "aws_secret_key_masked": null,
    "created_at": "2025-...",
    "updated_at": "2025-..."
  }
]
```

**Errors**: 502 (Supabase unreachable), 401 (unauthorized).

---

### 2. POST `/api/environments`

Create a new environment.

**Auth**: API access token required.

**Request Body**:
```json
{
  "slug": "scope-c",                    // required, URL-safe: ^[a-z0-9][a-z0-9-]*$
  "name": "Production C",              // required
  "aws_account_id": "605134452604",     // required
  "region": "us-west-2",               // required
  "tf_state_bucket": "scope-c-tf-state",// required
  "tf_directory_path": "terraform_code/ec2_terraform_account_c", // required
  "aws_profile": "account-c",          // optional
  "tf_lock_table": "terraform-locks",  // optional
  "scan_role_variable": "...",         // optional
  "apply_role_secret_name": "...",     // optional
  "apply_environment_name": "...",     // optional
  "repo_url": "...",                   // optional
  "repo_branch": "main",               // optional
  "git_auth_type": "token",            // optional: 'none' or 'token'
  "auth_type": "keys",                 // optional: 'profile', 'role', 'keys'
  "aws_role_arn": "...",               // optional
  "aws_external_id": "...",            // optional
  "_github_token": "...",              // optional, stored in environment_secrets
  "_aws_access_key_id": "...",         // optional, stored in environment_secrets
  "_aws_secret_access_key": "..."      // optional, stored in environment_secrets
}
```

**Validation rules**:
- `slug` must match `^[a-z0-9][a-z0-9-]*$`
- All required fields must be non-empty strings
- If `auth_type` is `'keys'`, both `_aws_access_key_id` and `_aws_secret_access_key` must be provided
- Fields prefixed with `_` are stored in `environment_secrets` (never returned unmasked)

**Response** (201): The created environment row as JSON.

**Special behavior**: If slug already exists, attempts to reactivate a soft-deleted row (`is_active = false`). If no soft-deleted row exists, returns 409.

---

### 3. PATCH `/api/environments/{id}`

Update an environment. Accepts the same optional fields as POST.

**Auth**: API access token required.

**Allowed fields**: `name`, `aws_account_id`, `aws_profile`, `region`, `tf_state_bucket`, `tf_lock_table`, `tf_directory_path`, `scan_role_variable`, `apply_role_secret_name`, `apply_environment_name`, `is_active`, `repo_url`, `repo_branch`, `git_auth_type`, `auth_type`, `aws_role_arn`, `aws_external_id`

**Secret fields**: `_github_token`, `_aws_access_key_id`, `_aws_secret_access_key` are routed to `environment_secrets`.

**Validation**: If switching `auth_type` to `'keys'`, access keys must be provided in the request or already stored.

**Response** (200): Updated row or `{"status": "ok"}`.

---

### 4. DELETE `/api/environments/{id}`

Soft-deletes an environment (sets `is_active = false`).

**Auth**: API access token required.

**Response** (200): `{"status": "ok"}`

---

### 5. POST `/api/scan`

Trigger a drift scan for a scope. Spawns `agent.py` as a background subprocess.

**Auth**: API access token required.

**Request Body**:
```json
{
  "scope": "scope-a",
  "unmanaged_flag": false
}
```

**Validation**: `scope` must be a valid active environment slug.

**Concurrency**: Returns 409 if a scan is already running for this scope.

**Response** (202):
```json
{
  "run_id": "uuid-of-scan-run"
}
```

**What happens**: A `scan_runs` row is created with status `'running'`. The agent runs these stages sequentially:
1. `unmanaged_scan` (if `--scan-unmanaged` flag was set)
2. `reconcile_agent` — runs terraform plan, formats drift JSON, sends to LLM
3. `trivy_gate` — runs Trivy security scan on proposed fixes
4. `alert_agent` — routes findings to PagerDuty/Slack
5. `drift_pr` — creates GitHub PRs with code fixes

Each stage updates `scan_runs.current_stage`. On completion, `scan_runs` is updated with status `'complete'` or `'failed'` and a `result_summary` JSONB object:

```json
{
  "mode": "full",
  "report_path": "/path/to/report.md",
  "drift": {
    "found": true,
    "count": 3,
    "findings": [{"resource_id": "aws_instance.web", "risk_level": "LOW"}],
    "pr_links": ["https://github.com/.../pull/123"]
  },
  "unmanaged": {
    "found": false,
    "count": 0,
    "findings": [],
    "pr_links": []
  },
  "alerts_sent": {"pagerduty": 1, "slack": 2}
}
```

**Errors**: 400 (invalid scope), 409 (scan already running), 502 (Supabase unreachable).

---

### 6. GET `/api/scan/{run_id}/logs`

Stream live logs from a running scan. Supports polling with offset.

**Auth**: API access token required.

**Query Parameters**:
- `offset` (integer, default 0) — line number to start from

**Response** (200):
```json
{
  "lines": [
    {"n": 0, "ts": "2025-07-28T12:00:01.123Z", "text": "Step 1: Running 'terraform plan'..."},
    {"n": 1, "ts": "2025-07-28T12:00:05.456Z", "text": "Step 2: Exporting plan to JSON..."}
  ],
  "complete": false
}
```

**Polling pattern**: The frontend should poll this endpoint every 1-2 seconds, passing the last received `n` as offset. When `complete` is `true`, the scan has finished. Check `scan_runs` for the final result.

Logs come from a ring buffer (2000 lines max) in memory and mirror files at `/tmp/drift-logs/{run_id}.log`. Logs are cleaned up after 24 hours.

---

### 7. POST `/api/rollback/preview`

Dry-run a rollback: compare stored baselines against live AWS state without patching any files.

**Auth**: API access token required.

**Request Body**:
```json
{
  "pr_number": 123,
  "scope": "scope-a"
}
```

**Response** (202):
```json
{
  "run_id": "uuid-of-rollback-run"
}
```

**Result** (in `rollback_runs.result` when complete):
```json
{
  "diff": [
    {
      "resource_id": "aws_instance.web",
      "field": "instance_type",
      "original": "t3.micro",
      "fixed": "t3.small",
      "current_live": "t3.large"
    }
  ]
}
```

---

### 8. POST `/api/rollback/execute`

Execute a rollback: reverse the drift fix by creating a new PR that reverts the original changes.

**Auth**: API access token required.

**Request Body**:
```json
{
  "pr_number": 123,
  "scope": "scope-a"
}
```

**Concurrency**: Returns 409 if a rollback is already running for this PR.

**Response** (202):
```json
{
  "run_id": "uuid-of-rollback-run"
}
```

**What happens**:
1. Loads baselines from `drift_events` for the given PR number
2. For each resource, swaps before↔after to produce a reverse patch
3. Applies the reverse patch to the .tf file
4. Runs `terraform plan` for freshness validation
5. Creates a rollback PR with `is_rollback=True` and `rolled_back_from_pr` set
6. The result includes `rollback_pr_url` in the `rollback_runs` row

---

### 9. GET `/api/exceptions?scope={scope}`

Get all active drift and unmanaged exceptions for a scope.

**Auth**: API access token required.

**Response** (200):
```json
{
  "drift_exceptions": [
    {
      "id": "uuid",
      "scope": "scope-a",
      "exception_type": "drift",
      "resource_address": "aws_instance.web",
      "drift_type": "instance_type",
      "reason": "Testing instance type changes are expected",
      "approved_by": "digambar",
      "expires": "2026-06-01",
      "auto": false,
      "active": true,
      "created_at": "2025-..."
    }
  ],
  "unmanaged_exceptions": [
    {
      "id": "uuid",
      "scope": "scope-a",
      "exception_type": "unmanaged",
      "resource_type": "aws_security_group",
      "resource_id_pattern": "launch-wizard",
      "reason": "Launch wizard SGs are known and accepted",
      "approved_by": "digambar",
      "max_monthly_cost_usd": null,
      "active": true,
      "created_at": "2025-..."
    }
  ]
}
```

**Errors**: 400 (invalid scope).

---

### 10. POST `/api/exceptions`

Add, expire, or delete an exception entry.

**Auth**: API access token required.

**Request Body** (add drift exception):
```json
{
  "scope": "scope-a",
  "exception_type": "drift",
  "action": "add",
  "entry": {
    "resource_address": "aws_instance.web",
    "drift_type": "*",
    "reason": "Expected drift during testing",
    "approved_by": "digambar",
    "expires": "2026-06-01",
    "auto": false
  }
}
```

**Request Body** (add unmanaged exception):
```json
{
  "scope": "scope-a",
  "exception_type": "unmanaged",
  "action": "add",
  "entry": {
    "resource_type": "aws_security_group",
    "resource_id_pattern": "launch-wizard",
    "reason": "Known launch-wizard SGs",
    "approved_by": "digambar",
    "max_monthly_cost_usd": 50.00
  }
}
```

**Request Body** (expire):
```json
{
  "scope": "scope-a",
  "exception_type": "drift",
  "action": "expire",
  "entry": {
    "resource_address": "aws_instance.web",
    "expires": "2025-01-01"
  }
}
```

**Request Body** (delete):
```json
{
  "scope": "scope-a",
  "exception_type": "drift",
  "action": "delete",
  "entry": {
    "resource_address": "aws_instance.web"
  }
}
```

**Validation**:
- `exception_type` must be `'drift'` or `'unmanaged'`
- `action` must be `'add'`, `'expire'`, or `'delete'`
- For drift add: `resource_address` and `reason` are required; `expires` must be a future ISO date
- For unmanaged add: `resource_type`, `resource_id_pattern`, and `reason` are required

**Response** (200): `{"id": "uuid"}` for add; `{"status": "ok"}` for expire/delete.

---

### 11. GET `/api/notification-settings`

Returns the current PagerDuty and Slack configuration status.

**Auth**: API access token required.

**Response** (200):
```json
{
  "pagerduty_configured": true,
  "pagerduty_masked": "••••key1",
  "slack_configured": true,
  "slack_masked": "••••ooks"
}
```

Secrets are masked: only the last 4 characters are shown, prefixed with `••••`.

---

### 12. POST `/api/notification-settings`

Update a notification secret.

**Auth**: API access token required.

**Request Body**:
```json
{
  "field": "pagerduty_routing_key",
  "value": "new-routing-key-value"
}
```

`field` must be `'pagerduty_routing_key'` or `'slack_webhook_url'`.

**Response** (200):
```json
{
  "success": true,
  "pagerduty_routing_key_configured": true
}
```

---

### 13. POST `/api/notification-settings/test`

Send a test notification.

**Auth**: API access token required.

**Request Body**:
```json
{
  "channel": "pagerduty",
  "scope": "scope-a"
}
```

`channel` must be `'pagerduty'` or `'slack'`. `scope` is optional.

**Response** (200): `{"success": true}`

**Error** (500): `{"success": false, "error": "..."}`

---

### 14. POST `/api/routing-rules`

Upsert a severity routing rule. If a rule for that severity+scope already exists, it's updated; otherwise a new row is inserted.

**Auth**: API access token required.

**Request Body**:
```json
{
  "severity": "HIGH",
  "channel": "pagerduty",
  "scope": "scope-a"
}
```

`scope` can be omitted (or set to `null`) for a global default. Omitting scope = global rule; providing scope = scope-specific override.

**Response** (200): `{"success": true}`

---

### Error Response Format

All API errors follow this format:
```json
{
  "error": "Human-readable error message",
  "run_id": "optional-existing-run-id"
}
```

HTTP status codes used: 400 (bad request), 401 (unauthorized), 404 (not found), 409 (conflict — scan/rollback already running), 500 (internal error), 502 (Supabase unreachable).

---

## Supabase Direct Queries (Anon Key)

The dashboard frontend can query Supabase directly using the anon key (injected into HTML as `__SUPABASE_ANON_KEY__`). This offloads read-heavy queries from serve.py.

### Tables accessible via anon key

All tables have `anon` SELECT policies except:
- `environment_secrets` — NO anon access
- `notification_secrets` — NO anon access

### Common query patterns (from existing dashboard)

**Drift severity summary** (overview cards):
```js
supabase.from("drift_severity_summary").select("severity, count").eq("account", scope)
```

**Open rollback count**:
```js
supabase.from("drift_events").select("*", { count: "exact", head: true })
  .eq("status", "open").eq("pr_type", "rollback").eq("account", scope)
```

**Last scan timestamp**:
```js
supabase.from("drift_events").select("created_at").eq("account", scope)
  .order("created_at", { ascending: false }).limit(1)
```

**Cost impact sum**:
```js
supabase.from("drift_events").select("cost_impact")
  .eq("status", "open").eq("account", scope)
```
(Client-side sum of `cost_impact.monthly_estimate_usd`)

**Scan runs** (for scan history):
```js
supabase.from("scan_runs").select("*").eq("scope", scope)
  .order("started_at", { ascending: false }).limit(20)
```

**Rollback runs**:
```js
supabase.from("rollback_runs").select("*").eq("scope", scope)
  .order("started_at", { ascending: false })
```

**RPC calls** (trends page):
```js
supabase.rpc("get_most_drifted", { p_account: scope, p_days: days })
supabase.rpc("get_mttr_by_severity", { p_account: scope, p_days: days })
supabase.rpc("get_drift_volume_daily", { p_account: scope, p_days: days })
```

**PR queue / explorer queries** (from `drift_events`):
```js
supabase.from("drift_events").select("*", { count: "exact" })
  .eq("account", scope)
  // optional filters:
  .eq("status", "open")       // open vs resolved
  .eq("severity", "HIGH")     // severity filter
  .eq("pr_type", "fix")       // type filter
  .order("created_at", { ascending: false })
  .range(offset, offset + pageSize - 1)
```

---

## Supabase Realtime (WebSocket)

The dashboard uses Supabase Realtime for live updates on the overview page. The channel subscribes to INSERT and UPDATE events on `drift_events`:

```js
supabase.channel("drift_events_changes")
  .on("postgres_changes",
    { event: "INSERT", schema: "public", table: "drift_events", filter: `account=eq.${scope}` },
    () => refreshAll(scope)
  )
  .on("postgres_changes",
    { event: "UPDATE", schema: "public", table: "drift_events", filter: `account=eq.${scope}` },
    () => refreshAll(scope)
  )
  .subscribe()
```

**Fallback**: The dashboard also runs a 60-second polling interval as a fallback if the WebSocket disconnects.

---

## Feature Modules — Deep Dive

### 1. Drift Detection & Reconciliation (agent.py)

**What it does**: Runs `terraform plan`, parses the plan JSON for configuration drift, sends findings to Amazon Nova Pro for analysis, runs Trivy security scan on proposed fixes, then creates GitHub PRs with code patches.

**Pipeline stages** (LangGraph StateGraph):

```
START → [unmanaged_scan] → reconcile_agent → trivy_gate → alert_agent → END
                                                    ↘ drift_pr → END
```

- **unmanaged_scan** (optional): Enumerates live AWS resources and subtracts those tracked in Terraform state
- **reconcile_agent**: Runs terraform plan + formatting script, LLM analysis
- **trivy_gate**: Security scan → fix → scan loop (max 3 iterations) against proposed code changes
- **alert_agent**: Routes HIGH→PagerDuty, MEDIUM/LOW→Slack based on routing rules
- **drift_pr**: Groups findings by file and creates GitHub PRs

**Drift suppression hierarchy** (applied during formatting):
1. `auto_suppressed` — ASG-managed tags, AWS-managed attributes (silent)
2. `externally_managed` — Fields covered by `lifecycle.ignore_changes` (warn, no PR)
3. `drift_exception_registry` entries — Human-approved suppressions (warn, no PR)
4. Expired exceptions — Warned about but NOT applied as suppression

**Finding statuses**:
- `null` — Active drift with changes
- `"deleted_externally"` — Resource in state but not in AWS (needs removal from .tf)
- `"externally_managed"` — Drift on `lifecycle.ignore_changes` fields
- `"auto_suppressed"` — Expected drift (ASG, AWS-managed tags)
- `"unmanaged"` — Resource in AWS but not in any Terraform state
- `"unmanaged_tagged"` — Resource tagged `ManagedBy=Terraform` but not in this workspace

**Cost estimation**: Unmanaged resources get cost estimates from `cost_cache.json`. The `cost_impact` field includes `hourly_usd`, `monthly_estimate_usd`, `accrued_usd`, and `runtime_hours`.

### 2. GitHub PR Creation (github_integration.py)

**What it does**: Creates GitHub branches and PRs with patched .tf files.

**PR types**:
- `fix` — Single-resource drift fix (code patch)
- `batch` — Multi-resource drift fix (multiple changes to same .tf file in one PR)
- `rollback` — Reverse a previous drift fix
- `unmanaged` — Report-only PR for unmanaged resources (no code patch, markdown report)
- `report-only` — Drift findings without a file_path (markdown report, no code patch)

**Branch naming**: `drift-fix/{scope}/{resource-id}-{unix-timestamp}`

**PR labels**: `drift-reconciler`, `risk:{low|medium|high}`

**Superseded PR closure**: Before creating a new PR for a resource, any open PRs for the same resource are auto-closed with a "Superseded" comment.

**PR body format**:
```markdown
## Drift detected: `resource_id`
**Risk level:** HIGH

### Summary
...

### Terraform Plan
...

_Opened automatically by AWS Terraform Drift Reconciler. Do not merge without review._
```

**File patching**: Uses `hcledit` when available (preferred), falls back to regex-based patching. Unpatchable fields (security group ingress/egress) are skipped with a warning.

**PR creation modes**:
- `code_to_reality` — Patch the .tf file to match live AWS state
- Report-only — Create a markdown report file under `drift-reports/`

### 3. Security Scanning (trivy_agent.py)

**What it does**: Runs Trivy (`trivy config --format json`) against the proposed drift-fix HCL code before creating a PR. Loops up to 3 iterations: scan → fix → scan.

**Finding classification**:
- **pre-existing** — Was in the baseline scan (before drift fix was applied); not auto-fixed
- **newly-introduced** — Caused by the drift fix; auto-fixed if possible
- **needs_review** — Requires a human decision (CIDR ranges, KMS ARNs, IAM roles)

**LLM-driven fixing**: The LLM rewrites individual Terraform resource blocks to resolve security findings. Two deterministic gates prevent bad fixes:
1. Resource count/address check — the edit must not create/drop resources
2. Provider schema check — all attribute names must exist in the real AWS provider schema

**Inline suppression**: `# trivy:ignore:AVD-XXX` comments above resource blocks suppress known/accepted findings.

**Data written to drift_events**: `trivy_passed`, `trivy_summary` (with `trivy_error`, `trivy_security_fixes`, `trivy_pre_existing_count`, `trivy_newly_introduced_count`).

### 4. Unmanaged Resource Scanner (unmanaged_scanner.py)

**What it does**: Enumerates live AWS resources and compares against Terraform state to find resources not managed by IaC.

**Scanned resource types**:
- `aws_instance` (EC2)
- `aws_security_group`
- `aws_vpc`
- `aws_subnet`
- `aws_route_table`
- `aws_internet_gateway`
- `aws_nat_gateway` (with cost estimation)
- `aws_s3_bucket`
- `aws_dynamodb_table`

**Classification**:
- Default VPC resources (named "default") are always skipped
- Resources tagged `ManagedBy=Terraform` but not in this workspace → `unmanaged_tagged` (LOW severity)
- Resources with no Terraform tag → `unmanaged` (MEDIUM severity)
- Resources matching an unmanaged exception → suppressed (unless cost exceeds `max_monthly_cost_usd`)

### 5. Alerting

**PagerDuty** (pagerduty_alert.py):
- Uses Events API v2 (`https://events.pagerduty.com/v2/enqueue`)
- One alert per HIGH-severity finding
- Dedup key: `{scope}-drift-{resource_id}` (prevents duplicate alerts for the same resource)
- Summary format: `[{scope}] Drift detected: {resource_id} (${cost}/mo)`

**Slack** (slack_notify.py):
- Uses incoming webhook with Block Kit format
- Batched: max 5 findings per message card
- Header: `:red_circle: {N} drift finding(s) — {scope} ({region})`
- Each finding line: `• \`resource_id\` [SEVERITY] — summary  <PR_URL|PR>`
- Distinguishes unmanaged vs. drift findings in the header text

**Routing rules**: Loaded from `severity_routing_rules` table. Scope-specific rules override global defaults. Falls back to hardcoded: HIGH→PagerDuty, MEDIUM/LOW→Slack.

### 6. Rollback System

**Checkpoint 1** (rollback preview, in serve.py / agent.py):
1. Load baselines from `drift_events` for the PR
2. For each resource, swap before↔after to reverse the change
3. Apply the reverse patch to the .tf file
4. Run `terraform plan` to extract live values
5. Compare live values against the expected rollback target
6. Report which fields are fresh, stale, or not found

**Checkpoint 2** (rollback_check.py, runs in CI):
1. Read stored baselines
2. Extract live values from the plan JSON at apply time
3. Compare: if any field is stale (intervening change), abort with exit code 1
4. Fire PagerDuty alert on staleness

**Rollback PR creation**: Creates a new PR with `[ROLLBACK]` prefix, `is_rollback=True`, and `rolled_back_from_pr` set to the original PR number.

### 7. Drift History & Trends

**drift_history.py**: Writes to `drift_events` table. Key operations:
- `append_entry()` — Insert a new event (called during PR creation)
- `resolve_entry()` — Mark as resolved (called by CI after PR merge/close)
- `load_baselines()` — Read baselines for rollback
- `has_unresolved_drift()` — Check if scope has open drift (for pre-apply gate)

**drift_trends.py**: Generates markdown reports from Supabase. Uses RPC functions for server-side aggregation. Report sections:
- Most Drifted Resources (top 15)
- Mean Time to Remediate by severity
- Drift Volume Over Time (daily)
- Rollback count
- Unresolved findings
- Summary (total, unique, resolved, unresolved)

### 8. Exception Registry

Managed entirely through the dashboard (no more file-based drift-exceptions.json or GitHub PR workflow).

**Drift exceptions**: Suppress drift on a specific resource address + field. Can be auto-approved (`auto: true`) or manual. Can have an expiration date.

**Unmanaged exceptions**: Suppress unmanaged resource findings by type + name pattern. Can have a cost cap (`max_monthly_cost_usd`) — resources above the cap still alert.

**Operations**: Add, expire (set an earlier expiration date), delete (set `active = false`). All are soft operations; nothing is hard-deleted.

### 9. Environment Management

**Credential resolution** (environment_credentials.py), in priority order:
1. `auth_type = 'role'` → STS AssumeRole with the stored ARN (+ optional external ID)
2. `auth_type = 'keys'` → Static access key from `environment_secrets`
3. No auth_type (legacy) → boto3 named profile from `aws_profile` column

**Git clone** (environment_credentials.py):
- If `repo_url` is set, the repo is cloned/refreshed under `DRIFT_CLONE_BASE` (default `~/.drift-clones/{slug}`)
- First run: `git clone --branch {branch} {url} {path}`
- Subsequent runs: `git fetch origin {branch}` + `git reset --hard origin/{branch}`
- If `git_auth_type = 'token'`, the GitHub token from `environment_secrets` is injected into the clone URL

### 10. Pre-apply Gate (pre_apply_check.py)

Checks Supabase for unresolved drift before allowing `terraform apply` in CI:
- Exit 0: No unresolved drift (or warn mode) → proceed
- Exit 1: Unresolved drift found and `--block` flag set → abort

### 11. Workflow Notification (workflow_notify.py)

Posts workflow outcomes to Slack from CI. Outcomes:
- `accepted` → ✅ Drift fix applied
- `rejected` → ↩️ Drift reverted
- `rollback_blocked` → ❌ Intervening change detected
- `failed` → ❌ Check logs

---

## LangGraph Pipeline (Detailed State Shape)

The pipeline state (`State` TypedDict) flows through all nodes:

```python
{
  "messages": [],            # Accumulated LangChain messages
  "drift_detected": bool,    # True if any drift was found
  "drift_findings": [        # One per finding
    {
      "resource_id": "aws_instance.web",
      "risk_level": "LOW",
      "drift_summary": "...",
      "plan_output": "{...}",
      "file_path": "/path/to/main.tf",
      "changes": {"instance_type": {"before": "t3.micro", "after": "t3.small"}},
      "status": null,         # null, "deleted_externally", "externally_managed", "unmanaged", "unmanaged_tagged"
      "cost_impact": {...},   # Optional, from unmanaged scanner
      "trivy_passed": true,   # Added by trivy_gate
      "trivy_error": false,
      "trivy_pre_existing_count": 0,
      "trivy_newly_introduced_count": 0,
      "trivy_security_fixes": 2
    }
  ],
  "trivy_scanned": bool,
  "scan_unmanaged": bool,
  "run_id": "uuid",
  "terraform_failed": bool
}
```

**Node outputs**:
- `unmanaged_scan` → adds to `drift_findings`, sets `drift_detected = True`
- `reconcile_agent` → sets `drift_detected`, populates `drift_findings` from LLM analysis
- `trivy_gate` → enriches findings with `trivy_*` fields
- `alert_agent` → returns `alerts_sent: {pagerduty: N, slack: N}`
- `drift_pr` → returns `pr_urls: [{url, type: "drift"|"unmanaged"}]`

---

## Third-Party Integrations

### 1. Supabase (PostgreSQL + REST API)

**Connection**: `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` (for writes) or `SUPABASE_ANON_KEY` (for reads from browser).

**Key tables**: 9 tables + 1 view + 3 RPC functions (detailed above).

**Direct access pattern**: The dashboard uses the Supabase JS client (`@supabase/supabase-js`) loaded from CDN. The anon key is injected into HTML at serve time.

### 2. GitHub (PyGithub SDK)

**Connection**: `GITHUB_TOKEN` + `GITHUB_REPO` (format: `owner/repo`).

**Operations**: Create branches, create/update files, create PRs, add labels, close superseded PRs, comment on PRs.

**Base branch**: Configurable via `GITHUB_BASE_BRANCH` env var (default: `main`).

### 3. Amazon Bedrock (Nova Pro)

**Model**: `us.amazon.nova-pro-v1:0`

**Region**: Configurable via `--region` CLI arg or `AWS_REGION` env var.

**Temperature**: 0.1 (deterministic output for code generation).

**Used by**: Main drift reconciler (agent_node) and Trivy fix agent (trivy_agent.py).

### 4. AWS APIs (boto3)

**Used for**:
- STS AssumeRole (for `auth_type = 'role'`)
- EC2 describe* APIs (unmanaged scanner)
- S3 list buckets / get bucket location
- DynamoDB list/describe tables

### 5. Terraform CLI

**Commands used**:
- `terraform init`
- `terraform plan -no-color -out=tfplan`
- `terraform show -no-color -json tfplan`
- `terraform show -no-color -json` (for current state)
- `terraform apply -auto-approve tfplan`
- `terraform providers schema -json` (for provider schema validation)

### 6. Trivy

**Command**: `trivy config --format json {tf_dir}`

**Used for**: Security scanning of Terraform HCL code before PR creation.

### 7. hcledit

**Command**: `hcledit attribute set` / `hcledit block rm`

**Used for**: Reliable HCL file patching (fallback: regex).

---

## GitHub Actions Workflows

### `drift-reconciler.yml`

Triggers on:
- PR closed (to main) with "Drift fix" in title
- `workflow_dispatch` (manual trigger)

**Jobs**:
1. `resolve-scope` — Determines scope from branch name or PR title
2. `reconcile-infra` — The main reconciliation job:
   - **ACCEPT** (merged PR): Runs pre-apply drift gate, rollback freshness check, then `terraform apply`
   - **REJECT** (closed PR): Runs pre-apply drift gate, then `terraform apply` to revert
   - Both paths: marks drift history resolved, comments on PR, sends Slack notification

**Required secrets**: `SCOPE_A_APPLY_ROLE_ARN`, `SCOPE_B_APPLY_ROLE_ARN`, `PAGERDUTY_ROUTING_KEY`, `SLACK_WEBHOOK_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`

**Required variables**: `PROD_A_REGION`, `PROD_B_REGION`, `DRIFT_GATE_MODE` (optional, `"block"` or empty for warn-only)

### `drift-preview.yml` (exists but not analyzed in detail)

Likely handles PR preview/draft scanning on PR open.

---

## Environment Variables

### Required for serve.py and agent.py

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (bypasses RLS) |
| `SUPABASE_ANON_KEY` | Anon key (for browser-side Supabase client) |
| `GITHUB_TOKEN` | GitHub PAT for PR creation |
| `GITHUB_REPO` | e.g. `digambarrajaram/AWS-Terraform-Drift-Reconciler` |
| `AWS_REGION` | Default AWS region |

### Optional

| Variable | Default | Purpose |
|----------|---------|---------|
| `API_ACCESS_TOKEN` | (empty = auth disabled) | Dashboard API auth |
| `GITHUB_BASE_BRANCH` | `main` | Base branch for PRs |
| `PAGERDUTY_ROUTING_KEY` | (empty) | Fallback if not in Supabase |
| `SLACK_WEBHOOK_URL` | (empty) | Fallback if not in Supabase |
| `DRIFT_CLONE_BASE` | `~/.drift-clones` | Git clone cache directory |
| `DEBUG_SCAN_RUNS` | (unset) | Verbose scan_run logging |
| `ACCOUNT_LABEL` | `default` | Default scope when running agent.py directly |
| `AGENT_MODE` | `deterministic` | `"deterministic"` or `"nova"` |

---

## Frontend Implementation Guide

### Pages/Screens to Build

| Page | Route | Purpose |
|------|-------|---------|
| **Overview / Dashboard** | `/` | KPI cards + live drift summary |
| **Scan** | `/scan` | Trigger scans, view live logs, scan history |
| **PR Queue** | `/pr-queue` | Browse/search/filter all drift PRs |
| **Rollback** | `/rollback` | Preview and execute rollbacks |
| **Trends** | `/trends` | Charts and stats for drift over time |
| **Exceptions** | `/exceptions` | Manage drift/unmanaged exception entries |
| **Alerts** | `/alerts` | Configure PagerDuty/Slack + routing rules |
| **Environments** | `/environments` | CRUD for environment configurations |
| **Explorer** | `/explorer` | Rich browse/search of drift findings |

### Environment / Scope Selector

Every page needs a scope selector (already implemented in `env-selector.js` as a shared module). The selector:
- Fetches active environments from `GET /api/environments`
- Renders as tabs or a dropdown
- Emits a callback when the scope changes
- Persists the selected scope in URL query params (`?scope=scope-a`)

### State Management Approach

There's no global state manager — each page fetches its own data. Scope selection is the only shared state, propagated via:
1. URL query parameter (`?scope=scope-a`)
2. `window.EnvSelector` global (from `env-selector.js`)

The new frontend should maintain this pattern or use a lightweight store.

### Data Flow Per Page

#### Overview Dashboard
- **Supabase direct**: `drift_severity_summary` view (open drift counts by severity)
- **Supabase direct**: `drift_events` (rollback count, last scan time, cost impact sum)
- **Supabase Realtime**: Subscribe to `drift_events` INSERT/UPDATE for live updates
- **Fallback**: Poll every 60 seconds

#### Scan Page
- **API**: `POST /api/scan` to trigger a scan
- **API**: `GET /api/scan/{run_id}/logs?offset=N` — poll every 1-2 seconds during scan
- **Supabase direct**: `scan_runs` table for scan history (filtered by scope, ordered by started_at desc)
- **UI states**: idle, running (with live log stream), complete (with result summary), failed (with error)

#### PR Queue
- **Supabase direct**: `drift_events` with filters (status, severity, pr_type, date range)
- **Pagination**: Client-side or server-side using `.range(offset, offset + limit)`
- **Filters**: status (open/resolved), severity (HIGH/MEDIUM/LOW), type (fix/rollback/unmanaged/batch)
- **Search**: resource_id substring match (or use Supabase `ilike`)
- **Sorting**: by created_at, severity, resource_id

#### Rollback Page
- **Supabase direct**: `drift_events` where `status = 'open'` — these are the eligible PRs
- **API**: `POST /api/rollback/preview` to dry-run
- **API**: `POST /api/rollback/execute` to execute
- **API**: `GET /api/scan/{run_id}/logs` for live preview/execute logs
- **Supabase direct**: `rollback_runs` for rollback history
- **Results display**: Show the diff from preview (field-by-field comparison)

#### Trends Page
- **Supabase RPC**: `get_most_drifted`, `get_mttr_by_severity`, `get_drift_volume_daily`
- **Supabase direct**: `drift_events` for rollback count, unresolved count, summary stats
- **Charts needed**:
  - Bar chart: Most Drifted Resources
  - Bar chart: MTTR by Severity
  - Line/area chart: Drift Volume Over Time
  - Stat tiles: Total Drifts, Unique Resources, Resolved, Unresolved, Rollbacks

#### Exceptions Page
- **API**: `GET /api/exceptions?scope=X` — returns drift and unmanaged exceptions
- **API**: `POST /api/exceptions` — add/expire/delete
- **Two tabs**: Drift exceptions + Unmanaged exceptions
- **Forms**:
  - Drift: resource_address, drift_type, reason, approved_by, expires, auto
  - Unmanaged: resource_type, resource_id_pattern, reason, approved_by, max_monthly_cost_usd
- **Validation**: Client-side validation matching the server rules

#### Alerts Page
- **API**: `GET /api/notification-settings` — PagerDuty/Slack config status
- **API**: `POST /api/notification-settings` — update keys
- **API**: `POST /api/notification-settings/test` — send test notification
- **API**: `POST /api/routing-rules` — upsert rules
- **Supabase direct**: `severity_routing_rules` — read current rules
- **Form**: PagerDuty routing key + Slack webhook URL (masked display)
- **Routing rules**: For each severity (HIGH/MEDIUM/LOW), select channel (PagerDuty/Slack/None), optional scope override

#### Environments Page
- **API**: `GET /api/environments` — list all environments
- **API**: `POST /api/environments` — create
- **API**: `PATCH /api/environments/{id}` — update
- **API**: `DELETE /api/environments/{id}` — soft-delete
- **Form fields**: All columns from the environments table
- **Secret fields**: github_token, aws_access_key_id, aws_secret_access_key (shown as masked, with separate input for new values)
- **auth_type conditional fields**: Show role ARN for 'role', access keys for 'keys', profile for legacy

#### Explorer Page
- **Supabase direct**: `drift_events` with rich filtering
- **Filters**: scope, status, severity, pr_type, date range, resource_id search
- **Display**: Table or card list with expandable details (changes_jsonb, drift_summary, cost_impact)
- **Pagination**: Infinite scroll or page-based

### Authentication Integration

The frontend must:
1. Include `auth.js` (or equivalent)
2. Call `window._authHeaders()` before every `/api/*` fetch
3. Merge the returned headers: `Object.assign({}, _authHeaders(), { "Content-Type": "application/json" })`
4. The `auth.js` script handles the one-time prompt + localStorage persistence
5. Supabase direct queries use the anon key (via `window.supabase.createClient(url, anonKey)`) — no auth header needed for reads

### Error Handling Patterns

1. **API errors**: Check `response.ok`, parse JSON error body, show user-friendly message from `json.error`
2. **409 Conflict**: Indicates a scan/rollback is already running — show the existing run ID with a link
3. **502 Bad Gateway**: Supabase is unreachable — show a "database unreachable" message with retry
4. **Supabase query errors**: Check `error` in `{ data, error }` response, show message
5. **401 Unauthorized**: Token is missing/invalid — re-prompt (clear localStorage `drift_api_token`)

### Realtime & Polling

- **Overview page**: Supabase Realtime (WebSocket) + 60s polling fallback
- **Scan logs**: HTTP polling at 1-2s intervals (GET with offset parameter)
- **Rollback logs**: Same pattern as scan logs
- Other pages: Load on navigation, no realtime needed

### Edge Cases to Handle

1. **No environments configured**: Show empty state with link to Environments page
2. **No scans yet**: Show "No scans yet" empty state on Overview and Scan pages
3. **Scan already running**: Show 409 error with link to view the running scan's logs
4. **Scope with no drift events**: Empty states on Explorer, PR Queue, Trends
5. **Auth token configured but not provided**: 401 errors with clear "API token required" message
6. **Long-running scan**: Log stream should show incremental progress; handle disconnection gracefully
7. **Rollback with stale resources**: Show the diff with clear "stale" indicators
8. **Exception with past expiration date**: Validation error at submit time
9. **Supabase unreachable during page load**: Show error state, not blank page
10. **Environment soft-deleted but still referenced**: It won't appear in scope selectors (filtered by `is_active = true`)

### Design Considerations

The current dashboard uses vanilla HTML/CSS/JS with the Supabase CDN client. For the rebuilt frontend:
- **Responsive**: All pages must work on desktop and tablet; mobile is secondary
- **Theme**: Support light and dark mode (the current CSS has no dark mode)
- **Consistent layout**: Sidebar or top nav for page navigation, scope selector in header
- **Loading states**: Skeleton screens for cards/tables, not spinners everywhere
- **Toast notifications**: For success/error feedback on mutations (scan triggered, exception added, etc.)
- **Confirmation dialogs**: For destructive actions (delete environment, execute rollback)
- **Form validation**: Inline validation errors, not just alert() dialogs

---

## Summary of All API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` (and page routes) | No | Serve injected HTML pages |
| GET | `/api/environments` | Token | List all environments with masked secrets |
| POST | `/api/environments` | Token | Create environment |
| PATCH | `/api/environments/{id}` | Token | Update environment |
| DELETE | `/api/environments/{id}` | Token | Soft-delete environment |
| POST | `/api/scan` | Token | Trigger drift scan |
| GET | `/api/scan/{run_id}/logs` | Token | Stream scan logs |
| POST | `/api/rollback/preview` | Token | Dry-run rollback |
| POST | `/api/rollback/execute` | Token | Execute rollback |
| GET | `/api/exceptions?scope=X` | Token | List exceptions for scope |
| POST | `/api/exceptions` | Token | Add/expire/delete exception |
| GET | `/api/notification-settings` | Token | Get PagerDuty/Slack status |
| POST | `/api/notification-settings` | Token | Update PagerDuty/Slack keys |
| POST | `/api/notification-settings/test` | Token | Send test notification |
| POST | `/api/routing-rules` | Token | Upsert severity routing rule |

## Summary of All Supabase Direct Access (Anon)

| Table/View/Function | Access | Primary Use |
|---------------------|--------|-------------|
| `environments` | SELECT (active only) | Environment/scope listing |
| `scan_runs` | SELECT | Scan history, live status |
| `rollback_runs` | SELECT | Rollback history, live status |
| `drift_events` | SELECT | PR queue, explorer, overview, rollback eligibility |
| `drift_severity_summary` | SELECT | Overview KPI cards |
| `drift_exception_registry` | SELECT | Exceptions listing |
| `severity_routing_rules` | SELECT | Alerts page routing display |
| `get_most_drifted` RPC | Execute | Trends chart |
| `get_mttr_by_severity` RPC | Execute | Trends MTTR chart |
| `get_drift_volume_daily` RPC | Execute | Trends volume chart |
| `environment_secrets` | **No access** | — |
| `notification_secrets` | **No access** | — |
