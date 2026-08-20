import { useState, useEffect, useRef } from 'react';
import { apiFetch } from '@/api/apiFetch';

export interface LogLine {
  n: number;
  ts: string;
  text: string;
}

const POLL_MS = 800;

/**
 * Polls GET /api/scan/{runId}/logs?offset=N every ~1.5 s.
 *
 * - Deduplicates lines by their API-issued ``n`` (line number), so even if
 *   the offset somehow stalls (network glitch, proxy retry, race between the
 *   initial fire-and-forget poll() and the first setInterval tick) the viewer
 *   never shows duplicate content.
 * - Tracks the highest ``n`` received in a ref and advances the offset
 *   request parameter every poll.
 * - Self-clears the interval when the backend signals ``complete: true``
 *   (scan / rollback finished) — no polling after the log is done.
 * - Cleans up on unmount / runId change.
 */
export function useScanLogs(runId: string | null) {
  const [lines, setLines]       = useState<LogLine[]>([]);
  const [complete, setComplete] = useState(false);

  // Mutable refs — read inside poll() without stale-closure issues.
  const seenRef       = useRef(new Set<number>());
  const offsetRef     = useRef(0);
  const completeRef   = useRef(false);
  const timerRef      = useRef<ReturnType<typeof setInterval> | undefined>(undefined);

  // ── Reset when the run changes ──────────────────────────────────────
  useEffect(() => {
    setLines([]);
    setComplete(false);
    offsetRef.current   = 0;
    completeRef.current = false;
    seenRef.current.clear();
    // Any existing timer belongs to the previous run — kill it.
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = undefined;
    }
  }, [runId]);

  // ── Polling loop ────────────────────────────────────────────────────
  useEffect(() => {
    if (!runId) return;

    let cancelled = false;

    async function poll() {
      if (cancelled || completeRef.current) return;

      // Snapshot the current offset *before* the fetch so the URL always
      // reflects what we've actually consumed.
      const offset = offsetRef.current;

      try {
        const data = await apiFetch<{ lines: LogLine[]; complete: boolean }>(
          `/scan/${runId}/logs?offset=${offset}`,
        );
        if (cancelled) return;

        // Dedupe: only keep lines whose n we haven't already rendered.
        const fresh = Array.isArray(data.lines)
          ? data.lines.filter((l) => !seenRef.current.has(l.n))
          : [];

        if (fresh.length > 0) {
          for (const l of fresh) seenRef.current.add(l.n);
          setLines((prev) => [...prev, ...fresh]);

          // Advance offset past the highest line number we just ingested.
          const maxN = fresh.reduce((max, l) => (l.n > max ? l.n : max), offset);
          offsetRef.current = maxN + 1;
        }

        if (data.complete) {
          completeRef.current = true;
          setComplete(true);
          // No more output — stop polling immediately.
          if (timerRef.current) {
            clearInterval(timerRef.current);
            timerRef.current = undefined;
          }
        }
      } catch {
        // Transient error — retry silently on the next tick.
      }
    }

    // Fire once immediately, then poll on an interval.
    poll();

    timerRef.current = setInterval(() => {
      poll();
    }, POLL_MS);

    return () => {
      cancelled = true;
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = undefined;
      }
    };
  }, [runId]);

  return { lines, complete };
}
