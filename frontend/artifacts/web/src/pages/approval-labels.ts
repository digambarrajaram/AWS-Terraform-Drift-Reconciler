// Pure label/toast strings for the Approvals queue, kept out of the
// component so they're testable without a React tree (node --test + tsx).
//
// File-only PRs (unmanaged/security_only) skip terraform — their "apply"
// IS the merge, so the running badge says merging, and reject just
// closes the PR (nothing reverts).  fix/batch/rollback/manual keep the
// drift wording; manual is a no-diff review PR but stays on the default
// strings until someone specifies its wording.

type ClaimStatus = 'approved' | 'rejected';

const FILE_ONLY_RUNNING: Record<string, Partial<Record<ClaimStatus, string>>> = {
  unmanaged: { approved: 'Merging unmanaged PR…', rejected: 'Closing…' },
  security_only: { approved: 'Merging security PR…', rejected: 'Closing…' },
};
const DRIFT_RUNNING: Record<ClaimStatus, string> = {
  approved: 'Applying drift…',
  rejected: 'Reverting drift…',
};

/** Running-badge label for a claim state, or '' when status isn't a claim. */
export function runningLabel(status: string, prType: string | null | undefined): string {
  if (status !== 'approved' && status !== 'rejected') return '';
  return FILE_ONLY_RUNNING[prType ?? '']?.[status] ?? DRIFT_RUNNING[status];
}

/** Terminal statuses — the job ran and finished.  'approved'/'rejected'
 * are the *claim* states the decision handler writes when the job starts,
 * so they must never read as done; the job's own final write is one of
 * the terminal values ('reverted' for a file-only reject). */
const JOB_DONE = new Set(['applied', 'failed', 'cancelled', 'reverted',
  'reverted_gate_blocked', 'manual_revert_required']);

/** True when the row has reached a terminal status (poller should stop). */
export function isJobDone(status: string | null | undefined): boolean {
  return status != null && JOB_DONE.has(status);
}

const FILE_ONLY_TYPES = new Set(['unmanaged', 'security_only', 'manual']);

/** Success toast after a decision POST — wording matches what the backend
 * actually does for this pr_type (merge+apply vs merge+exceptions). */
export function decisionToast(
  prNumber: number,
  decision: ClaimStatus,
  prType: string | null | undefined,
): string {
  const fileOnly = prType != null && FILE_ONLY_TYPES.has(prType);
  return fileOnly
    ? decision === 'approved'
      ? `PR #${prNumber} approved — merged, exceptions added`
      : `PR #${prNumber} rejected — PR closed; will resurface next scan`
    : decision === 'approved'
      ? `PR #${prNumber} approved — merge + apply started`
      : `PR #${prNumber} rejected — close + revert started`;
}
