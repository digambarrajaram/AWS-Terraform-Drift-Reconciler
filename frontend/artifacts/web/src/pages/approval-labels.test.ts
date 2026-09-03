// Badge/toast wording per pr_type — the user-facing-string audit.  The
// pure functions live in approval-labels.ts so this needs no React tree.
// Run: node --import tsx --test src/pages/approval-labels.test.ts
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { runningLabel, decisionButtonLabel, decisionToast, isJobDone } from './approval-labels';

describe('runningLabel', () => {
  it('keeps drift wording for fix/batch/manual/null', () => {
    assert.equal(runningLabel('approved', 'fix'), 'Applying drift…');
    assert.equal(runningLabel('rejected', 'batch'), 'Reverting drift…');
    assert.equal(runningLabel('approved', 'manual'), 'Applying drift…');
    assert.equal(runningLabel('approved', null), 'Applying drift…');
  });

  it('says merging/closing for file-only types', () => {
    assert.equal(runningLabel('approved', 'unmanaged'), 'Merging unmanaged PR…');
    assert.equal(runningLabel('rejected', 'unmanaged'), 'Closing…');
    assert.equal(runningLabel('approved', 'security_only'), 'Merging security PR…');
    assert.equal(runningLabel('rejected', 'security_only'), 'Closing…');
  });

  it('says applying/cancelling for rollback', () => {
    assert.equal(runningLabel('approved', 'rollback'), 'Applying rollback…');
    assert.equal(runningLabel('rejected', 'rollback'), 'Cancelling rollback…');
  });

  it('returns empty for non-claim statuses', () => {
    assert.equal(runningLabel('applied', 'unmanaged'), '');
    assert.equal(runningLabel('awaiting_approval', 'fix'), '');
    // excepted is terminal (sync Except) — never a running badge
    assert.equal(runningLabel('excepted', 'security_only'), '');
  });
});

describe('decisionToast', () => {
  it('keeps drift wording for terraform types', () => {
    assert.equal(decisionToast(7, 'approved', 'fix'), 'PR #7 approved — updating code + state to match AWS');
    assert.equal(decisionToast(7, 'rejected', 'batch'), 'PR #7 rejected — reverting AWS to match original code');
    assert.equal(decisionToast(7, 'rejected', null), 'PR #7 rejected — reverting AWS to match original code');
  });

  it('uses merge/exceptions wording for file-only types', () => {
    // unmanaged always auto-excepts on merge.  security review_only no
    // longer offers Approve — Except is the suppress path.
    for (const t of ['unmanaged', 'manual']) {
      assert.equal(decisionToast(7, 'approved', t), 'PR #7 approved — merged, exceptions added');
      assert.equal(decisionToast(7, 'rejected', t), 'PR #7 rejected — PR closed; will resurface next scan');
    }
    assert.equal(
      decisionToast(7, 'rejected', 'security_only'),
      'PR #7 rejected — PR closed; will resurface next scan',
    );
  });

  it('uses rollback wording', () => {
    assert.equal(decisionToast(7, 'approved', 'rollback'), 'PR #7 approved — applying rollback');
    assert.equal(decisionToast(7, 'rejected', 'rollback'), 'PR #7 rejected — rollback cancelled');
  });
});

describe('decisionButtonLabel', () => {
  it('uses outcome wording for drift types', () => {
    assert.equal(decisionButtonLabel('approved', 'fix'), 'Accept AWS changes');
    assert.equal(decisionButtonLabel('rejected', 'batch'), 'Revert AWS to code');
    assert.equal(decisionButtonLabel('approved', null, { compact: true }), 'Accept AWS');
    assert.equal(decisionButtonLabel('rejected', 'fix', { compact: true }), 'Revert AWS');
  });

  it('uses apply/cancel wording for rollback', () => {
    assert.equal(decisionButtonLabel('approved', 'rollback'), 'Apply rollback');
    assert.equal(decisionButtonLabel('rejected', 'rollback'), 'Cancel rollback');
    assert.equal(decisionButtonLabel('approved', 'rollback', { compact: true }), 'Apply rollback');
    assert.equal(decisionButtonLabel('rejected', 'rollback', { compact: true }), 'Cancel');
  });

  it('uses merge/close wording for file-only types', () => {
    assert.equal(decisionButtonLabel('approved', 'unmanaged'), 'Accept & merge PR');
    assert.equal(decisionButtonLabel('rejected', 'security_only'), 'Close without changes');
    assert.equal(decisionButtonLabel('approved', 'manual', { compact: true }), 'Accept & merge');
    assert.equal(decisionButtonLabel('rejected', 'unmanaged', { compact: true }), 'Close PR');
  });

  it('labels except clearly', () => {
    assert.equal(decisionButtonLabel('excepted', 'security_only'), 'Add exception');
    assert.equal(decisionButtonLabel('excepted', 'security_only', { compact: true }), 'Except');
  });
});
describe('isJobDone', () => {
  it('claim states are never done — the rejected-as-terminal collision', () => {
    assert.equal(isJobDone('approved'), false);
    assert.equal(isJobDone('rejected'), false);
    assert.equal(isJobDone('awaiting_approval'), false);
  });

  it('terminal statuses are done', () => {
    for (const s of ['applied', 'failed', 'cancelled', 'reverted',
      'reverted_gate_blocked', 'manual_revert_required', 'excepted']) {
      assert.equal(isJobDone(s), true);
    }
  });
});

describe('decisionToast — security real-fix vs review_only', () => {
  it('real-fix merge does not claim exceptions were added', () => {
    assert.equal(
      decisionToast(7, 'approved', 'security_only', false),
      'PR #7 approved — merged (fix applied, no exception)',
    );
  });

  it('review_only uses Except (not merge) — toast for excepted', () => {
    assert.equal(
      decisionToast(7, 'excepted', 'security_only', true),
      'PR #7 excepted — closed without merge; exception added',
    );
  });

  it('excepted closes without merge and adds exception', () => {
    assert.equal(
      decisionToast(7, 'excepted', 'security_only', false),
      'PR #7 excepted — closed without merge; exception added',
    );
  });
});

describe('isJobDone — excepted is terminal', () => {
  it('excepted is done', () => {
    assert.equal(isJobDone('excepted'), true);
  });
});

