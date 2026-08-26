import { useState, useEffect, useRef } from 'react';
import { apiFetch } from '@/api/apiFetch';
import { serialPoll, mergePollLines, type PollPage } from './serialPoll';

export interface LogLine {
  n: number;
  ts: string;
  text: string;
}

const POLL_MS = 800;

/**
 * Streams the log endpoint for a scan / rollback / pending-apply run.
 *
 * - Serialized polling (see serialPoll): the next request fires only after
 *   the previous resolves — slow responses never stack and never get
 *   canceled mid-flight by the next tick.
 * - Each request has a generous hard timeout via AbortController; a timed
 *   out page is skipped and the next tick retries.  Requests are aborted
 *   ONLY on hard timeout — never by the polling cadence or effect cleanup
 *   (aborting the final complete:true page raced the Approvals drawer and
 *   left the buffer empty when "Job finished" appeared).
 * - Stops on the backend's ``complete: true``, or when ``active`` flips
 *   false (caller reached a terminal status) — after one final unaborted
 *   fetch so the view shows the complete log tail.
 * - Accumulated lines survive ``complete: true`` (including an empty final
 *   page).  Only a ``runId`` change clears the buffer.
 * - ``kind='pending'`` points at /api/pending-applies/{id}/logs so the
 *   endpoint probes only the pending_applies table.
 * - Returns the row ``status`` carried in the same page (pending applies)
 *   so the caller can render the live badge from THIS poller alone — no
 *   second per-row status query running alongside the log poll.
 * - Deduplicates lines by their API-issued ``n`` and advances the offset
 *   past the highest line ingested.
 */
export function useScanLogs(
  runId: string | null,
  active = true,
  kind: 'scan' | 'pending' = 'scan',
) {
  const [lines, setLines]       = useState<LogLine[]>([]);
  const [complete, setComplete] = useState(false);
  const [status, setStatus]     = useState<string | null>(null);

  const seenRef       = useRef(new Set<number>());
  const completeRef   = useRef(false);
  const activeRef     = useRef(active);
  // Mirror of ``lines`` so a late in-flight page after cleanup can still
  // merge without reading stale React state.
  const linesRef      = useRef<LogLine[]>([]);
  activeRef.current = active;

  // ── Reset when the run changes ──────────────────────────────────────
  useEffect(() => {
    linesRef.current = [];
    setLines([]);
    setComplete(false);
    setStatus(null);
    completeRef.current = false;
    seenRef.current.clear();
  }, [runId]);

  // ── Polling loop ────────────────────────────────────────────────────
  useEffect(() => {
    if (!runId) return;

    let stopped = false;
    let inFlight: AbortController | null = null;
    // Hard ceiling per request — a genuinely hung backend must not stall
    // the stream forever (the next tick retries); a *working* but slow
    // backend is never aborted.
    const HARD_TIMEOUT_MS = POLL_MS * 20;

    void serialPoll({
      fetchPage: async (offset) => {
        const ctrl = new AbortController();
        inFlight = ctrl;
        const hard = setTimeout(() => ctrl.abort(), HARD_TIMEOUT_MS);
        try {
          const page = await apiFetch<PollPage & { status?: string | null }>(
            `/${kind === 'pending' ? 'pending-applies' : 'scan'}/${runId}/logs?offset=${offset}`,
            { signal: ctrl.signal },
          );
          if (page.status) setStatus(page.status);
          return page;
        } finally {
          clearTimeout(hard);
          if (inFlight === ctrl) inFlight = null;
        }
      },
      isActive: () => activeRef.current,
      isStopped: () => stopped || completeRef.current,
      intervalMs: POLL_MS,
      onLines: (pageLines) => {
        // Apply even after ``stopped`` so an in-flight final page that
        // races cleanup still lands — only a runId change clears via the
        // reset effect.  Empty pages are a no-op (mergePollLines).
        const next = mergePollLines(linesRef.current, pageLines as LogLine[], seenRef.current);
        if (next === linesRef.current) return;
        linesRef.current = next;
        setLines(next);
      },
      onComplete: () => {
        completeRef.current = true;
        setComplete(true);
      },
    });

    return () => {
      stopped = true;
      // Do not abort an in-flight page: that final complete:true response
      // often carries the last lines (or confirms the tail).  Aborting it
      // raced the Approvals drawer's status flip and left the buffer empty
      // right when "Job finished" appeared.  The hard timeout still aborts
      // hung requests; a new runId's effect ignores late merges via reset.
      inFlight = null;
    };
  }, [runId, kind]);

  return { lines, complete, status };
}
