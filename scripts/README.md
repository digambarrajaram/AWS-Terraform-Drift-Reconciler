# Drift guardrails

These scripts implement lightweight, safe drift checks that can be run locally or in CI.

- `check-infra-drift.mjs` runs Terraform plan and fails on drift.
- `check-operational-drift.mjs` checks EC2 instance state against the operational baseline.
- `check-config-drift.mjs` hashes a known configuration baseline and fails on mismatches.
- `check-application-drift.mjs` validates that the release metadata follows the GitOps branch and digest policy.
- `check-environment-drift.mjs` validates environment parity markers.
- `check-schema-drift.mjs` validates migration guardrails using simple content checks.

These checks are intended to be idempotent and do not mutate infrastructure. They only report drift or block the pipeline until it is reviewed and remediated.
