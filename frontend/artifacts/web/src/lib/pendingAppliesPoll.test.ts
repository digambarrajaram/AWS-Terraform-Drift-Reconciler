import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  isPendingApplyJobInFlight,
  pendingAppliesRefetchInterval,
} from './pendingAppliesPoll';

describe('pendingAppliesPoll', () => {
  it('fast-polls only claimed rows within the claim window', () => {
    const recent = new Date(Date.now() - 60_000).toISOString();
    assert.equal(
      pendingAppliesRefetchInterval([{ status: 'approved', approved_at: recent }]),
      3_000,
    );
    assert.equal(
      pendingAppliesRefetchInterval([{ status: 'awaiting_approval' }]),
      30_000,
    );
  });

  it('stops fast-poll for stale claimed rows', () => {
    const old = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
    assert.equal(isPendingApplyJobInFlight({ status: 'approved', approved_at: old }), false);
    assert.equal(
      pendingAppliesRefetchInterval([{ status: 'approved', approved_at: old }]),
      30_000,
    );
  });

  it('does not fast-poll claimed rows without a timestamp', () => {
    assert.equal(isPendingApplyJobInFlight({ status: 'approved' }), false);
    assert.equal(
      pendingAppliesRefetchInterval([{ status: 'rejected' }]),
      30_000,
    );
  });
});
