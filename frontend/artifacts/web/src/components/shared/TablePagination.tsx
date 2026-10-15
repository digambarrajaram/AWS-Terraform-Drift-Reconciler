import { ChevronLeft, ChevronRight } from 'lucide-react';

export function TablePagination({
  page,
  totalPages,
  total,
  from,
  to,
  pageSize,
  isRefetching,
  onPage,
  noun = 'row',
}: {
  page: number;
  totalPages: number;
  total: number;
  from: number;
  to: number;
  pageSize: number;
  isRefetching?: boolean;
  onPage: (p: number) => void;
  noun?: string;
}) {
  if (total === 0) return null;

  const plural = total !== 1 ? `${noun}s` : noun;

  return (
    <div
      className="flex items-center justify-between flex-wrap gap-3 rounded-lg border border-border bg-card px-3 py-2 shadow-sm"
      role="navigation"
      aria-label="Table pagination"
    >
      <span className="text-xs text-foreground">
        <span className="font-medium tabular-nums">{from}–{to}</span>
        <span className="text-muted-foreground"> of {total.toLocaleString()} {plural}</span>
        <span className="text-muted-foreground"> · {pageSize} per page</span>
        {isRefetching && <span className="text-muted-foreground"> · updating…</span>}
      </span>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
          className="flex items-center gap-1 rounded-md border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-40"
        >
          <ChevronLeft size={13} aria-hidden /> Previous
        </button>
        <span className="min-w-[5.5rem] text-center text-xs font-medium tabular-nums text-foreground">
          Page {page + 1} of {totalPages}
        </span>
        <button
          type="button"
          disabled={page >= totalPages - 1}
          onClick={() => onPage(page + 1)}
          className="flex items-center gap-1 rounded-md border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-40"
        >
          Next <ChevronRight size={13} aria-hidden />
        </button>
      </div>
    </div>
  );
}
