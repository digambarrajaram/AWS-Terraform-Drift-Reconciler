-- Enforce one pending_applies row per (pr_number, scope).
--
-- Duplicates were possible before this migration: the dashboard decision
-- claim flips the row to approved/rejected before GitHub delivers the
-- merged webhook, so the webhook's awaiting_approval-only dedup missed the
-- row and inserted a second one.  Keep the earliest row (the one the
-- dashboard list shows first), drop later duplicates, then enforce.

delete from pending_applies a
  using pending_applies b
  where a.pr_number = b.pr_number
    and a.scope = b.scope
    and (
      a.created_at > b.created_at
      or (a.created_at = b.created_at and a.id > b.id)
    );

create unique index if not exists pending_applies_pr_number_scope_key
  on pending_applies (pr_number, scope);
