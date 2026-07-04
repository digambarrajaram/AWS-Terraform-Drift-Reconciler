# AWS Terraform Drift Reconciler — End-to-End Test Checklist

Scope reminder: Infrastructure/Resource drift only (Terraform-managed AWS resources vs. live state). Every scenario below uses a REAL manual change via AWS Console/CLI — no scripts, no injection endpoints, per CONVENTIONS.md.

For every scenario: **make the change → Scan Env → verify DRIFTED with a real (non-empty) diff → run Agent Reconciliation → review PR → approve/reject → verify remediation → revert manually → rescan → verify COMPLIANT.**

---

## 1. Detection — Security Group (`aws_security_group.ec2`)

- [] 1.1 Change an ingress rule port (e.g. 22 → 8080) — *already verified working*
- [ ] 1.2 Change an ingress rule CIDR block (e.g. restrict `0.0.0.0/0` → a specific IP)
- [ ] 1.3 Add a brand-new ingress rule not in Terraform config at all
- [ ] 1.4 Delete an existing rule that Terraform expects to exist
- [ ] 1.5 Change the security group **description** field
- [ ] 1.6 Change/add a **tag** on the security group (confirm whether `ignore_changes` lifecycle applies — check your `.tf` first)
- [ ] 1.7 Change an egress rule's protocol (e.g. `tcp` → `all`)

## 2. Detection — IAM Policy (`aws_iam_policy.drift_reconciler_dynamodb`)

- [ ] 2.1 Add a new `Action` to the policy statement (e.g. add `dynamodb:DeleteTable`)
- [ ] 2.2 Change `Effect` from `Allow` to `Deny` on a statement
- [ ] 2.3 Add a completely new statement block
- [ ] 2.4 Change the `Resource` ARN to a wildcard (`*`)
- [ ] 2.5 Change the policy `description` field

## 3. Detection — DynamoDB Table (`aws_dynamodb_table.drift_audit`)

- [ ] 3.1 Toggle Point-in-Time Recovery off (currently `enabled: true` in desired state)
- [ ] 3.2 Change billing mode (`PAY_PER_REQUEST` → `PROVISIONED`, if your account allows it — note this is a disruptive/replacement-triggering change, test cautiously)
- [ ] 3.3 Add/remove a tag
- [ ] 3.4 Disable server-side encryption

## 4. Detection — EC2 Instance (`aws_instance.demo_server`)

- [ ] 4.1 Change the instance type via console (e.g. resize) — this **is** a configurable attribute, should be caught
- [ ] 4.2 Add/change a tag
- [ ] 4.3 Modify `user_data` if editable post-launch (may require stop first — check provider docs)
- [ ] 4.4 **Negative test:** stop/start the instance — confirm it does **NOT** show as drifted (per confirmed scope boundary: power state is a computed attribute, out of scope)

## 5. Detection — VPC Security Group Rules (`aws_vpc_security_group_egress_rule.*`, `ingress_rule.*`)

- [ ] 5.1 Change `from_port`/`to_port` on the bastion ingress rule
- [ ] 5.2 Change the referenced security group ID
- [ ] 5.3 Delete one of the standalone rule resources entirely from AWS (tests "resource missing" drift, not just attribute drift)
- [ ] 5.4 Change a rule's description tag

## 6. Detection — Edge Cases & Negative Tests

- [ ] 6.1 **No drift baseline:** confirm a totally clean scan shows 0 drifted, 7 compliant (regression check — re-run after every category above once reverted)
- [ ] 6.2 **Multiple simultaneous drifts:** change 3 different resources at once, confirm all 3 (and only those 3) show drifted
- [ ] 6.3 **Resource deleted entirely from AWS** (not just modified) — does the tool report this distinctly from an attribute change, or crash/misreport?
- [ ] 6.4 **New resource created in AWS but not in Terraform config at all** (unmanaged resource) — confirm expected behavior: should this show up at all, given your resource list is Terraform-state-driven? Worth deciding if "unmanaged resource" detection is in scope or explicitly excluded.
- [ ] 6.5 **Terraform state file itself is stale/out of sync with S3** — force a mismatch, confirm the app doesn't silently trust bad state
- [ ] 6.6 **AWS credentials revoked mid-scan** — confirm graceful fallback (EC2 describe/deep-diff path), not a crash
- [ ] 6.7 **terraform binary unavailable** — confirm fallback to EC2 describe + deep-diff, no 502 (this was the earlier fix — regression-test it)

---

## 7. Agent Reconciliation — Analysis Quality

For at least 2-3 of the confirmed drifts above:

- [ ] 7.1 Run "Run Agent Reconciliation" — confirm it completes without error for both `AGENT_MODE=deterministic` and `AGENT_MODE=nova` (if wired up)
- [ ] 7.2 Confirm risk classification is sensible (e.g. open SSH to `0.0.0.0/0` → Critical; a tag change → Low)
- [ ] 7.3 Confirm the generated HCL diff reflects the REAL change (from earlier fix — verify it's not showing empty `{}` anymore)
- [ ] 7.4 Confirm `fixType` is honestly labeled (`unapproved_recommendation`, not a false "validated" claim)
- [ ] 7.5 Confirm Checkov/policy check results are either real CLI output or clearly labeled `heuristic_*` — no fake `CKV_AWS_*` IDs
- [ ] 7.6 Test a resource type with **no explicit handling** in `security_analysis_node` — confirm graceful generic fallback, not a crash

## 8. PR Workflow

- [ ] 8.1 Confirm a real GitHub PR is created (not just an in-memory record) — check your actual repo
- [ ] 8.2 Confirm PR idempotency: run analyze twice on the same drift — does it create a duplicate PR or reuse the existing one (per the SHA256 dedup logic found in the earlier audit)?
- [ ] 8.3 **Low/Moderate risk merge:** confirm it merges without requiring `approvedBy`
- [ ] 8.4 **Critical/High risk merge:** confirm it's blocked without `approvedBy`, succeeds with it
- [ ] 8.5 Confirm `API_ACCESS_TOKEN` gating works — try merge/reject without the token header, confirm rejection
- [ ] 8.6 Confirm `/api/merge-pr/reject` requires a `reason` of minimum length (per earlier audit finding) and logs `rejectedBy`
- [ ] 8.7 Confirm audit trail entries are actually written to DynamoDB for: PR created, PR merged, PR rejected, reset
- [ ] 8.8 Confirm merging does **NOT** run `terraform apply` — verify actual AWS state is unchanged after merge, only the reconciliation record updates (per the disclaimer added earlier)

## 9. Alerting

- [ ] 9.1 Confirm PagerDuty alert fires on new drift detection (dedup key working — same drift shouldn't re-alert every scan)
- [ ] 9.2 Confirm alert severity maps correctly (Critical drift → Critical PagerDuty urgency, if configured)
- [ ] 9.3 Confirm no Slack/Email code paths exist or fire (per earlier cleanup)
- [ ] 9.4 Test `/api/alerts/test` endpoint — confirm it triggers a real test alert

## 10. Dashboard / UI Integrity

- [ ] 10.1 Confirm resource counts (Managed/Drifted/Open PRs) update correctly after each scan
- [ ] 10.2 Confirm Timeline tab logs each scan/PR/merge/reject event with correct timestamps
- [ ] 10.3 Confirm account ID masking works (`/api/state/unmask` toggle) — check ARNs are masked by default
- [ ] 10.4 Confirm `/health`, `/ready`, `/metrics` endpoints return sensible data under real load (not just empty stubs)
- [ ] 10.5 Confirm "Reset to Compliant" button correctly restores in-memory state without needing a real AWS revert (this only resets the app's tracking, not real infra — confirm the UI doesn't imply otherwise)

## 11. Documentation Consistency (final pass)

- [ ] 11.1 Every endpoint in the API reference actually exists and behaves as documented
- [ ] 11.2 Every env var actually used in code is documented with correct default
- [ ] 11.3 CONVENTIONS.md scope statement matches what you'd tell an interviewer, word for word in spirit
- [ ] 11.4 README's "What this is" section has zero remaining "demo"/"simulated" language

---

## Suggested order of execution

1. Section 6.1 (clean baseline) first — establish your known-good starting point.
2. Sections 1–5, one resource type at a time, full cycle each (drift → scan → reconcile → revert → rescan clean) before moving to the next.
3. Section 6.2–6.7 (edge cases) — do these deliberately, they're the ones most likely to surface real bugs.
4. Section 7 (agent quality) — pick from your confirmed drifts in 1–5.
5. Section 8 (PR workflow) — needs at least one Critical and one Low risk drift to test both approval paths.
6. Section 9 (alerting) — can run in parallel with 8.
7. Section 10 (UI) — ongoing observation throughout, formal pass at the end.
8. Section 11 (docs) — last, once everything above is confirmed working.
