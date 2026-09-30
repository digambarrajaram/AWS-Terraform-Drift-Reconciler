/**
 * Shared status labels across Approvals (pending_applies), PR Queue, and
 * Explorer (drift_events).  The two tables use different raw vocabularies
 * for the same outcomes — these labels make Accept/Reject read the same
 * on every page.
 *
 *   Approvals `applied`  ↔  finding `resolved`  →  "Accepted"
 *   Approvals `reverted` ↔  finding `reverted`  →  "Reverted"
 */

/** drift_events.status — finding / PR-queue / explorer */
export function findingStatusLabel(status: string): string {
  switch (status) {
    case 'open':
      return 'Open';
    case 'resolved':
      return 'Accepted';
    case 'reverted':
      return 'Reverted';
    case 'suppressed':
      return 'Suppressed';
    case 'manual_revert_required':
      return 'Manual action needed';
    case 'reverted_gate_blocked':
      return 'Reverted (gate blocked)';
    default:
      return status.replace(/_/g, ' ');
  }
}

/** pending_applies.status — Approvals queue */
export function applyStatusLabel(
  status: string,
  opts?: { mergedAt?: string | null },
): string {
  if (status === 'applied' && !opts?.mergedAt) {
    return 'Accepted, not merged';
  }
  switch (status) {
    case 'awaiting_approval':
      return 'Awaiting approval';
    case 'approved':
      return 'Accepted';
    case 'rejected':
      return 'Rejected';
    case 'applied':
      return 'Accepted';
    case 'reverted':
      return 'Reverted';
    case 'excepted':
      return 'Excepted';
    case 'failed':
      return 'Failed';
    case 'cancelled':
      return 'Cancelled';
    case 'reverted_gate_blocked':
      return 'Reverted (gate blocked)';
    case 'manual_revert_required':
      return 'Manual action needed';
    default:
      return status.replace(/_/g, ' ');
  }
}
