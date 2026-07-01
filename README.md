# AWS Terraform Drift Reconciler — Simulation

> **An in-memory simulation of an AI-powered IaC drift detection and auto-remediation pipeline.**
>
> Detects configuration drift, classifies risk, generates illustrative HCL diffs, and manages simulated pull requests — all through a Human-in-the-Loop approval gate.

---

## What this is

This is a **full-stack demo** that simulates the complete lifecycle of infrastructure drift detection and remediation. Every component runs in-memory — no real AWS, no real Terraform, no real GitHub. It is designed to demonstrate the user experience, pipeline flow, and code architecture of such a system.

**Real:** the React dashboard, the Express API, the Python drift-analysis agent, the TypeScript types, and the approval-gate logic.

**Simulated:** AWS resources, `terraform plan` execution, GitHub PRs, Checkov scans, Slack/Email/PagerDuty alerting, DynamoDB persistence.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React SPA (Vite + Tailwind)              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐ │
│  │ Resources│ │   PRs    │ │ Timeline │ │ Alerts + HITL │ │
│  │   Tab    │ │   Tab    │ │   Tab    │ │     Tab       │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───────┬───────┘ │
└───────┼────────────┼────────────┼────────────────┼─────────┘
        │            │            │                │
   GET/POST /api/*   │            │                │
        │            │            │                │
┌───────┴────────────┴────────────┴────────────────┴─────────┐
│                 Express Server (server.ts)                  │
│  ┌──────────┐ ┌──────────────┐ ┌────────────────────────┐  │
│  │ In-Memory│ │  API Routes  │ │ Python Agent Bridge    │  │
│  │  State   │ │ /api/state   │ │ spawn → agent.py       │  │
│  │ (no DB)  │ │ /api/scan    │ │ stdin/stdout JSON      │  │
│  │          │ │ /api/analyze │ │ timeout 10s            │  │
│  │          │ │ /api/merge-pr│ └────────────────────────┘  │
│  │          │ │ /api/reset   │                              │
│  │          │ │ /api/demo/*  │                              │
│  └──────────┘ └──────────────┘                              │
└──────────────────────────────┬──────────────────────────────┘
                               │ spawn + JSON over stdin/stdout
┌──────────────────────────────┴──────────────────────────────┐
│                  Python Agent (agent.py)                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐  │
│  │Classify  │→│ Security │→│  Cost    │→│   HCL Recon   │  │
│  │  Node    │ │ Analysis │ │  Est.    │ │  (difflib)    │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────┬────────┘  │
│                                                 │           │
│                                          ┌──────┴────────┐  │
│                                          │ Policy Scan   │  │
│                                          │ (simulated)   │  │
│                                          └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Data flow for one drift cycle

```
1. Button click → POST /api/drift  (mutates in-memory actualState)
2. Button click → POST /api/scan   (recursive deep-diff desired vs actual)
3. Button click → POST /api/analyze
   ├─ spawn python3 agent.py, pipe resource JSON to stdin
   ├─ agent runs 5-node pipeline (classify → audit → cost → HCL → scan)
   ├─ agent prints result JSON to stdout
   └─ server wraps result into an in-memory PullRequest
4. Review PR in UI → approve if high-risk → POST /api/merge-pr
   └─ actualState ← deep-clone of desiredState (resource restored)
```

### Directory structure

```
.
├── server.ts              # Express API server + in-memory state store
├── agent.py               # Python drift-analysis agent (5-step pipeline)
├── package.json           # Dependencies and scripts
├── tsconfig.json          # TypeScript strict-mode config
├── vite.config.ts         # Vite bundler config
├── index.html             # SPA entry point
├── .env.example           # Environment variable template
├── README.md
├── metadata.json          # AI Studio metadata
└── src/
    ├── main.tsx           # React entry point
    ├── App.tsx            # Main dashboard component (tabs, state, handlers)
    ├── types.ts           # Shared TypeScript interfaces
    ├── index.css          # Tailwind imports + custom theme
    └── components/
        ├── Header.tsx              # Top bar (scan/reset buttons, status)
        └── AlertsAndApproval.tsx   # Alert config + HITL flow + simulation
```

---

## Quick Start

### Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Node.js | ≥ 18 | `node --version` |
| npm | ≥ 9 | `npm --version` |
| Python | ≥ 3.9 | `python3 --version` |

### Setup

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd aws-terraform-drift-reconciler

# 2. Install dependencies
npm install

# 3. (Optional) Copy the env template
cp .env.example .env

# 4. Start the dev server
npm run dev
```

Open **http://localhost:3000** in your browser.

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PORT` | `3000` | Server listen port |
| `NODE_ENV` | `development` | Set to `production` to serve built assets |
| `DEMO_SCAN_DELAY_MS` | `400` | Artificial delay on scan (ms) — set lower for faster demos |
| `DEMO_TOKEN` | _(empty)_ | If set, `POST /api/reset`, `/api/merge-pr`, and `/api/demo/*` require header `X-Demo-Token: <value>` |
| `GEMINI_API_KEY` | _(unused)_ | Previously used for LLM calls; now the agent is deterministic |

---

## Demo Walkthrough

A complete demo cycle takes ~30 seconds:

### 1. Reset & inject drift
Click **"Reset & Re-Drift Demo"** in the Resources tab sidebar. This resets all state and applies all 4 preset drift scenarios simultaneously:
- **S3 bucket**: ACL changed to `public-read`, public access blocks disabled, encryption off
- **Security group**: SSH port 22 opened to `0.0.0.0/0`
- **IAM role**: `AdministratorAccess` policy attached
- **RDS instance**: `publicly_accessible = true`, storage encryption off

### 2. Scan
Click **"Scan Env"** in the header. The server runs a recursive deep-diff between `desiredState` and `actualState` for every resource. Results appear in the CLI console panel. All 4 resources should show as **DRIFTED**.

### 3. Review a resource
Click any resource in the left panel. The right panel shows:
- **Live State Editor** — edit the actual JSON directly for custom drift scenarios
- **Desired vs Actual** side-by-side diff
- **Desired Terraform (HCL)** — the reference configuration

### 4. Run the agent
Click **"Run Agent Reconciliation"** on a drifted resource. The Python agent:
1. **Classifies** risk via keyword matching (field names + values → `high_risk_change` / `moderate_risk_change` / `low_risk_change`)
2. **Audits** security impact based on resource type (S3, SG, IAM, RDS)
3. **Estimates** cost/compliance overhead
4. **Generates** an illustrative HCL diff using Python's `difflib`
5. **Checks** simulated policy rules (Checkov-style pass/fail)

The UI shows an animated 5-step progress indicator synced to agent completion.

### 5. Review the PR
The app switches to the **PRs** tab. Review:
- **Reconciliation Report** — explanation, security impact, cost impact
- **Agent Audit Trace** — per-node output from the pipeline
- **Simulated Policy Checks** — illustrative pass/fail results
- **Proposed HCL Diff** — side-by-side unified diff

### 6. Approve and merge
- **Low/Moderate risk**: Click **"Merge Pull Request"** to reconcile immediately
- **High risk / Critical / IAM**: The server requires an approval. The merge button prompts for an operator name which is sent as `{ approvedBy: "name" }`. Without it, the API returns **403**.

After merge, the resource's `actualState` is restored to match `desiredState`, the drift is cleared, and the timeline records the event.

### 7. Reset
Click **"Reset"** to return all resources to compliant state (preserves alert configuration).

---

## API Reference

All endpoints return JSON. The state store is **not persistent** — server restart wipes everything.

### `GET /api/state`
Returns the full system state. Credential fields (`slackWebhook`, `pagerDutyKey`) are masked with `••••`.

### `POST /api/scan`
Runs deep-diff on all resources. Returns updated state.
- **409** if a scan is already in progress.
- Artificial delay controlled by `DEMO_SCAN_DELAY_MS` (default 400ms).

### `POST /api/drift`
Injects a preset drift scenario for a known resource.
```json
{ "resourceId": "s3_uploads" }
```
Known IDs: `s3_uploads`, `sg_web`, `role_lambda`, `rds_postgres`.

### `POST /api/drift/update`
Applies a custom actual state to a resource. Validates that `updatedState` has the same keys and types as `desiredState`.
```json
{ "resourceId": "s3_uploads", "updatedState": { "acl": "public-read", ... } }
```

### `POST /api/analyze`
Spawns the Python agent, pipes the resource as JSON to stdin, reads the analysis from stdout. Falls back to a generic TypeScript analysis if the agent fails.
```json
{ "resourceId": "s3_uploads" }
```
Returns `{ systemState, pr }`.

### `POST /api/merge-pr`
Merges a simulated PR. Requires `{ approvedBy: "name" }` for high-risk changes (classification `high_risk_change`, risk `Critical`, or IAM resource type).
```json
{ "prId": "pr_...", "approvedBy": "operator-name" }
```
- **403** if approval is required but missing.
- **404** if PR not found.
- **400** if PR already merged.

### `POST /api/reset`
Resets all resources, PRs, and timeline to initial state. Preserves alert config. Requires `X-Demo-Token` if `DEMO_TOKEN` is set.

### `POST /api/demo/reset-and-redrift`
Resets state then applies all 4 preset drifts. Requires `X-Demo-Token` if `DEMO_TOKEN` is set.

### `POST /api/resource`
Creates a custom tracked resource.
```json
{
  "name": "my_queue",
  "type": "aws_sqs_queue",
  "service": "VPC",
  "terraformCode": "resource \"aws_sqs_queue\" ...",
  "desiredState": { "name": "...", ... }
}
```
Valid services: `S3`, `VPC`, `IAM`, `RDS`.

### `POST /api/alerts/config`
Saves alerting configuration.
```json
{
  "slackWebhook": "https://...",
  "operatorEmail": "...",
  "pagerDutyKey": "...",
  "slackEnabled": true,
  "emailEnabled": true,
  "pagerDutyEnabled": false
}
```

### `POST /api/alerts/test`
Logs a simulated alert to the server console. Channel must be `slack`, `email`, or `pagerduty`.
```json
{ "resourceId": "s3_uploads", "channel": "slack" }
```

---

## Agent Pipeline

The Python agent (`agent.py`) is the core analysis engine. It accepts a JSON resource on stdin and outputs a `DriftAnalysis` object on stdout.

### Pipeline steps

| Step | Function | Input | Output |
|------|----------|-------|--------|
| 1. Classify | `classification_node` | `drift_details[].field`, `.actual` | `classification`, `risk_score` |
| 2. Security Audit | `security_analysis_node` | `name`, `type`, `drift_details` | `explanation`, `security_impact` |
| 3. Cost Estimation | `cost_estimation_node` | `risk_score` | `cost_impact` |
| 4. HCL Reconciliation | `hcl_reconciliation_node` | `terraform_code`, `drift_details` | `hcl_fix`, `hcl_diff`, `fixType` |
| 5. Policy Scan | `security_scan_node` | `hcl_fix`, `name`, `type` | `checkov_checks[]`, `checkov_summary` |

### Classification keywords

| Risk Level | Classification | Field/value contains |
|------------|---------------|---------------------|
| **Critical** | `high_risk_change` | `public`, `acl`, `cidr`, `port_22`, `0.0.0.0`, `admin`, `all_traffic` |
| **High** | `moderate_risk_change` | `encrypt`, `key`, `policy`, `password`, `tls`, `ssl`, `credentials` |
| **Medium/Low** | `low_risk_change` | anything else with drift |

The keyword lists are shared between `agent.py` and `server.ts` (`determineSeverity`). Both files have copies; keep them in sync.

### HCL diff generation

Uses Python's `difflib.unified_diff` to produce a standard unified diff between the original Terraform code and the proposed reconciliation. The `fixType: "illustrative_diff"` field signals that the output has not been validated by `terraform plan` or `terraform validate`.

### Simulated policy checks

The `security_scan_node` checks generated HCL against hardcoded rules (CKV_AWS_19, CKV_AWS_144, CKV_AWS_24, CKV_AWS_1, CKV_AWS_16, etc.). PASSED/FAILED is determined by substring matching against the HCL fix. These are illustrative — not real Checkov scans.

---

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start dev server with hot reload (tsx + Vite middleware) |
| `npm run build` | Build frontend (vite) + bundle server (esbuild) |
| `npm start` | Run the production build (`node dist/server.cjs`) |
| `npm run lint` | TypeScript type check (`tsc --noEmit`) |
| `npm run clean` | Remove build artifacts |

---

## Security

### In this demo
- **Demo token**: Set `DEMO_TOKEN` in your environment to require `X-Demo-Token` header on destructive endpoints (`/api/reset`, `/api/merge-pr`, `/api/demo/*`).
- **Rate limiting**: Mutation endpoints are rate-limited in-memory (10 req/min for reset, 20 req/min for merge).
- **Credential masking**: `/api/state` masks `slackWebhook` and `pagerDutyKey` values.
- **Input validation**: `/api/drift/update` validates state shape; `/api/resource` validates service type; JSON body size limited to 500KB.
- **Security headers**: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` set on all responses.
- **No outbound calls**: Alert integrations (`sendDriftAlertsOnDetection`) log to console only.

### Before connecting to real infrastructure
1. Replace `initialResources` with actual state pulled from an S3 backend or Terraform Cloud API.
2. Replace `getDeepDiff` with `terraform plan -json` output parsing.
3. Add DynamoDB for state persistence (the `SystemState` interface is already typed).
4. Add GitHub App installation + API client for real PR creation.
5. Wire alert integrations to actually POST to Slack/Email/PagerDuty.
6. Run generated HCL through `terraform validate` before surfacing it.
7. Add authentication (OAuth/OIDC) — the demo token is not production auth.
8. Rotate all placeholder credentials in `alertConfig`.

---

## Extending

### Adding a new resource type

1. **server.ts** — add a preset drift payload in `initialResources` and in `/api/drift` and `/api/demo/reset-and-redrift`.
2. **agent.py** — add a branch in `security_analysis_node` for the new type's explanation text.
3. **agent.py** — add Checkov rules in `security_scan_node` for the new type.
4. **src/types.ts** — add the service to the `service` union type if needed.

### Connecting real AWS

The `getDeepDiff` function in server.ts is the integration point. Replace it with a function that:
1. Fetches desired state from your Terraform state backend (S3, Terraform Cloud, etc.)
2. Fetches actual state via AWS SDK (`aws-sdk` or `@aws-sdk/client-*`)
3. Returns the diff in the existing `{ field, expected, actual, severity }[]` format

### Connecting a real LLM

The agent's `classification_node` and `security_analysis_node` have comment placeholders where Gemini calls previously lived. Re-add `urllib` or the Google GenAI SDK calls at those points. The pipeline structure (5 sequential nodes) is already in place.
