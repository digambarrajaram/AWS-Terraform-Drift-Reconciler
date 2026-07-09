# AWS Terraform Drift Reconciler

A pragmatic tool that detects drift between Terraform desired state and live AWS resources, proposes HCL reconciliation suggestions, and opens GitHub pull requests with the proposed changes.

This repository contains a demo-quality server, a small Python analysis agent, and a React UI for reviewing proposed reconciliations. The project mixes real integrations (when configured via environment variables) with an in-memory simulation layer for demos.

Key design principle: this tool detects and proposes — it does NOT execute remediation on your infrastructure. Merging a PR created by this tool updates the reconciliation record and may mark a resource reconciled in the app's in-memory state, but it does not run `terraform apply`. Apply changes through your CI/CD pipeline (GitHub Actions, etc.).

---

What's real vs. recommendation-only

- Real (when corresponding env vars are provided and configured):
  - Terraform plan drift detection: the server can call `terraform plan -json` (via `terraformPlanDrift`) and use the plan output to detect changes.
  - Terraform state storage in S3: state is read from S3 via `readTerraformState()` (S3 client).
  - GitHub PR creation & branch updates: uses Octokit when `GITHUB_TOKEN` is set to create/update branches and open pull requests.
  - PagerDuty alerts: when `PAGERDUTY_ROUTING_KEY` is configured, alerts are posted via the PagerDuty integration.
  - Audit persistence: audit records are written to DynamoDB (best-effort) when AWS credentials are provided. Table naming uses the configured TF resource/project prefix and environment (see env vars).

- Heuristic / recommendation-only (informational; do not assume enforcement):
  - HCL fix suggestions: the Python agent generates illustrative HCL diffs (via difflib) as proposed reconciliations. These are recommendations only and have not been validated by `terraform validate` or `terraform plan` in the target environment.
  - Policy checks: policy and security checks are heuristic keyword-based matches (policy IDs are labeled as heuristic and include a `source` field). They are useful guidance but not a substitute for a real policy scanner (e.g., Checkov) configured in CI.

---

Quick architecture overview

- Frontend: React + Vite SPA (`src/`) — shows resources, PRs, timeline, and approval flow.
- Backend: Express server (`server.ts`) — in-memory `systemState` that drives the UI, spawns the Python agent for analysis, creates PRs via GitHub integration, and writes audit records to DynamoDB when configured.
- Agent: Python agent (`agent.py` / `agent_nova.py`) — 5-node pipeline: classification → security analysis → cost estimate → HCL reconciliation (diff) → policy scan (heuristic). The agent outputs a `DriftAnalysis` JSON object.
- AWS integration layer: `src/integrations/aws.ts` — reads Terraform state from S3, describes live resources (EC2), writes audit records to DynamoDB, and exposes cost/STS helpers.
- GitHub integration: `src/integrations/github.ts` — uses Octokit when `GITHUB_TOKEN` is set; falls back to a simulated PR response otherwise.

Important safety note: the server does not run `terraform apply`. The produced PRs are intended to be reviewed and applied by your CI/CD pipeline.

---

Environment variables (summary)

Configure these in your environment or an env file. Values marked mandatory depend on whether you want the integration to be "real" (versus simulated):

- `PORT` — (default: `3000`) server listen port.
- `NODE_ENV` — (`development` | `production`).
- `ENVIRONMENT` — (`demo` | `staging` | `production`). Default: `production`.

AWS & Terraform state

- `AWS_ACCESS_KEY_ID` — AWS credential (optional; required for real S3/DynamoDB/EC2 calls).
- `AWS_SECRET_ACCESS_KEY` — AWS secret.
- `AWS_REGION` — AWS region (default `us-east-1`).
- `TF_STATE_BUCKET` — S3 bucket containing Terraform state. Example: `my-project-state-123456789012`.
- `TF_STATE_KEY` — Key path to terraform state file in the bucket. Example: `ec2/terraform.tfstate`.
- `TF_RESOURCE_PREFIX` or `TF_PROJECT_NAME` — Project prefix used to construct DynamoDB audit table name and lock table names. If neither provided, a fallback prefix is used.
- `SCAN_HEARTBEAT_MS` — Milliseconds between scan heartbeats for scheduler health checks; defaults to `3600000` (1h).
- `TERRAFORM_DIR` — Local Terraform working directory used by `terraform plan`; defaults to `./terraform/ec2`.
- `TERRAFORM_PATH` — Explicit path to the Terraform binary if it is not on PATH; if unset the server searches common locations and then uses `terraform`.

GitHub

- `GITHUB_TOKEN` — Personal access token for Octokit. If missing, PR creation is simulated.
- `GITHUB_REPO` — `owner/repo` used to create reconciliation branches and PRs.
- `GITHUB_BRANCH` — Base branch for reconciliation PRs (e.g., `main` or `drift`).

PagerDuty

- `PAGERDUTY_ROUTING_KEY` — Routing key for PagerDuty Events API (optional). If missing, alerts log to console.

Audit & DynamoDB

- DynamoDB table naming convention (used by the app): `{TF_RESOURCE_PREFIX || TF_PROJECT_NAME || 'aws-terraform-drift-reconciler'}-{ENVIRONMENT}-drift-audit`.
  - The app writes audit records via `writeAuditRecord(...)`; these writes are best-effort (fire-and-forget) and only performed when AWS credentials are available.
  - A bootstrap/locks DynamoDB table may also be used by your Terraform backend; the repo's terraform bootstrap step documents lock table creation.

Other

- `AGENT_MODE` — `deterministic` (keyword-driven agent) or `nova` (future/LLM mode). Default: `deterministic`.

- `API_ACCESS_TOKEN` — If set, destructive endpoints (`/api/reset`, `/api/merge-pr`) require header `X-Api-Access-Token: <value>`. This token is an access control guard for the server and should be managed like any other service credential.

---

API overview

All endpoints return JSON. The server maintains an in-memory `systemState` that drives the UI; a server restart clears that in-memory state. Audit records are written to DynamoDB when available.

Key endpoints

- `GET /api/state` — Returns masked application state (resources, PRs, timeline). Sensitive fields are masked.
- `POST /api/scan` — Run a drift scan. The server attempts `terraform plan -json`, falls back to AWS describe calls and then runs a deep-diff on configured resources. Runs immediately (no artificial demo delays).
- `GET /health` — Basic service health check. Returns `{ status: 'ok', uptime: ... }`.
- `GET /ready` — Readiness check for AWS, S3, GitHub, and PagerDuty integrations.
- `GET /metrics` — Export internal service metrics such as scan totals and PagerDuty alert counts.
- `POST /api/analyze` — Invoke the Python agent to analyze a resource and propose an HCL fix. The agent output is wrapped into a Pull Request (simulated or real depending on GitHub config).
- `POST /api/merge-pr` — Merge a PR. This is a destructive action and requires `{ approvedBy: "name" }` in the request body; the actor is recorded in the audit trail. Important: merging here updates the reconciliation record and can mark the resource reconciled in the app's in-memory state but does NOT execute `terraform apply` on your infrastructure.
- `POST /api/merge-pr/reject` — Reject a PR with a reason; audited.
- `POST /api/reset` — Reset all resources and timeline to initial state. Requires `{ requestedBy: "name" }` and is audited.
- `POST /api/resource` — Register a custom tracked resource (name, type, terraformCode, desiredState).
- `POST /api/alerts/config` — Toggle PagerDuty alerting on/off.

Audit

- The app records audit events (scan, pr_created, pr_merged, reset, etc.) in-memory and attempts to persist them to DynamoDB when AWS credentials are present.
- DynamoDB audit table name follows the naming convention described above.

---

Agent semantics & guarantees

- The Python agent produces a `DriftAnalysis` JSON object that includes classification, risk assessment, a proposed HCL diff (`hclFix`), a `fixType` set to `unapproved_recommendation`, and heuristic policy references.
- Policy findings and IDs are heuristic and labeled accordingly (e.g., `source: "keyword_matching"`, IDs prefixed with `heuristic_`). Treat these as recommendations for human review or CI-based policy scanning.
- The server intentionally does not set any `validationStatus` claiming that the agent's suggestion was validated. If you want authoritative validation, integrate `terraform validate --json` or a real policy scanner (Checkov) in your CI pipeline or ask to add server-side validation (requires Terraform binary and careful environment setup).

---

Developer notes

- Local development

  1. Install dependencies: `npm install`
  2. Copy env template: `cp .env.example .env` and populate required variables for real integrations (AWS creds, GITHUB_TOKEN, etc.)
  3. Start dev server: `npm run dev` (Vite + Node dev server)

Secrets and `.env`

- Keep only `.env.example` committed in the repository. Do NOT commit a live `.env` file containing secrets.
- For local development, copy `.env.example` to `.env` and populate values. Add `.env` to your personal Git ignore if your system doesn't already ignore it.
- After any accidental commit of secrets: rotate the secret immediately (revoke and reissue credentials), then follow the repository history purge instructions in `HISTORY_PURGE_INSTRUCTIONS.md` (this repository contains a prepared guide and scripts).

If you operate shared CI/CD or deploy pipelines, move sensitive values to a secrets manager (AWS Secrets Manager, GitHub Actions Secrets, or your CI provider) and reference them via the provider's secure mechanism rather than storing them in files.

- Running the Python agent

  The agent runs as a subprocess. To test locally you can run `python3 agent.py` and pipe a resource JSON on stdin; the agent emits a `DriftAnalysis` object to stdout.

- Making integrations real

  - To enable real S3/DynamoDB/EC2 runs, set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_REGION`.
  - Provide `TF_STATE_BUCKET` and `TF_STATE_KEY` pointing to your Terraform state file in S3.
  - To persist audit logs, ensure a DynamoDB table named `${prefix}-${env}-drift-audit` exists and the provided AWS credentials can write to it.
  - To enable real PR creation, set `GITHUB_TOKEN` and `GITHUB_REPO`.
  - To send real alerts, set `PAGERDUTY_ROUTING_KEY`.

---

Security & operational guidance

- This tool is intended to assist operators by detecting drift and proposing fixes. It is not an automatic remediation engine. Treat PRs generated by this tool as proposals that must be validated and applied by your existing CI/CD and change control workflows.
- Do not store long-lived credentials in the app's in-memory state or in client-side responses. The server masks credential-like fields in `/api/state` responses.
- If you require the server to perform authoritative validation of HCL fixes before creating PRs, we can add `terraform validate --json` and/or Checkov CLI invocation — note this requires the Terraform binary in the server environment and care around module resolution and backend config.

---

*** End of README ***
