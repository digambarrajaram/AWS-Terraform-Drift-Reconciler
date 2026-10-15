import { useEffect, useState } from 'react';
import { format, formatDistanceStrict } from 'date-fns';

/** Tick interval for relative timestamps — keeps Explorer and PR Queue in sync. */
const TICK_MS = 30_000;

/** Shared clock so relative labels match across pages opened at the same time. */
export function useRelativeNow(tickMs = TICK_MS): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), tickMs);
    return () => clearInterval(id);
  }, [tickMs]);
  return now;
}

export function formatAbsoluteTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return format(date, 'MMM d, yyyy, HH:mm');
}

export function formatRelativeTime(iso: string, now: number): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return formatDistanceStrict(date, new Date(now), { addSuffix: true });
}
