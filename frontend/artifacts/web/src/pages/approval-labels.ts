// Pure label/toast strings for the Approvals queue, kept out of the
// component so they're testable without a React tree (node --test + tsx).
//
// File-only PRs (unmanaged/security_only) skip terraform — their "apply"
// IS the merge, so the running badge says merging, and reject just
// closes the PR (nothing reverts).  fix/batch/rollback/manual keep the
// drift wording; manual is a no-diff review PR but stays on the default
// strings until someone specifies its wording.

type ClaimStatus = 'approved' | 'rejected' | 'excepted';

const FILE_ONLY_RUNNING: Record<string, Partial<Record<'approved' | 'rejected', string>>> = {
  unmanaged: { approved: 'Merging unmanaged PR…', rejected: 'Closing…' },
  security_only: {
    approved: 'Merging security PR…',
    rejected: 'Closing…',
  },
  rollback: {
    approved: 'Applying rollback…',
    rejected: 'Cancelling rollback…',
  },
};
const DRIFT_RUNNING: Record<'approved' | 'rejected', string> = {
  approved: 'Applying drift…',
  rejected: 'Reverting drift…',
};

/** Running-badge label for a claim state, or '' when status isn't a claim.
 *  ``excepted`` is terminal (Except runs sync in the decision handler —
 *  no apply subprocess), so it must not return a running label. */
export function runningLabel(status: string, prType: string | null | undefined): string {
  if (status !== 'approved' && status !== 'rejected') return '';
  return FILE_ONLY_RUNNING[prType ?? '']?.[status] ?? DRIFT_RUNNING[status];
}

/** Terminal statuses — the job ran and finished.  'approved'/'rejected'
 * are the *claim* states the decision handler writes when the job starts,
 * so they must never read as done; the job's own final write is one of
 * the terminal values ('reverted' for a file-only reject). */
const JOB_DONE = new Set(['applied', 'failed', 'cancelled', 'reverted',
  'reverted_gate_blocked', 'manual_revert_required', 'excepted']);

/** True when the row has reached a terminal status (poller should stop). */
export function isJobDone(status: string | null | undefined): boolean {
  return status != null && JOB_DONE.has(status);
}

const FILE_ONLY_TYPES = new Set(['unmanaged', 'security_only', 'manual']);

/** Decision button label — outcome-oriented, not GitHub jargon.
 *  Drift approve: keep AWS, update Terraform + state to match.
 *  Drift reject: keep Terraform, revert AWS (+ state) to match code.
 *  Rollback approve: merge reverse patch + apply (undo prior fix).
 *  File-only: merge/close (no terraform apply). */
export function decisionButtonLabel(
  decision: ClaimStatus,
  prType: string | null | undefined,
  opts?: { compact?: boolean },
): string {
  const compact = opts?.compact === true;
  if (decision === 'excepted') {
    return compact ? 'Except' : 'Add exception';
  }
  if (prType === 'rollback') {
    if (decision === 'approved') {
      return compact ? 'Apply rollback' : 'Apply rollback';
    }
    return compact ? 'Cancel' : 'Cancel rollback';
  }
  const fileOnly = prType != null && FILE_ONLY_TYPES.has(prType);
  if (fileOnly) {
    if (decision === 'approved') {
      return compact ? 'Accept & merge' : 'Accept & merge PR';
    }
    return compact ? 'Close PR' : 'Close without changes';
  }
  if (decision === 'approved') {
    return compact ? 'Accept AWS' : 'Accept AWS changes';
  }
  return compact ? 'Revert AWS' : 'Revert AWS to code';
}

/** Success toast after a decision POST — wording matches what the backend
 * actually does for this pr_type (merge+apply vs merge+exceptions). */
export function decisionToast(
  prNumber: number,
  decision: ClaimStatus,
  prType: string | null | undefined,
  reviewOnly?: boolean | null,
): string {
  if (decision === 'excepted') {
    return `PR #${prNumber} excepted — closed without merge; exception added`;
  }
  if (prType === 'rollback') {
    return decision === 'approved'
      ? `PR #${prNumber} approved — applying rollback`
      : `PR #${prNumber} rejected — rollback cancelled`;
  }
  const fileOnly = prType != null && FILE_ONLY_TYPES.has(prType);
  if (fileOnly) {
    if (decision === 'approved') {
      // Real-fix security: merge applies the .tf patch, no exception row.
      if (prType === 'security_only' && !reviewOnly) {
        return `PR #${prNumber} approved — merged (fix applied, no exception)`;
      }
      return `PR #${prNumber} approved — merged, exceptions added`;
    }
    return `PR #${prNumber} rejected — PR closed; will resurface next scan`;
  }
  return decision === 'approved'
    ? `PR #${prNumber} approved — updating code + state to match AWS`
    : `PR #${prNumber} rejected — reverting AWS to match original code`;
}
