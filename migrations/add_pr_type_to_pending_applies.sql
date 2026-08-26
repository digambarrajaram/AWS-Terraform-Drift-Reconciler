-- Add pr_type to pending_applies so the approval queue can label and
-- filter PR kinds (fix/batch/unmanaged/security_only) — without it every
-- row looks the same and security/unmanaged PRs are indistinguishable.
ALTER TABLE pending_applies ADD COLUMN IF NOT EXISTS pr_type TEXT;
