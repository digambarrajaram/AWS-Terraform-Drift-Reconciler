# CONVENTIONS.md � AWS Terraform Drift Reconciler

This file documents the current implementation, operational expectations, and security conventions for this repository. It is intentionally aligned to the code that runs today, not legacy demo language.

## Current project reality

This repo is built around real integrations when configured:

- Terraform state is read from S3 in `src/integrations/aws.ts`.
- Audit events are persisted to DynamoDB when AWS credentials are available.
- Drift detection prefers `terraform plan -json` and treats cloud changes as real drift. It does not generate drift from the dashboard or API.
- GitHub PR creation is real when `GITHUB_TOKEN` and `GITHUB_REPO` are set.
- PagerDuty is the only alerting integration implemented in server-side flow.
- `systemState` in `server.ts` is an in-memory UI/runtime store, not a fake drift source.

## What this project is not

- It is not a purely simulated demo.
- It does not inject drift through the UI or API.
- It does not fake outbound AWS, GitHub, or PagerDuty behavior when the real integrations are configured.
- `API_ACCESS_TOKEN` is a real security control on destructive and state-mutation endpoints.
- HCL reconciliation suggestions are not authoritative unless validation actually ran.

## Tech stack

- Frontend: React + Vite + Tailwind
- Backend: Express server in `server.ts`
- Agent: Python agents invoked from Node subprocesses
- Integrations: AWS S3, DynamoDB, EC2, GitHub, PagerDuty, Terraform CLI

## Key conventions

- Prefer real integration paths when the corresponding environment and binaries are available.
- Explicitly label fallback behavior when a real integration cannot be used.
- Never add fake drift creation into the dashboard or API.
- Protect destructive and persistent state mutations with `API_ACCESS_TOKEN`.
- PagerDuty is the main alerting integration; do not assume Slack or email alerts are supported unless those integrations are added.
- HCL fixes must be labeled `fixType: "unapproved_recommendation"` unless a real validation tool actually executed.
- Attempt a real Checkov CLI scan before falling back to heuristic policy checks.

## Agent semantics

### `agent.py`

- Uses deterministic keyword classification and risk scoring.
- Generates proposed HCL fixes via `difflib.unified_diff`.
- Marks generated HCL proposals as `fixType: "unapproved_recommendation"`.
- Attempts to run the real `checkov` CLI against proposed HCL.
- If the `checkov` CLI is unavailable or fails, falls back to heuristic checks.
- Heuristic fallback results must use `heuristic_*` IDs and `source: "keyword_matching"`.
- Never claim CLI validation unless the real tool completed successfully.

### `agent_nova.py`

- Uses Amazon Bedrock Nova Pro for classification and analysis when available.
- Falls back to deterministic keyword-mode when Bedrock or boto3 is unavailable.
- Also attempts a real `checkov` CLI scan before using heuristic fallbacks.
- Keeps the same `unapproved_recommendation` HCL semantics as `agent.py`.

## API conventions

- Read-only endpoints may remain ungated.
- Mutating and destructive endpoints require `API_ACCESS_TOKEN` via the `X-Api-Access-Token` header.
- Gated endpoints include:
  - `POST /api/merge-pr`
  - `POST /api/merge-pr/reject`
  - `POST /api/reset`
  - `POST /api/state/unmask`
  - `POST /api/resource`
  - `POST /api/alerts/config`
- `POST /api/alerts/test` is intentionally left ungated because it only tests alert delivery and does not mutate persistent server state.
- Destructive actions should continue to require request metadata when appropriate (`approvedBy`, `requestedBy`).

## Data and security conventions

- `GET /api/state` must not expose credential-like values.
- `API_ACCESS_TOKEN` is a real access control guard, not a demo flag.
- `approvedBy` / `requestedBy` are audit metadata only and are not authentication.

## Environment conventions

Important environment variables for current runtime behavior:

- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` � required for real AWS calls.
- `TF_STATE_BUCKET`, `TF_STATE_KEY` � S3 Terraform state location.
- `TERRAFORM_DIR` � local Terraform working directory used for `.tf` enrichment and for `terraform plan`. Defaults to `./terraform/ec2` for plan execution and `./terraform` for HCL enrichment.
- `TERRAFORM_PATH` � explicit path to the Terraform binary. If unset, the server searches PATH and common platform locations, then falls back to `terraform`.
- `SCAN_HEARTBEAT_MS` � scheduler/health heartbeat interval; defaults to `3600000` (1 hour).
- `GITHUB_TOKEN`, `GITHUB_REPO`, `GITHUB_BRANCH` � required for real GitHub PR creation.
- `PAGERDUTY_ROUTING_KEY` � required for real PagerDuty alerting. Without it, alerts may fall back to local logging.

## Development workflow

- Keep changes small and aligned to one implementation goal.
- When changing agent behavior, update both `agent.py` and `agent_nova.py` if the behavior should remain consistent.
- If risk keyword sets change, update server-side severity logic in `server.ts` as well.

## What not to do

- Do not add fake drift creation in the UI or API.
- Do not treat in-memory `systemState` as simulated infrastructure.
- Do not label proposed HCL fixes as validated unless a real validation tool ran.
- Do not remove `API_ACCESS_TOKEN` from destructive endpoints without replacing it with equivalent or stronger access control.

## Drift scope boundary

This project detects Infrastructure/Resource drift ONLY: Terraform-managed AWS resources versus live state via `terraform plan` and AWS SDK inspection. Explicitly out of scope: OS/config drift (needs Ansible/Chef), application/code drift (needs CI/CD tooling), environmental drift (dev/staging/prod comparison), and data/schema drift (needs Liquibase/Flyway-class tooling). Do not add detection logic for these categories — they require fundamentally different data sources and tools, not extensions of this pipeline.
