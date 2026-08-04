# AWS Terraform Drift Reconciler

An automated drift-detection pipeline that compares Terraform desired state against live AWS resources, classifies drift, proposes HCL fixes via an LLM agent, and opens GitHub pull requests for review. Supports multi-account/multi-region deployment, security scanning, cost estimation, unmanaged-resource detection, rollback, Slack/PagerDuty alerting, and historical trend reporting.

## Architecture

<p align="center">
  <img src="agent_pipeline_flow.png" alt="Agent Pipeline Flow" width="720">
</p>

<p align="center">
  <img src="cicd_accept_reject_flow.png" alt="CI/CD Accept/Reject Flow" width="800">
</p>

**Pipeline** (`drift_reconciler/agent.py`) — 5 LangGraph nodes in sequence
- `unmanaged_scan` (optional, `--scan-unmanaged`) — boto3 AWS enumeration
- `reconcile_agent` — Nova Pro classifies drift, proposes HCL fix
- `trivy_gate` — baseline scan → patch → scan → fix loop with pre-existing classification
- `drift_alert` — severity-routed: PagerDuty or Slack via configurable rules
- `drift_pr` — GitHub PR (fix/batch/rollback) + Supabase history append

**CLI** — `--rollback`, `--rollback-pr`, `--scan-unmanaged`, `--trends`, `--tf-dir`, `--account-label`, `--region`

**CI/CD** (`.github/workflows/`) — OIDC auth, scope-resolved, dual-gate (drift + freshness), auto-revert on block

---

## Features

### Core drift detection

| Feature | Status |
|---|---|
| Drift detection via `terraform plan -json` | ✅ |
| Multi-account / multi-region matrix | ✅ |
| GitHub OIDC-based AWS auth (scan role + apply role) | ✅ |
| PR creation with patched `.tf` file | ✅ |
| Unmanaged-resource PR type (`pr_type="unmanaged"`) tracked separately | ✅ |
| PR queue with type filter (fix/batch/rollback/unmanaged) + colored badges | ✅ |
| Scope-tagged PR branches, titles, and dedup keys | ✅ |
| `lifecycle.ignore_changes` / externally-managed resource handling | ✅ |
| Drift exceptions stored in Supabase (no more local JSON files) | ✅ |

### LLM agent

| Feature | Status |
|---|---|
| Amazon Nova Pro via Bedrock for analysis + fix proposals | ✅ |
| Remediation suggestions (HCL diff + plain-English summary) | ✅ |
| Cost-aware findings sorted by estimated monthly impact | ✅ |
| Skips LLM call when terraform plan fails (unmanaged-only mode) | ✅ |

### Security scanning

| Feature | Status |
|---|---|
| Trivy misconfiguration scan on proposed drift fixes | ✅ |
| Auto-fix loop (LLM patch → validate → re-scan) | ✅ |
| Pre-existing vs newly-introduced issue classification | ✅ |
| Baseline scan before patching to establish origin | ✅ |
| Human-review routing for CIDR/KMS/IAM decisions | ✅ |

### Unmanaged resource detection

| Feature | Status |
|---|---|
| boto3-based AWS enumeration (EC2, VPC, S3, DynamoDB, RDS, ElastiCache, etc.) | ✅ |
| Terraform state subtraction | ✅ |
| Classification (default / tagged-elsewhere / genuinely unmanaged) | ✅ |
| Unmanaged exceptions in Supabase with optional cost cap | ✅ |
| Integrated into agent pipeline behind `--scan-unmanaged` flag | ✅ |
| Continues when terraform plan fails (standalone AWS scan) | ✅ |

### Cost estimation

| Feature | Status |
|---|---|
| Static price cache (16 services, 4 regions) | ✅ |
| Per-resource hourly + monthly estimate | ✅ |
| 4-hour runtime window for accrued cost | ✅ |
| Cost surfaced in PR body, PagerDuty summary, Slack message | ✅ |
| `cost_impact` field on findings sorted by descending cost | ✅ |

### Alerting

| Feature | Status |
|---|---|
| PagerDuty (severity-routed, including rollback aborts) | ✅ |
| Slack incoming webhook (batched max 5/card) | ✅ |
| Workflow outcome → Slack (accept/reject/failure/rollback-blocked) | ✅ |
| Configurable severity routing via dashboard (HIGH/MEDIUM/LOW → PagerDuty/Slack) | ✅ |
| Routing rules stored in Supabase, configurable per scope | ✅ |
| PagerDuty/Slack credentials stored in Supabase (masked, service-role only) | ✅ |
| Test-alert button from dashboard | ✅ |
| Per-scan alert tracking (PagerDuty dispatch count, Slack message count) | ✅ |
| All notification modules CI-safe (stdlib + `requests`, no dotenv dependency) | ✅ |

### Noise suppression

| Feature | Status |
|---|---|
| Drift exceptions with expiry, pattern matching, and optional `auto` flag | ✅ |
| Unmanaged exceptions with cost-cap threshold | ✅ |
| Auto-suppress rules for ASG-managed / AWS-managed drift (no human review needed) | ✅ |
| Direct Supabase CRUD from dashboard (add/expire/delete, no PR needed) | ✅ |
| Supabase-backed → pipeline reads exceptions at runtime, no local file dependency | ✅ |

### Patching

| Feature | Status |
|---|---|
| hcledit-based `.tf` patching for simple types (string, number, bool) | ✅ |
| Regex fallback when hcledit not available | ✅ |
| JSON-to-HCL converter for complex types (maps, lists, tags) | ✅ |
| Human-in-the-loop reviews every PR before merge | ✅ |

### Rollback

| Feature | Status |
|---|---|
| Baselines stored in Supabase (`changes_jsonb` column) — no local files needed | ✅ |
| `--rollback --rollback-pr <n>` CLI (reads from Supabase, works from any machine) | ✅ |
| Freshness gate at PR creation (checkpoint 1, always creates PR — warns if stale) | ✅ |
| Freshness gate at apply time (checkpoint 2, blocks apply + reverts merge if stale) | ✅ |
| PagerDuty on checkpoint-2 abort | ✅ |
| Self-similar rollback chain | ✅ |
| Rollback dashboard with live preview diff and polling | ✅ |

### CI/CD drift gate

| Feature | Status |
|---|---|
| Pre-apply check via `pre_apply_check.py` (reads Supabase for unresolved drift) | ✅ |
| `DRIFT_GATE_MODE` variable (`warn` or `block`) | ✅ |
| Blocked apply auto-reverts merge to keep code + AWS consistent | ✅ |
| Gate runs on both ACCEPT and REJECT paths | ✅ |
| Manual workflow_dispatch runs logged to Supabase history | ✅ |

### Historical drift store

| Feature | Status |
|---|---|
| Supabase PostgreSQL backend | ✅ |
| Append on drift detection, resolve on accept/reject | ✅ |
| `drift_trends.py` markdown report + Supabase RPC aggregation | ✅ |
| `--trends` flag on agent CLI | ✅ |
| Dashboard trends page with Chart.js (most-drifted, MTTR, daily volume, rollbacks, KPIs) | ✅ |
| MTTR with server-side aggregation + zero-fill for missing days | ✅ |

### IAM

| Feature | Status |
|---|---|
| Separate scan (read-only) and apply (write) roles per account | ✅ |
| OIDC trust scoped to GitHub environment (apply) or branch (scan) | ✅ |
| Inline policies with explicit `Describe*` / `Get*` read permissions for refresh | ✅ |
| Write policies scoped to managed resource prefixes (S3, DynamoDB) | ✅ |

### Dashboard

| Feature | Status |
|---|---|
| Live scan trigger with stage tracking (polling + Realtime) | ✅ |
| Stage indicators with no-op detection (hollow dots for idle nodes) | ✅ |
| Per-stage outcome chips (Drift PR N / Unmanaged PR N) | ✅ |
| Alerts-sent tracking (PagerDuty + Slack counts, no-op detection) | ✅ |
| Drift findings explorer with filters, search, pagination | ✅ |
| Rollback UI with preview diff + confirmation polling | ✅ |
| Trends page with 4 Chart.js visualizations + KPI summary cards | ✅ |
| Exceptions management (add/expire/delete via Supabase CRUD) | ✅ |
| Alerts configuration (PagerDuty/Slack keys, severity routing, test send) | ✅ |
| Environments management (CRUD, git source, AWS credentials via UI) | ✅ |
| Responsive dark-theme design with shared site navigation | ✅ |
| 5-minute scan polling timeout with user-facing message | ✅ |
| Structured scan results (mode banner, drift/unmanaged blocks, per-type PR links) | ✅ |
| Structured error display (summary, suggestion, expandable details) | ✅ |
| Shared environment selector component (`env-selector.js`) | ✅ |
| API access token gating (`X-Api-Access-Token` header, constant-time check) | ✅ |
| Live execution log streaming (terminal pane, 2s polling, scroll-lock) | ✅ |
| One-time browser token prompt with localStorage persistence | ✅ |

### Environments & credentials

| Feature | Status |
|---|---|
| Environments table with full scope metadata (region, state bucket, IAM roles) | ✅ |
| AWS credential storage: profile / OIDC assume-role / static keys | ✅ |
| Masked secrets display (last 4 chars, never sent to frontend in full) | ✅ |
| Git clone source (repo URL, branch, auth) per environment | ✅ |
| Dynamic scope resolution from Supabase (add an environment → valid everywhere) | ✅ |
| Auth-type validation (keys requires both access key + secret key) | ✅ |
| AWS_PROFILE only set for profile-auth environments (prevents boto3 crash) | ✅ |

### Error handling & observability

| Feature | Status |
|---|---|
| `scan_runs` lifecycle tracking (running → complete/failed) in Supabase | ✅ |
| Live stdout capture from agent subprocess (ring buffer + `/tmp/drift-logs/` file) | ✅ |
| `GET /api/scan/{run_id}/logs?offset={n}` endpoint with file + buffer fallback | ✅ |
| Subprocess stdout piped with `-u` (unbuffered), `encoding="utf-8"`, `stderr→STDOUT` | ✅ |
| Terraform plan failure caught inside try/except (was pre-try `sys.exit(1)`) | ✅ |
| Unmanaged-only scan continues when terraform plan fails | ✅ |
| Human-readable error messages via `humanize_terraform_error()` pattern matching | ✅ |
| UTF-8 encoding on all subprocess calls (no mangled box-drawing characters) | ✅ |
| `-no-color` on all terraform commands + ANSI-strip defense-in-depth | ✅ |
| Stale-request guard in `refreshAll` (prevents rapid-tab-switch rendering races) | ✅ |
| Null-safe `updateTracker` with Realtime channel unsubscribe on stop | ✅ |
| Canvas lifecycle fix (recreate on empty → data transitions) | ✅ |

---

## Quick start

### Prerequisites

- Python 3.11+ with `requests`, `boto3`, `langchain-aws`, `langgraph`, `pygithub`
- Node.js 20+ with pnpm (for the new React dashboard)
- Terraform CLI 1.9+
- Trivy (optional, for security scanning)
- hcledit (optional, for reliable `.tf` patching)
- Supabase project (for drift history, exceptions, routing rules, environments, secrets)

## Running the stack

Three services, three terminals:

```bash
# ── Terminal 1: Python backend (port 8080) ──────────────────────────────
# Serves the vanilla dashboard HTML/JS/CSS, REST APIs for scan triggers,
# rollback, exceptions, environments, and the live-log streaming endpoint.
python dashboard/serve.py --port 8080

# ── Terminal 2: Express API server (port 3000) ──────────────────────────
# Backend for the new React dashboard — Drizzle ORM, proxy routes, config.
cd frontend && pnpm install && pnpm --filter @workspace/api-server dev

# ── Terminal 3: React dev server (port 5173) ────────────────────────────
# New TypeScript dashboard — open http://localhost:5173 in a browser.
cd frontend && pnpm --filter @workspace/web dev
```

### CLI (drift pipeline)

```bash
# Drift detection only
python drift_reconciler/agent.py --tf-dir terraform_code/ec2_terraform_account_a --account-label scope-a --region us-east-1

# With unmanaged resource scan
python drift_reconciler/agent.py --tf-dir terraform_code/ec2_terraform_account_a --account-label scope-a --region us-east-1 --scan-unmanaged

# Rollback a previous fix
python drift_reconciler/agent.py --tf-dir terraform_code/ec2_terraform_account_a --account-label scope-a --region us-east-1 --rollback --rollback-pr 50

# Trend report
python drift_reconciler/agent.py --trends --trends-account scope-a
```


### Dashboard pages

Both dashboards cover the same 10 pages:

| Page | Vanilla (port 8080) | React (port 5173) |
|---|---|---|
| Overview / KPI cards | `index.html` | `Overview.tsx` |
| Drift findings explorer | `explorer.html` | `Explorer.tsx` |
| Scan trigger + live logs | `scan.html` | `Scan.tsx` |
| Rollback preview + confirm | `rollback.html` | `Rollback.tsx` |
| Trends + Chart.js | `trends.html` | `Trends.tsx` |
| Exception CRUD | `exceptions.html` | `Exceptions.tsx` |
| Alert settings + routing | `alerts.html` | `Alerts.tsx` |
| Environment management | `environments.html` | `Environments.tsx` |
| PR queue | `pr-queue.html` | `PrQueue.tsx` |

### Frontend monorepo structure

```
frontend/
  artifacts/
    web/               # React SPA — 10 pages, shadcn/ui, Tailwind, Supabase
      src/pages/       # Overview, Explorer, Scan, Rollback, Trends,
                       #   Exceptions, Alerts, Environments, PR Queue
      src/components/  # LogViewer, AuthPromptModal, ScopeSelector, ui/*
      src/hooks/       # useScanLogs, useAuthStore, useDriftEvents, etc.
      src/api/         # Supabase client + API fetch wrappers
    api-server/        # Express API server — Drizzle ORM, Pino, CORS
    mockup-sandbox/    # UI prototyping sandbox with infinite canvas
  lib/
    api-spec/          # OpenAPI 3.1 specification (Orval codegen source)
    api-zod/           # Generated Zod validation schemas
    api-client-react/  # Generated React query hooks + API client
    db/                # Drizzle ORM schema + migrations
  scripts/             # Post-merge build scripts
```

### Environment

Copy `.env.example` to `.env` and configure:

| Variable | Purpose |
|---|---|
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Database backend |
| `SUPABASE_ANON_KEY` | Dashboard read access |
| `GITHUB_TOKEN` / `GITHUB_REPO` | PR creation |
| `PAGERDUTY_ROUTING_KEY` | PagerDuty alerts (legacy — can be managed via dashboard) |
| `SLACK_WEBHOOK_URL` | Slack notifications (legacy — can be managed via dashboard) |
| `AWS_REGION` | Default region |
| `API_ACCESS_TOKEN` | Optional dashboard auth token (shared secret, `X-Api-Access-Token` header) |
| `DRIFT_CLONE_BASE` | Git clone directory (default: `~/.drift-clones`) |

### GitHub Actions

Two workflows handle PR lifecycle:

- `drift-preview.yml` — posts `terraform plan` output as a PR comment on `pull_request: [opened, synchronize]`
- `drift-reconciler.yml` — on `pull_request: [closed]`, runs `terraform apply` (accepted) or revert (rejected), resolves drift history, posts Slack notification

Required GitHub Secrets: `SCOPE_A_APPLY_ROLE_ARN` / `SCOPE_B_APPLY_ROLE_ARN`, `PROD_A_REGION` / `PROD_B_REGION` (Variables), `PAGERDUTY_ROUTING_KEY`, `SLACK_WEBHOOK_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.

---

## Supabase tables

| Table | Purpose |
|---|---|
| `drift_events` | Per-finding event log (resource, severity, PR, status, changes JSONB) |
| `scan_runs` | Pipeline invocation tracking (status, stages, results) |
| `rollback_runs` | Rollback invocation tracking |
| `notification_secrets` | Singleton: PagerDuty key + Slack webhook (service-role only) |
| `severity_routing_rules` | HIGH/MEDIUM/LOW → PagerDuty/Slack routing |
| `drift_exception_registry` | Drift + unmanaged exception entries (anon-readable) |
| `environments` | Scope metadata, AWS credentials, git source |
| `environment_secrets` | Per-environment secrets (AWS keys, GitHub token, service-role only) |

## Project structure

```
drift_reconciler/
  agent.py                    # LangGraph pipeline entrypoint
  trivy_agent.py              # Trivy scan → fix → scan loop
  github_integration.py       # PR creation, hcledit/regex .tf patching
  pagerduty_alert.py          # PagerDuty Events API
  slack_notify.py             # Slack Block Kit webhook
  workflow_notify.py          # Workflow outcome → Slack
  drift_history.py            # Supabase drift event log
  drift_trends.py             # Markdown trend report generator
  drift_migrate.py            # Local JSONL / baselines → Supabase migration
  rollback_check.py           # Checkpoint-2 freshness gate
  pre_apply_check.py          # CI/CD pre-apply drift gate (warn/block)
  unmanaged_scanner.py        # boto3 AWS resource enumeration
  formatting_drift_json.py    # terraform plan JSON → drift report
  notification_config.py      # Supabase-backed notification secrets CRUD
  environment_credentials.py  # AWS session resolver (profile/role/keys)
  scan_runs.py                # Scan lifecycle tracking in Supabase
  rollback_runs.py            # Rollback lifecycle tracking in Supabase
  cost_cache.json             # Static on-demand hourly rates

dashboard/
  serve.py                    # ThreadingHTTPServer (8 pages, REST APIs)
  index.html / dashboard.js   # Live KPI dashboard
  explorer.html / explorer.js # Drift findings explorer
  scan.html                   # Scan trigger + stage tracker
  rollback.html / rollback.js # Rollback preview + confirm
  trends.html / trends.js     # Chart.js visualizations
  exceptions.html / exceptions.js # Exception CRUD
  alerts.html / alerts.js     # Notification settings + routing
  environments.html / environments.js # Environment management
  env-selector.js             # Shared dynamic scope selector
  auth.js                     # API access token prompt + localStorage persistence
  styles.css                  # Dark-theme responsive stylesheet

terraform_code/
  ec2_terraform_account_a/    # scope-a terraform root
  ec2_terraform_account_b/    # scope-b terraform root
  account-a/                  # scope-a IAM bootstrap (scan + apply roles)

migrations/
  create_scan_runs_table.sql
  create_rollback_runs_table.sql
  create_drift_severity_summary_view.sql
  create_exception_registry_table.sql
  create_notification_secrets_table.sql
  create_severity_routing_rules_table.sql
  create_environments_table.sql
  create_scope_config_table.sql
  create_trends_rpc_functions.sql
  add_aws_credentials_to_environments.sql
  add_git_source_to_environments.sql
  add_pr_review_columns_drift_events.sql
  add_rolled_back_from_pr_column.sql
  enable_rls_drift_events.sql

.github/workflows/
  drift-preview.yml           # PR plan preview
  drift-reconciler.yml        # PR accept/reject, rollback gate, notify

frontend/                     # New TypeScript React dashboard (pnpm monorepo)
  artifacts/web/              # React SPA — shadcn/ui, Vite, Tailwind
  artifacts/api-server/       # Express API server — Drizzle, Pino, CORS
  artifacts/mockup-sandbox/   # UI prototyping sandbox
  lib/api-spec/               # OpenAPI 3.1 spec (Orval codegen source)
  lib/api-zod/                # Generated Zod schemas
  lib/api-client-react/       # Generated React hooks + API client
  lib/db/                     # Drizzle ORM schema
  scripts/                    # Post-merge build helpers
```
