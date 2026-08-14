-- Drop scan_role_variable from environments.
--
-- Both workflows now fetch region and aws_role_arn directly from
-- Supabase — no GitHub Variables indirection remains.  The column is
-- dead weight.

alter table environments drop column if exists scan_role_variable;
