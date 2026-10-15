/** Pending apply row — minimal shape for poll-interval logic. */
export interface PendingApplyPollRow {
  status: string;
  approved_at?: string | null;
  created_at?: string;
}

const FAST_POLL_MS = 3_000;
const SLOW_POLL_MS = 30_000;
/** Stop fast-polling claimed rows that never reach a terminal status. */
const CLAIM_FAST_POLL_MAX_MS = 2 * 60 * 60 * 1000;

export function isPendingApplyJobInFlight(row: PendingApplyPollRow): boolean {
  if (row.status !== 'approved' && row.status !== 'rejected') return false;
  const ts = row.approved_at ?? row.created_at;
  // Missing timestamps are stale rows — do not fast-poll forever (backend sync lag).
  if (!ts) return false;
  const age = Date.now() - new Date(ts).getTime();
  if (!Number.isFinite(age) || age < 0) return false;
  return age < CLAIM_FAST_POLL_MAX_MS;
}

export function pendingAppliesRefetchInterval(
  rows: PendingApplyPollRow[] | undefined,
): number {
  if (rows?.some(isPendingApplyJobInFlight)) return FAST_POLL_MS;
  return SLOW_POLL_MS;
}
