# AWS Terraform Drift Reconciler

An automated drift-detection pipeline that compares Terraform desired state against live AWS resources, classifies drift, proposes HCL fixes via an LLM agent, and opens GitHub pull requests for review. Supports multi-account/multi-region deployment, security scanning, cost estimation, unmanaged-resource detection, rollback, Slack/PagerDuty alerting, and historical trend reporting.

## Architecture

```
terraform plan → format drift JSON → LangGraph agent pipeline
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
            [unmanaged scan]      [reconcile agent]       [trivy gate]
            (optional)            classify + propose      scan→fix→scan
                    │                     │                     │
                    ▼                     ▼                     ▼
            [alert + PR]          [PagerDuty / Slack]    [drift_history]
```

- **Agent**: Python (`test/agent.py`) — LangGraph pipeline with configurable nodes.
- **Workflow**: GitHub Actions (`drift-reconciler.yml`, `drift-preview.yml`).
- **State**: Terraform remote state in S3, lock table in DynamoDB.
- **Alerting**: PagerDuty (high-severity) + Slack (all severities + workflow outcomes).
- **History**: Supabase PostgreSQL (drift events + trend reporting).

---

## Features

### Core drift detection

| Feature | Status |
|---|---|
| Drift detection via `terraform plan -json` | ✅ |
| Multi-account / multi-region matrix | ✅ |
| GitHub OIDC-based AWS auth (scan role + apply role) | ✅ |
| PR creation with patched `.tf` file | ✅ |
| PR accept/reject workflow with `terraform apply` | ✅ |
| Scope-tagged PR branches, titles, and dedup keys | ✅ |
| `lifecycle.ignore_changes` / externally-managed resource handling | ✅ |
| `drift-exceptions.json` for suppressing known drift | ✅ |

### LLM agent

| Feature | Status |
|---|---|
| Amazon Nova Pro via Bedrock for analysis + fix proposals | ✅ |
| Remediation suggestions (HCL diff + plain-English summary) | ✅ |
| Cost-aware findings sorted by estimated monthly impact | ✅ |

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
| `unmanaged-exceptions.json` with optional cost cap | ✅ |
| Integrated into agent pipeline behind `--scan-unmanaged` flag | ✅ |

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
| PagerDuty (HIGH severity → page) | ✅ |
| Slack incoming webhook (MEDIUM/LOW → channel post) | ✅ |
| Batched Slack messages (max 5 findings per card) | ✅ |
| Workflow outcome notifications (accept/reject/failure/rollback-blocked) | ✅ |
| All notification modules CI-safe (zero external dependencies beyond `requests`) | ✅ |

### Rollback

| Feature | Status |
|---|---|
| Baseline stored per-PR (`.drift-baselines/pr-{n}/`) | ✅ |
| `--rollback --rollback-pr <n>` CLI | ✅ |
| Freshness gate at PR creation (checkpoint 1, informational) | ✅ |
| Freshness gate at apply time (checkpoint 2, blocks apply + reverts merge if stale) | ✅ |
| PagerDuty on checkpoint-2 abort | ✅ |
| Self-similar rollback chain (every rollback creates a new baseline) | ✅ |
| Never cross-type supersede (regular ↔ rollback PRs don't collide) | ✅ |

### Historical drift store

| Feature | Status |
|---|---|
| Supabase PostgreSQL backend | ✅ |
| Append on drift detection, resolve on accept/reject | ✅ |
| `drift_trends.py` markdown report (most-drifted, MTTR, unresolved, rollbacks, summary) | ✅ |
| `--trends` flag on agent CLI | ✅ |
| Migration script for local JSONL → Supabase | ✅ |

### IAM

| Feature | Status |
|---|---|
| Separate scan (read-only) and apply (write) roles per account | ✅ |
| OIDC trust scoped to GitHub environment (apply) or branch (scan) | ✅ |
| Inline policies with explicit `Describe*` / `Get*` read permissions for refresh | ✅ |
| Write policies scoped to managed resource prefixes (S3, DynamoDB) | ✅ |

---

## Quick start

### Prerequisites

- Python 3.11+ with `requests`, `boto3`, `langchain-aws`, `langgraph`, `pygithub`
- Terraform CLI 1.9+
- Trivy (optional, for security scanning)
- hcledit (optional, for reliable `.tf` patching)
- Supabase project (for drift history)

### Local run

```bash
# Drift detection only
python test/agent.py --tf-dir terraform_code/ec2_terraform_account_a --account-label scope-a --region us-east-1

# With unmanaged resource scan
python test/agent.py --tf-dir terraform_code/ec2_terraform_account_a --account-label scope-a --region us-east-1 --scan-unmanaged

# Rollback a previous fix
python test/agent.py --tf-dir terraform_code/ec2_terraform_account_a --account-label scope-a --region us-east-1 --rollback --rollback-pr 50

# Trend report
python test/agent.py --trends --trends-account scope-a
```

### Environment

Copy `.env.example` to `.env` and configure:

| Variable | Purpose |
|---|---|
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS credentials for boto3 / Bedrock |
| `AWS_REGION` | Default region |
| `GITHUB_TOKEN` / `GITHUB_REPO` | PR creation |
| `PAGERDUTY_ROUTING_KEY` | PagerDuty alerts |
| `SLACK_WEBHOOK_URL` | Slack notifications |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Drift history store |

### GitHub Actions

Two workflows handle PR lifecycle:

- `drift-preview.yml` — posts `terraform plan` output as a PR comment on `pull_request: [opened, synchronize]`
- `drift-reconciler.yml` — on `pull_request: [closed]`, runs `terraform apply` (accepted) or revert (rejected), resolves drift history, posts Slack notification

Required GitHub Secrets: `AWS_ROLE_ARN` (or scope-specific `SCOPE_A_APPLY_ROLE_ARN` / `SCOPE_B_APPLY_ROLE_ARN`), `PROD_A_REGION` / `PROD_B_REGION` (variables), `PAGERDUTY_ROUTING_KEY`, `SLACK_WEBHOOK_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`.

---

## Project structure

```
test/
  agent.py                    # LangGraph pipeline entrypoint
  trivy_agent.py              # Trivy scan → fix → scan loop
  github_integration.py       # PR creation, hcledit/regex .tf patching
  pagerduty_alert.py          # PagerDuty Events API
  slack_notify.py             # Slack Block Kit webhook
  workflow_notify.py          # Workflow outcome → Slack
  drift_history.py            # Supabase drift event log
  drift_trends.py             # Markdown trend report generator
  drift_migrate.py            # Local JSONL → Supabase migration
  rollback_check.py           # Checkpoint-2 freshness gate
  unmanaged_scanner.py        # boto3 AWS resource enumeration
  formatting_drift_json.py    # terraform plan JSON → drift report
  cost_cache.json             # Static on-demand hourly rates

terraform_code/
  ec2_terraform_account_a/    # scope-a terraform root
  ec2_terraform_account_b/    # scope-b terraform root
  account-a/                  # scope-a IAM bootstrap (scan + apply roles)
  account-b/                  # scope-b IAM bootstrap
  backend-state-a/            # scope-a S3 + DynamoDB state backend
  backend-state-b/            # scope-b S3 + DynamoDB state backend

.github/workflows/
  drift-preview.yml           # PR plan preview
  drift-reconciler.yml        # PR accept/reject, rollback gate, notify
```
