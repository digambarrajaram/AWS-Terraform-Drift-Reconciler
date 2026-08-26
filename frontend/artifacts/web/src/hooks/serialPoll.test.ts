// Regression guards for the log-stream poller:
// - requests never overlap (slow responses can't pile up, and a working
//   request is never canceled by the next tick — the scan-terminal bug)
// - stops once a page reports complete
// - does one final fetch when the caller flips inactive, then stops
// - retries after a transient error instead of dying
// - complete:true with an empty final page keeps prior lines (Approvals
//   drawer used to look wiped when "Job finished" appeared)
//
// Run: ../../scripts/node_modules/.bin/tsx --test src/hooks/serialPoll.test.ts
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { serialPoll, mergePollLines } from './serialPoll';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe('serialPoll', () => {
  it('never runs two fetches at once — the cancel-loop regression', async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    let calls = 0;
    const seen: number[] = [];
    // Pages take 30 ms but the interval is 5 ms: an interval+abort poller
    // would overlap/cancel every request; serialized polling must not.
    await serialPoll({
      fetchPage: async (offset) => {
        calls++;
        inFlight++;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await sleep(30);
        inFlight--;
        const done = calls >= 3;
        return { lines: done ? [{ n: offset }] : [], complete: done };
      },
      isActive: () => true,
      isStopped: () => false,
      intervalMs: 5,
      onLines: (l) => seen.push(...l.map((x) => x.n)),
      onComplete: () => {},
    });
    assert.equal(maxInFlight, 1, 'requests must never overlap');
    assert.equal(calls, 3);
    assert.deepEqual(seen, [0]); // only the final page carried lines, at offset 0
  });

  it('delivers lines in order and advances the offset', async () => {
    const seen: number[] = [];
    let n = 0;
    await serialPoll({
      fetchPage: async (offset) => {
        n++;
        return { lines: [{ n: offset }], complete: n >= 3 };
      },
      isActive: () => true,
      isStopped: () => false,
      intervalMs: 1,
      onLines: (l) => seen.push(...l.map((x) => x.n)),
      onComplete: () => {},
    });
    assert.deepEqual(seen, [0, 1, 2]);
  });

  it('does one final fetch when active flips false, then stops', async () => {
    let active = true;
    let calls = 0;
    const seen: number[] = [];
    await serialPoll({
      fetchPage: async (offset) => {
        calls++;
        active = false; // caller reaches terminal after the first page
        return { lines: [{ n: offset }], complete: false };
      },
      isActive: () => active,
      isStopped: () => false,
      intervalMs: 1,
      onLines: (l) => seen.push(...l.map((x) => x.n)),
      onComplete: () => {},
    });
    assert.equal(calls, 2, 'one normal + one final pull, nothing more');
    assert.deepEqual(seen, [0, 1]);
  });

  it('retries after a transient error instead of dying', async () => {
    let calls = 0;
    await serialPoll({
      fetchPage: async () => {
        calls++;
        if (calls === 1) throw new Error('network blip');
        return { lines: [], complete: true };
      },
      isActive: () => true,
      isStopped: () => false,
      intervalMs: 1,
      onLines: () => {},
      onComplete: () => {},
    });
    assert.equal(calls, 2);
  });

  it('keeps prior lines when the job reaches complete:true with an empty final page', async () => {
    const display: number[] = [];
    let completed = false;
    let n = 0;
    await serialPoll({
      fetchPage: async (offset) => {
        n++;
        if (n === 1) {
          return { lines: [{ n: offset }, { n: offset + 1 }], complete: false };
        }
        // Job finished — status terminal, no new log bytes.
        return { lines: [], complete: true };
      },
      isActive: () => true,
      isStopped: () => false,
      intervalMs: 1,
      onLines: (l) => display.push(...l.map((x) => x.n)),
      onComplete: () => { completed = true; },
    });
    assert.equal(completed, true);
    assert.deepEqual(display, [0, 1], 'empty complete page must not wipe the buffer');
  });

  it('does not throw when a mis-routed page omits lines (treat as empty)', async () => {
    // Regression: GET /pending-applies/{id}/logs used to return the row
    // JSON (no lines array).  serialPoll must not crash — crash+retry
    // spun the drawer on Waiting forever while status=applied showed Done.
    let completed = false;
    await serialPoll({
      fetchPage: async () => {
        // @ts-expect-error intentional bad shape
        return { status: 'applied', complete: true };
      },
      isActive: () => true,
      isStopped: () => false,
      intervalMs: 1,
      onLines: () => { throw new Error('onLines must not run'); },
      onComplete: () => { completed = true; },
    });
    assert.equal(completed, true);
  });

  it('mergePollLines is a no-op on an empty page (display stays put)', () => {
    const seen = new Set<number>([0, 1]);
    const prev = [{ n: 0 }, { n: 1 }];
    const next = mergePollLines(prev, [], seen);
    assert.equal(next, prev);
    assert.deepEqual(next, [{ n: 0 }, { n: 1 }]);
  });
});
