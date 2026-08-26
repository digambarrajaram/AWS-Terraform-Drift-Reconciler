-- Security PRs record the (resource_address, rule_id) pairs they fix on
-- the pending_applies row, so a successful merge can auto-add security
-- exceptions for exactly those findings — no separate manual entry.
alter table pending_applies add column if not exists fixes_jsonb jsonb;
