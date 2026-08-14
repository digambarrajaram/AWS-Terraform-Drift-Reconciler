-- Add scan_role_arn to environments — a separate, optionally less-privileged
-- role for the read-only preview/scan workflow.  NULL means "fall back to
-- aws_role_arn" (the apply role).

alter table environments add column if not exists scan_role_arn text;
