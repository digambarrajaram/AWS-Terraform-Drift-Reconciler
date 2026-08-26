// Serialized log-stream polling — the loop useScanLogs runs on.  Kept
// pure (no React, no fetch) so the failure modes it prevents are
// testable with node:test:
//
//   1. Never overlaps requests — the next poll fires only after the
//      previous one resolves, so slow responses can't stack (the original
//      pileup) and nothing ever cancels a request that is still working
//      (the cancel loop that left the scan terminal empty).
//   2. Stops the moment a page reports complete:true.
//   3. When the caller flips inactive (row/run reached terminal status),
//      does ONE final unaborted fetch so the view shows the complete
//      tail even if earlier responses were lost — then stops for good.
//   4. A final complete:true page with zero new lines must NOT wipe
//      earlier pages — onLines is only called when the page carries
//      lines, so the caller's accumulated buffer stays intact.
//
// Errors are transient by definition: a failed page is skipped and the
// next tick retries (the caller bounds each request with its own hard
// timeout via AbortController inside fetchPage).

export interface PollLine {
  n: number;
}

export interface PollPage {
  lines: PollLine[];
  complete: boolean;
}

export interface SerialPollOptions {
  fetchPage: (offset: number) => Promise<PollPage>;
  /** Caller-driven stop signal (row reached a terminal status). */
  isActive: () => boolean;
  /** Hard stop (unmount / run change / complete already observed). */
  isStopped: () => boolean;
  intervalMs: number;
  onLines: (lines: PollLine[]) => void;
  onComplete: () => void;
}

/** Merge a page of lines into an accumulated buffer, deduping by ``n``.
 * Empty pages are a no-op — used by useScanLogs and tested so a final
 * complete:true response cannot wipe prior content. */
export function mergePollLines<T extends PollLine>(
  prev: T[],
  pageLines: T[],
  seen: Set<number>,
): T[] {
  if (!pageLines.length) return prev;
  const fresh = pageLines.filter((l) => !seen.has(l.n));
  if (!fresh.length) return prev;
  for (const l of fresh) seen.add(l.n);
  return prev.concat(fresh);
}

export async function serialPoll(opts: SerialPollOptions): Promise<void> {
  const { fetchPage, isActive, isStopped, intervalMs, onLines, onComplete } = opts;
  let offset = 0;

  async function oneFetch(): Promise<boolean> {
    const page = await fetchPage(offset);
    const pageLines = Array.isArray(page.lines) ? page.lines : [];
    // Only notify when the page actually carries lines — an empty
    // complete:true tail must not look like a "replace with nothing".
    // Guard against a mis-routed response (no ``lines`` array) so we
    // don't throw and spin forever on retries.
    if (pageLines.length) {
      offset = pageLines.reduce((max, l) => (l.n > max ? l.n : max), offset) + 1;
      onLines(pageLines);
    }
    if (page.complete) {
      onComplete();
      return true;
    }
    return false;
  }

  while (!isStopped()) {
    if (!isActive()) {
      // Caller says terminal — one final pull so the view shows the
      // complete tail, then stop.
      try { await oneFetch(); } catch { /* nothing left to retry */ }
      return;
    }
    let done = false;
    try { done = await oneFetch(); } catch { /* transient — retry next tick */ }
    if (done || isStopped()) return;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
