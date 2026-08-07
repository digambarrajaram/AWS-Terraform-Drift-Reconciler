import { useRef, useEffect } from 'react';
import { format } from 'date-fns';
import type { LogLine } from '@/hooks/useScanLogs';

/**
 * Scrolling monospace log terminal.
 * Auto-scrolls to the bottom on new lines unless the user has manually
 * scrolled up (detected by >40 px from the bottom).
 */
export function LogViewer({ lines, complete }: { lines: LogLine[]; complete: boolean }) {
  const containerRef   = useRef<HTMLDivElement>(null);
  const bottomRef      = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    userScrolledUp.current =
      el.scrollHeight - el.scrollTop - el.clientHeight > 40;
  }

  useEffect(() => {
    if (!userScrolledUp.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines.length]);

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="h-72 overflow-y-auto rounded-lg border border-border bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-200"
    >
      {lines.length === 0 && !complete && (
        <span className="text-zinc-500 animate-pulse">Waiting for output…</span>
      )}
      {lines.map((line) => (
        <div key={line.n} className="flex gap-2 hover:bg-white/5 px-1 rounded">
          <span className="shrink-0 text-zinc-500 select-none w-10 text-right">
            {line.n}
          </span>
          <span className="text-zinc-400 shrink-0 select-none">
            {line.ts ? format(new Date(line.ts), 'HH:mm:ss') : ''}
          </span>
          <span className="break-all whitespace-pre-wrap">{line.text}</span>
        </div>
      ))}
      {complete && (
        <div className="mt-2 text-zinc-500">─── end of log ───</div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
