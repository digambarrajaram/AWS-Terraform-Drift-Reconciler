-- Track the merge commit SHA on pending_applies so a gate-failure
-- revert step knows exactly which commit to revert (-m 1 <sha>).

alter table pending_applies add column if not exists merge_commit_sha text;
