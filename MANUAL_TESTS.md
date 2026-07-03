# Manual Test Guide — AWS Terraform Drift Reconciler

This document lists quickstart steps and manual test scenarios that exercise the project's main features: detecting drift via `terraform plan`, scanning, reviewing attribute-level diffs in the UI, analyzing drift with the AI agent (PR generation), and merging PRs to reconcile state.

## Quickstart

1. Install dependencies and start the dev server

```bash
npm install
npm run dev
```

Server runs on http://localhost:3000 by default.

2. Open the UI

- Visit http://localhost:3000 in your browser. The app fetches `/api/state` on load.

3. Environment notes

- Ensure `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are set if you want live AWS checks. Otherwise the app runs in demo/simulated mode.
- Terraform operations run from `terraform/ec2` by default. You can override with `TERRAFORM_DIR` env var.

## Core endpoints (useful for manual tests)

- GET `/api/state` — returns the current `systemState` (resources, PRs, timeline).
- POST `/api/scan` — triggers a drift scan (runs `terraform plan -json` by default, falls back to EC2 describe).
- POST `/api/analyze` — body: `{ resourceId }` — runs the AI agent and generates a PR (requires `API_ACCESS_TOKEN`).
- POST `/api/merge-pr` — body: `{ prId, approvedBy }` — merges a PR (requires `API_ACCESS_TOKEN`).
- POST `/api/resource` — create a custom tracked resource (requires `API_ACCESS_TOKEN`).
- POST `/api/reset` — reset all resources to desired state (requires `API_ACCESS_TOKEN`).

## Scenario A — Real terraform-based drift (recommended)

This scenario shows a true terraform->AWS drift workflow.

1. Make a change in `terraform/` that produces a drift. Example: change a security group's ingress port from 22 to 8080 in your `.tf` file.

2. Run terraform plan in the repo's terraform folder (the server's plan runner will do this for scans; you can preview manually):

```powershell
cd terraform/ec2
terraform init
terraform plan -out=plan.tfplan
terraform show -json plan.tfplan > plan.json
```

3. Trigger a scan (server will run `terraform plan -json` automatically if Terraform is available):

```bash
curl -s -X POST http://localhost:3000/api/scan -H "Content-Type: application/json" -d '{}'
```

Expected results:
- The server `systemState.resources` should show the affected resource with `isDrifted: true`.
- `desiredState` and `actualState` should contain attribute-level values — if plan events left `before`/`after` empty the server will fill them from `changedFields` so reviewers see fields like `from_port: 22 → 8080` (attribute keys and values).
- The UI resource panel (select a resource) will show JSON for `desiredState` and `actualState` with changed fields.

## Scenario B — Simulated drift using `terraform/ec2/plan.json`

If you don't have Terraform or don't want to modify real HCL, the repository contains sample plan output in `terraform/ec2/plan.json`. You can:

- Ensure the server is running, then run `POST /api/scan`. The server's plan parser will consume the live `terraform plan -json` output when it runs `terraform plan`; to simulate without executing terraform, temporarily set `TERRAFORM_DIR` to a folder containing a `plan.json` file — or run terraform locally to produce the plan.json.

## Scenario C — Quick simulated resource drift (fast path)

1. Create a custom resource with a desired state:

```bash
curl -s -X POST http://localhost:3000/api/resource \
  -H "Content-Type: application/json" \
  -H "X-Api-Access-Token: <YOUR_API_TOKEN>" \
  -d '{"name":"demo-sg","type":"aws_security_group","service":"VPC","terraformCode":"resource ...","desiredState":{"from_port":22,"cidr_blocks":["10.0.0.0/24"]}}'
```

2. Simulate drift by either (a) updating live infra outside Terraform so the server's EC2 describe returns a different `actualState`, or (b) run a `terraform plan` that reports `resource_drift` for that resource. The server's fallback describe will populate `actualState` for known resource types.

## Scenario D — Analyze with AI and create PR

1. Ensure `API_ACCESS_TOKEN` and (optionally) GitHub credentials (`GITHUB_REPO`, `GITHUB_TOKEN`) are set in your environment.
2. From the UI click the 'Agent Reconciliation' / 'Analyze' button for a drifted resource or call the API:

```bash
curl -s -X POST http://localhost:3000/api/analyze \
  -H "Content-Type: application/json" \
  -H "X-Api-Access-Token: <YOUR_API_TOKEN>" \
  -d '{"resourceId":"<RESOURCE_ID>"}'
```

3. Expected outcomes:
- The server runs the agent (Python script) and returns a `PullRequest` object in `systemState.prs`.
- `analysis.hclFix` contains the agent-proposed HCL reconciliation (illustrative patch).
- The server persists the PR in-memory and (if GitHub configured) attempts to open a real PR via the GitHub API.

## Scenario E — Merge PR (apply reconciliation)

1. Open the PR in the UI (or use the returned PR id). Approve if required for high-risk PRs.
2. Merge via API (requires `API_ACCESS_TOKEN`):

```bash
curl -s -X POST http://localhost:3000/api/merge-pr \
  -H "Content-Type: application/json" \
  -H "X-Api-Access-Token: <YOUR_API_TOKEN>" \
  -d '{"prId":"<PR_ID>","approvedBy":"qa-tester"}'
```

3. Expected outcome:
- The server marks PR as `Merged` and reconciles `actualState` → `desiredState` in-memory for the resource (simulate apply).
- `isDrifted` becomes `false`, and `driftDetails` are cleared.

## Verification checklist

- [ ] After `POST /api/scan`, drifted resources show `isDrifted: true`.
- [ ] `desiredState` and `actualState` contain attribute keys and values (not `{}`), or at least the changed fields derived from `changedFields`.
- [ ] Clicking 'Analyze' produces a PR object containing `analysis.hclFix` and `analysis.validationStatus`.
- [ ] Merging the PR clears the drift (`isDrifted: false`) and updates `actualState` to match `desiredState`.

## Troubleshooting

- If `desiredState`/`actualState` remain empty for drifted resources:
  - Confirm the server parsed `terraform plan -json` correctly; check server logs for parse errors.
  - Confirm Terraform is installed and `TERRAFORM_DIR` points to a valid terraform project.
  - Inspect `plan.json` output and ensure `resource_changes` or `resource_drift` entries include either `change.before`/`change.after` or `changedFields` entries (the server derives desired/actual from `changedFields` when before/after are empty).

- If AI analysis fails:
  - Check the Python agent output in server logs; ensure `python` or `python3` is in PATH and the agent scripts (`agent.py`, `agent_nova.py`) are present and executable.
  - If GitHub PRs are not created, verify `GITHUB_TOKEN` and `GITHUB_REPO` env vars.

## Example sequence (copyable)

Start server:

```bash
npm run dev
```

Trigger scan (bash):

```bash
curl -s -X POST http://localhost:3000/api/scan -H "Content-Type: application/json" -d '{}'
```

List state:

```bash
curl -s http://localhost:3000/api/state | jq '.'
```

Analyze resource (replace placeholders):

```bash
curl -s -X POST http://localhost:3000/api/analyze \
  -H "Content-Type: application/json" \
  -H "X-Api-Access-Token: ${API_TOKEN}" \
  -d '{"resourceId":"aws_security_group_ec2"}'
```

Merge PR (replace PR ID):

```bash
curl -s -X POST http://localhost:3000/api/merge-pr \
  -H "Content-Type: application/json" \
  -H "X-Api-Access-Token: ${API_TOKEN}" \
  -d '{"prId":"pr_...","approvedBy":"qa"}'
```

## Notes & tips

- Use `POST /api/resource` to create small, self-contained test resources if you prefer not to run Terraform.
- The server writes plan event dumps to `/tmp/plan-events-<timestamp>.json` for debugging the plan parsing logic — check server logs for the file path.
- High-risk PRs may require manual approval (API returns `requiresApproval: true`).

---

If you'd like, I can also:

- Add this file into the repository (I already created `MANUAL_TESTS.md`).
- Create a small checklist in `README.md` linking to this file.
- Add one or two automated unit tests that assert `changedFields` are transformed into `desiredState`/`actualState` maps.

Which of those should I do next?