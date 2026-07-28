import { useState, useEffect, useRef } from 'react';
import { apiFetch } from '@/api/apiFetch';

export interface LogLine {
  n: number;
  ts: string;
  text: string;
}

/**
 * Polls GET /api/scan/{runId}/logs?offset=N every ~1.5 s.
 * Accumulates lines across polls. Stops automatically when complete === true.
 * Resets state whenever runId changes.
 */
export function useScanLogs(runId: string | null) {
  const [lines, setLines]       = useState<LogLine[]>([]);
  const [complete, setComplete] = useState(false);
  const offsetRef               = useRef(0);
  const completeRef             = useRef(false);

  // Reset on runId change
  useEffect(() => {
    setLines([]);
    setComplete(false);
    offsetRef.current   = 0;
    completeRef.current = false;
  }, [runId]);

  useEffect(() => {
    if (!runId) return;

    let cancelled = false;

    async function poll() {
      if (cancelled || completeRef.current) return;
      try {
        const data = await apiFetch<{ lines: LogLine[]; complete: boolean }>(
          `/scan/${runId}/logs?offset=${offsetRef.current}`,
        );
        if (cancelled) return;
        if (data.lines.length > 0) {
          setLines((prev) => [...prev, ...data.lines]);
          offsetRef.current = Math.max(...data.lines.map((l) => l.n)) + 1;
        }
        if (data.complete) {
          completeRef.current = true;
          setComplete(true);
        }
      } catch {
        // Transient errors are silently retried on the next tick
      }
    }

    poll();
    const id = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [runId]);

  return { lines, complete };
}
