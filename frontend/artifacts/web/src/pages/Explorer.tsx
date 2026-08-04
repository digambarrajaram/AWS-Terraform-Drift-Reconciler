import { useState, useEffect } from 'react';
import { format, formatDistanceToNow } from 'date-fns';
import {
  Search, ChevronUp, ChevronDown, ChevronsUpDown,
  ChevronLeft, ChevronRight, Inbox, LayoutList, LayoutGrid,
  ShieldCheck, ShieldX, DollarSign, ChevronDown as ExpandIcon,
  ExternalLink, CalendarRange, RotateCcw,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from '@/components/ui/sheet';
import { useScope } from '@/hooks/useScope';
import {
  useDriftEvents, PAGE_SIZE,
  type SortColumn, type DriftFilters, type DriftSort,
} from '@/hooks/useDriftEvents';
import type { DriftEvent } from '@/types';

// ── Badge maps ──────────────────────────────────────────────────────────────

const SEV_CLS: Record<string, string> = {
  HIGH:   'bg-red-100    text-red-700    dark:bg-red-900/30   dark:text-red-400',
  MEDIUM: 'bg-amber-100  text-amber-700  dark:bg-amber-900/30 dark:text-amber-400',
  LOW:    'bg-blue-100   text-blue-700   dark:bg-blue-900/30  dark:text-blue-400',
};
const STATUS_CLS: Record<string, string> = {
  open:       'bg-amber-100  text-amber-700  dark:bg-amber-900/30  dark:text-amber-400',
  resolved:   'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
  suppressed: 'bg-zinc-100   text-zinc-600   dark:bg-zinc-800      dark:text-zinc-400',
};

// ── Shared UI atoms ─────────────────────────────────────────────────────────

function Badge({ value, map }: { value: string; map: Record<string, string> }) {
  const cls = map[value] ?? 'bg-muted text-muted-foreground';
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${cls}`}>
      {value}
    </span>
  );
}

function SortTh({
  col, label, sort, onSort,
}: {
  col: SortColumn; label: string; sort: DriftSort;
  onSort: (c: SortColumn) => void;
}) {
  const active = sort.column === col;
  return (
    <th
      onClick={() => onSort(col)}
      className="cursor-pointer select-none px-4 py-2.5 text-left text-xs font-medium text-muted-foreground hover:text-foreground whitespace-nowrap"
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active
          ? sort.ascending
            ? <ChevronUp size={12} className="text-foreground" />
            : <ChevronDown size={12} className="text-foreground" />
          : <ChevronsUpDown size={12} className="opacity-40" />}
      </span>
    </th>
  );
}

const selectCls =
  'rounded-md border border-input bg-background px-2 py-1.5 text-xs text-foreground ' +
  'focus:outline-none focus:ring-2 focus:ring-ring';

// ── Detail drawer (table-row click) ────────────────────────────────────────

function kv(label: string, value: React.ReactNode) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="text-sm text-foreground break-all">{value ?? '—'}</span>
    </div>
  );
}

/**
 * fields_changed is jsonb in the DB — it can arrive as a parsed array,
 * a JSON-encoded string (e.g. '["tags","tags_all"]'), or null.
 * Normalise to a plain string[] so .map() never throws.
 */
function normalizeFields(fields: unknown): string[] {
  if (Array.isArray(fields)) return fields as string[];
  if (typeof fields === 'string') {
    try { const parsed = JSON.parse(fields); return Array.isArray(parsed) ? parsed : []; }
    catch { return []; }
  }
  return [];
}

function EventDetail({ e }: { e: DriftEvent }) {
  const fields = normalizeFields(e.fields_changed);
  return (
    <div className="space-y-6 text-sm">
      <section className="space-y-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">General</h3>
        <div className="grid grid-cols-2 gap-3">
          {kv('Status',   <Badge value={e.status} map={STATUS_CLS} />)}
          {kv('Severity', <Badge value={e.severity} map={SEV_CLS} />)}
          {kv('Type',     e.pr_type ?? '—')}
          {kv('Region',   e.region)}
          {kv('Account',  e.account)}
          {kv('File',     e.file_path)}
          {kv('Created',  format(new Date(e.created_at), 'PPpp'))}
          {kv('Unmanaged', e.unmanaged ? 'Yes' : 'No')}
        </div>
        {e.resolution && kv('Resolution', e.resolution)}
      </section>

      {e.drift_summary && (
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">Drift Summary</h3>
          <p className="text-sm leading-relaxed text-foreground whitespace-pre-wrap">{e.drift_summary}</p>
        </section>
      )}

      {fields.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">Fields Changed</h3>
          <div className="flex flex-wrap gap-1.5">
            {fields.map((f) => (
              <span key={f} className="rounded-md bg-muted px-2 py-0.5 text-xs font-mono text-muted-foreground">{f}</span>
            ))}
          </div>
        </section>
      )}

      {e.changes_jsonb && Object.keys(e.changes_jsonb).length > 0 && (
        <section className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">Changes</h3>
          {Object.entries(e.changes_jsonb).map(([field, { before, after }]) => (
            <div key={field} className="space-y-1">
              <p className="text-xs font-mono font-medium text-foreground">{field}</p>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-md bg-red-50 dark:bg-red-950/30 p-2">
                  <p className="mb-1 text-[10px] font-semibold uppercase text-red-600 dark:text-red-400">Before</p>
                  <pre className="text-[11px] text-red-800 dark:text-red-300 whitespace-pre-wrap break-all font-mono">
                    {JSON.stringify(before, null, 2)}
                  </pre>
                </div>
                <div className="rounded-md bg-emerald-50 dark:bg-emerald-950/30 p-2">
                  <p className="mb-1 text-[10px] font-semibold uppercase text-emerald-600 dark:text-emerald-400">After</p>
                  <pre className="text-[11px] text-emerald-800 dark:text-emerald-300 whitespace-pre-wrap break-all font-mono">
                    {JSON.stringify(after, null, 2)}
                  </pre>
                </div>
              </div>
            </div>
          ))}
        </section>
      )}

      {e.cost_impact?.monthly_estimate_usd != null && (
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">Cost Impact</h3>
          <p className="text-lg font-semibold text-foreground">
            {new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
              .format(e.cost_impact.monthly_estimate_usd)}
            <span className="ml-1 text-xs font-normal text-muted-foreground">/ mo est.</span>
          </p>
        </section>
      )}

      {(e.trivy_passed != null || e.trivy_summary) && (
        <section className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">Trivy</h3>
          {e.trivy_passed != null && (
            <div className="flex items-center gap-1.5">
              {e.trivy_passed
                ? <><ShieldCheck size={15} className="text-emerald-500" /><span className="text-sm text-emerald-600 dark:text-emerald-400 font-medium">Gate passed</span></>
                : <><ShieldX size={15} className="text-destructive" /><span className="text-sm text-destructive font-medium">Gate failed</span></>}
            </div>
          )}
          {e.trivy_summary && (
            <pre className="rounded-md bg-muted p-3 text-[11px] font-mono text-muted-foreground whitespace-pre-wrap break-all overflow-auto max-h-48">
              {e.trivy_summary}
            </pre>
          )}
        </section>
      )}
    </div>
  );
}

function DetailDrawer({ event, onClose }: { event: DriftEvent | null; onClose: () => void }) {
  return (
    <Sheet open={!!event} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
        {event && (
          <>
            <SheetHeader className="mb-4">
              <SheetTitle className="text-base break-all font-mono">{event.resource_id}</SheetTitle>
            </SheetHeader>
            <EventDetail e={event} />
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

// ── Card view ───────────────────────────────────────────────────────────────

function ChangesDiff({ changes }: { changes: NonNullable<DriftEvent['changes_jsonb']> }) {
  const entries = Object.entries(changes);
  if (entries.length === 0) return null;
  return (
    <div className="space-y-2">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Changes</p>
      {entries.map(([field, { before, after }]) => (
        <div key={field} className="space-y-1">
          <p className="text-[11px] font-mono font-medium text-foreground">{field}</p>
          <div className="grid grid-cols-2 gap-1.5">
            <div className="rounded bg-red-50 dark:bg-red-950/30 px-2 py-1.5">
              <p className="mb-0.5 text-[9px] font-bold uppercase text-red-500">Before</p>
              <pre className="text-[10px] text-red-800 dark:text-red-300 whitespace-pre-wrap break-all font-mono leading-tight">
                {JSON.stringify(before, null, 2)}
              </pre>
            </div>
            <div className="rounded bg-emerald-50 dark:bg-emerald-950/30 px-2 py-1.5">
              <p className="mb-0.5 text-[9px] font-bold uppercase text-emerald-500">After</p>
              <pre className="text-[10px] text-emerald-800 dark:text-emerald-300 whitespace-pre-wrap break-all font-mono leading-tight">
                {JSON.stringify(after, null, 2)}
              </pre>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function DriftCard({
  event, expanded, onToggle,
}: {
  event: DriftEvent; expanded: boolean; onToggle: () => void;
}) {
  const fields     = normalizeFields(event.fields_changed);
  const hasCost    = event.cost_impact?.monthly_estimate_usd != null;
  const hasTrivy   = event.trivy_passed != null || !!event.trivy_summary;
  const hasChanges = !!event.changes_jsonb && Object.keys(event.changes_jsonb).length > 0;
  const hasFields  = fields.length > 0;
  const hasDetails = hasCost || hasTrivy || hasChanges || hasFields;

  return (
    <div className="rounded-xl border border-border bg-card flex flex-col overflow-hidden">
      {/* Card header */}
      <div className="px-4 pt-4 pb-3 space-y-2">
        {/* Badges row */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge value={event.severity} map={SEV_CLS} />
          <Badge value={event.status}   map={STATUS_CLS} />
          {event.pr_type && (
            <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground capitalize">
              {event.pr_type}
            </span>
          )}
          {event.pr_number && (
            <span className="ml-auto inline-flex items-center gap-1 text-[10px] text-primary">
              <ExternalLink size={9} /> #{event.pr_number}
            </span>
          )}
        </div>

        {/* Resource ID */}
        <p className="font-mono text-sm font-semibold text-foreground break-all leading-tight">
          {event.resource_id}
        </p>

        {/* Meta row */}
        <p className="text-[11px] text-muted-foreground">
          {event.account}
          {event.region && <> · {event.region}</>}
          {' · '}
          <span title={format(new Date(event.created_at), 'PPpp')}>
            {formatDistanceToNow(new Date(event.created_at), { addSuffix: true })}
          </span>
        </p>

        {/* Drift summary — prominent */}
        {event.drift_summary && (
          <p className="text-xs text-foreground leading-relaxed whitespace-pre-wrap border-t border-border pt-2 mt-1">
            {event.drift_summary}
          </p>
        )}

        {/* Quick stats */}
        <div className="flex flex-wrap items-center gap-3 pt-1">
          {hasCost && (
            <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
              <DollarSign size={11} />
              {new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
                .format(event.cost_impact!.monthly_estimate_usd as number)}
              <span className="text-[10px]">/mo</span>
            </span>
          )}
          {event.trivy_passed != null && (
            <span className="inline-flex items-center gap-1 text-[11px]">
              {event.trivy_passed
                ? <><ShieldCheck size={11} className="text-emerald-500" /><span className="text-emerald-600 dark:text-emerald-400">Trivy ✓</span></>
                : <><ShieldX size={11} className="text-destructive" /><span className="text-destructive">Trivy ✗</span></>}
            </span>
          )}
          {hasFields && (
            <span className="text-[11px] text-muted-foreground">
              {fields.length} field{fields.length !== 1 ? 's' : ''} changed
            </span>
          )}
        </div>
      </div>

      {/* Expand toggle */}
      {hasDetails && (
        <button
          type="button"
          onClick={onToggle}
          className="flex items-center gap-1.5 border-t border-border px-4 py-2 text-[11px] font-medium text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-colors text-left"
        >
          <ExpandIcon
            size={12}
            className={`transition-transform ${expanded ? 'rotate-180' : ''}`}
          />
          {expanded ? 'Hide details' : 'Show details'}
        </button>
      )}

      {/* Expandable detail section */}
      {expanded && hasDetails && (
        <div className="border-t border-border bg-muted/20 px-4 py-4 space-y-4">
          {hasFields && (
            <div className="space-y-1.5">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Fields Changed</p>
              <div className="flex flex-wrap gap-1">
                {fields.map((f) => (
                  <span key={f} className="rounded bg-background border border-border px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
                    {f}
                  </span>
                ))}
              </div>
            </div>
          )}

          {hasChanges && <ChangesDiff changes={event.changes_jsonb!} />}

          {hasCost && (
            <div className="space-y-1">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Cost Impact</p>
              <p className="text-base font-semibold text-foreground">
                {new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
                  .format(event.cost_impact!.monthly_estimate_usd as number)}
                <span className="ml-1 text-xs font-normal text-muted-foreground">/ mo est.</span>
              </p>
            </div>
          )}

          {hasTrivy && (
            <div className="space-y-1.5">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Trivy</p>
              {event.trivy_passed != null && (
                <div className="flex items-center gap-1.5 text-xs">
                  {event.trivy_passed
                    ? <><ShieldCheck size={13} className="text-emerald-500" /><span className="text-emerald-600 dark:text-emerald-400 font-medium">Gate passed</span></>
                    : <><ShieldX size={13} className="text-destructive" /><span className="text-destructive font-medium">Gate failed</span></>}
                </div>
              )}
              {event.trivy_summary && (
                <pre className="rounded bg-muted px-3 py-2 text-[10px] font-mono text-muted-foreground whitespace-pre-wrap break-all overflow-auto max-h-40">
                  {event.trivy_summary}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Filter bar ──────────────────────────────────────────────────────────────

interface ExplorerFilters extends DriftFilters {
  dateFrom: string;
  dateTo:   string;
}

const DEFAULT_FILTERS: ExplorerFilters = {
  statusFilter: 'all', severityFilter: 'all', typeFilter: 'all',
  search: '', dateFrom: '', dateTo: '',
};
const DEFAULT_SORT: DriftSort = { column: 'created_at', ascending: false };

function FilterBar({
  filters, onFilters, searchInput, onSearchInput, sort, onSort, view,
  onClear, isFiltered,
}: {
  filters:       ExplorerFilters;
  onFilters:     (f: Partial<ExplorerFilters>) => void;
  searchInput:   string;
  onSearchInput: (v: string) => void;
  sort:          DriftSort;
  onSort:        (c: SortColumn) => void;
  view:          'table' | 'cards';
  onClear:       () => void;
  isFiltered:    boolean;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 items-center">
        {/* Status */}
        <select value={filters.statusFilter}
          onChange={(e) => onFilters({ statusFilter: e.target.value })} className={selectCls}>
          <option value="all">All statuses</option>
          <option value="open">Open</option>
          <option value="resolved">Resolved</option>
          <option value="suppressed">Suppressed</option>
        </select>

        {/* Severity */}
        <select value={filters.severityFilter}
          onChange={(e) => onFilters({ severityFilter: e.target.value })} className={selectCls}>
          <option value="all">All severities</option>
          <option value="HIGH">High</option>
          <option value="MEDIUM">Medium</option>
          <option value="LOW">Low</option>
        </select>

        {/* Type */}
        <select value={filters.typeFilter}
          onChange={(e) => onFilters({ typeFilter: e.target.value })} className={selectCls}>
          <option value="all">All types</option>
          <option value="fix">Fix</option>
          <option value="batch">Batch</option>
          <option value="rollback">Rollback</option>
          <option value="unmanaged">Unmanaged</option>
          <option value="manual">Manual</option>
        </select>

        {/* Resource search */}
        <div className="relative flex items-center">
          <Search size={13} className="absolute left-2 text-muted-foreground pointer-events-none" />
          <input
            type="search"
            placeholder="Search resource ID…"
            value={searchInput}
            onChange={(e) => onSearchInput(e.target.value)}
            className="rounded-md border border-input bg-background pl-7 pr-3 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring w-48"
          />
        </div>

        {/* Card-mode sort */}
        {view === 'cards' && (
          <select
            value={`${sort.column}:${sort.ascending ? 'asc' : 'desc'}`}
            onChange={(e) => {
              const [col, dir] = e.target.value.split(':');
              onSort(col as SortColumn);
              // Hack: toggle sort twice if needed to land on the right direction
              // Actually we need a direct setter — let's skip for now and handle via onSort
            }}
            className={selectCls}
          >
            <option value="created_at:desc">Newest first</option>
            <option value="created_at:asc">Oldest first</option>
            <option value="severity:asc">Severity (A→Z)</option>
            <option value="resource_id:asc">Resource (A→Z)</option>
          </select>
        )}

        {/* Clear */}
        {isFiltered && (
          <button type="button" onClick={onClear}
            className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent transition-colors">
            <RotateCcw size={11} /> Clear
          </button>
        )}
      </div>

      {/* Date range row */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <CalendarRange size={12} /> Date range
        </span>
        <input
          type="date"
          value={filters.dateFrom}
          max={filters.dateTo || undefined}
          onChange={(e) => onFilters({ dateFrom: e.target.value })}
          className={`${selectCls} text-xs`}
        />
        <span className="text-[11px] text-muted-foreground">to</span>
        <input
          type="date"
          value={filters.dateTo}
          min={filters.dateFrom || undefined}
          onChange={(e) => onFilters({ dateTo: e.target.value })}
          className={`${selectCls} text-xs`}
        />
        {(filters.dateFrom || filters.dateTo) && (
          <button type="button"
            onClick={() => onFilters({ dateFrom: '', dateTo: '' })}
            className="text-[11px] text-muted-foreground hover:text-foreground hover:underline">
            Clear dates
          </button>
        )}
      </div>
    </div>
  );
}

// ── Table skeleton ──────────────────────────────────────────────────────────

function TableSkeleton() {
  return (
    <>
      {[...Array(8)].map((_, i) => (
        <tr key={i}>
          {[...Array(6)].map((_, j) => (
            <td key={j} className="px-4 py-3">
              <Skeleton className="h-4" style={{ width: `${55 + (j * 13 + i * 9) % 40}%` }} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function CardSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {[...Array(6)].map((_, i) => (
        <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-3">
          <div className="flex gap-2">
            <Skeleton className="h-4 w-12 rounded-full" />
            <Skeleton className="h-4 w-16 rounded-full" />
          </div>
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-10 w-full" />
        </div>
      ))}
    </div>
  );
}

// ── Empty state ─────────────────────────────────────────────────────────────

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
      <Inbox size={40} className="text-muted-foreground/30" />
      <p className="text-sm font-medium text-muted-foreground">
        {filtered ? 'No events match your filters' : 'No drift events for this scope'}
      </p>
      {filtered && (
        <p className="text-xs text-muted-foreground/70">Try adjusting or clearing your filters.</p>
      )}
    </div>
  );
}

// ── Pagination bar ──────────────────────────────────────────────────────────

function Pagination({
  page, totalPages, total, from, to, isFetching, onPage,
}: {
  page: number; totalPages: number; total: number;
  from: number; to: number; isFetching: boolean;
  onPage: (p: number) => void;
}) {
  if (total === 0) return null;
  return (
    <div className="flex items-center justify-between flex-wrap gap-2">
      <span className="text-xs text-muted-foreground">
        {from}–{to} of {total.toLocaleString()} event{total !== 1 ? 's' : ''}
        {isFetching && ' · updating…'}
      </span>
      <div className="flex items-center gap-1">
        <button type="button" disabled={page === 0}
          onClick={() => onPage(page - 1)}
          className="flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40 transition-colors">
          <ChevronLeft size={13} /> Prev
        </button>
        <span className="px-2 text-xs text-muted-foreground">{page + 1} / {totalPages}</span>
        <button type="button" disabled={page >= totalPages - 1}
          onClick={() => onPage(page + 1)}
          className="flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40 transition-colors">
          Next <ChevronRight size={13} />
        </button>
      </div>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function Explorer() {
  const { scope } = useScope();

  const [filters,     setFilters]     = useState<ExplorerFilters>(DEFAULT_FILTERS);
  const [sort,        setSort]        = useState<DriftSort>(DEFAULT_SORT);
  const [page,        setPage]        = useState(0);
  const [searchInput, setSearchInput] = useState('');
  const [view,        setView]        = useState<'table' | 'cards'>('table');
  const [selected,    setSelected]    = useState<DriftEvent | null>(null);
  const [expanded,    setExpanded]    = useState<Set<number>>(new Set());

  // Debounce search → filters.search
  useEffect(() => {
    const id = setTimeout(() => {
      setFilters((prev) => ({ ...prev, search: searchInput }));
      setPage(0);
    }, 300);
    return () => clearTimeout(id);
  }, [searchInput]);

  // Reset page on filter / sort change
  // Decompose sort into primitives to avoid re-firing when the object
  // reference changes but the values are identical.
  useEffect(() => {
    setPage(0);
  }, [
    filters.statusFilter, filters.severityFilter, filters.typeFilter,
    filters.dateFrom, filters.dateTo,
    sort.column, sort.ascending,
  ]);

  // Clear expanded cards on page/view change
  useEffect(() => { setExpanded(new Set()); }, [page, view]);

  const { data, isLoading, isFetching } = useDriftEvents(scope, filters, sort, page);
  const events     = data?.events ?? [];
  const total      = data?.count  ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from       = page * PAGE_SIZE + 1;
  const to         = Math.min((page + 1) * PAGE_SIZE, total);

  const isFiltered = (
    filters.statusFilter   !== 'all' ||
    filters.severityFilter !== 'all' ||
    filters.typeFilter     !== 'all' ||
    !!filters.search       ||
    !!filters.dateFrom     ||
    !!filters.dateTo
  );

  function updateFilters(patch: Partial<ExplorerFilters>) {
    setFilters((prev) => ({ ...prev, ...patch }));
  }

  function handleSort(col: SortColumn) {
    setSort((prev) =>
      prev.column === col
        ? { column: col, ascending: !prev.ascending }
        : { column: col, ascending: col === 'created_at' ? false : true },
    );
  }

  function handleCardSort(val: string) {
    const [col, dir] = val.split(':') as [SortColumn, 'asc' | 'desc'];
    setSort({ column: col, ascending: dir === 'asc' });
  }

  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function clearFilters() {
    setFilters(DEFAULT_FILTERS);
    setSearchInput('');
    setPage(0);
  }

  return (
    <div className="p-6 space-y-4 max-w-7xl">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold">Explorer</h1>
          {!isLoading && (
            <span className="text-xs text-muted-foreground">
              {total.toLocaleString()} result{total !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        {/* View toggle */}
        <div className="flex items-center rounded-lg border border-border bg-background p-0.5 gap-0.5">
          <button
            type="button"
            onClick={() => setView('table')}
            className={[
              'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
              view === 'table'
                ? 'bg-accent text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            ].join(' ')}
          >
            <LayoutList size={13} /> Table
          </button>
          <button
            type="button"
            onClick={() => setView('cards')}
            className={[
              'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
              view === 'cards'
                ? 'bg-accent text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            ].join(' ')}
          >
            <LayoutGrid size={13} /> Cards
          </button>
        </div>
      </div>

      {/* Filter bar */}
      <FilterBar
        filters={filters}
        onFilters={updateFilters}
        searchInput={searchInput}
        onSearchInput={setSearchInput}
        sort={sort}
        onSort={handleSort}
        view={view}
        onClear={clearFilters}
        isFiltered={isFiltered}
      />

      {/* ── TABLE VIEW ──────────────────────────────────────────────────── */}
      {view === 'table' && (
        <div className="rounded-xl border border-border overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[720px]">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  <SortTh col="resource_id" label="Resource"  sort={sort} onSort={handleSort} />
                  <SortTh col="severity"    label="Severity"  sort={sort} onSort={handleSort} />
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Type</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
                  <SortTh col="created_at"  label="Created"   sort={sort} onSort={handleSort} />
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">PR</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {isLoading ? (
                  <TableSkeleton />
                ) : events.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <EmptyState filtered={isFiltered} />
                    </td>
                  </tr>
                ) : (
                  events.map((ev) => (
                    <tr
                      key={ev.id}
                      onClick={() => setSelected(ev)}
                      className={[
                        'cursor-pointer transition-colors',
                        selected?.id === ev.id ? 'bg-accent' : 'hover:bg-muted/50',
                      ].join(' ')}
                    >
                      <td className="px-4 py-3 font-mono text-xs max-w-[240px]">
                        <span className="block truncate" title={ev.resource_id}>{ev.resource_id}</span>
                      </td>
                      <td className="px-4 py-3">
                        <Badge value={ev.severity} map={SEV_CLS} />
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground capitalize">
                        {ev.pr_type ?? '—'}
                      </td>
                      <td className="px-4 py-3">
                        <Badge value={ev.status} map={STATUS_CLS} />
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
                        <span title={format(new Date(ev.created_at), 'PPpp')}>
                          {formatDistanceToNow(new Date(ev.created_at), { addSuffix: true })}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs">
                        {ev.pr_number
                          ? <span className="inline-flex items-center gap-1 text-primary" onClick={(e) => e.stopPropagation()}>
                              <ExternalLink size={11} /> #{ev.pr_number}
                            </span>
                          : <span className="text-muted-foreground">—</span>}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          {/* Table pagination */}
          {!isLoading && total > 0 && (
            <div className="border-t border-border bg-muted/20 px-4 py-2.5">
              <Pagination
                page={page} totalPages={totalPages} total={total}
                from={from} to={to} isFetching={isFetching}
                onPage={setPage}
              />
            </div>
          )}
        </div>
      )}

      {/* ── CARD VIEW ───────────────────────────────────────────────────── */}
      {view === 'cards' && (
        <div className="space-y-4">
          {/* Card-mode sort selector */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Sort:</span>
            <select
              value={`${sort.column}:${sort.ascending ? 'asc' : 'desc'}`}
              onChange={(e) => handleCardSort(e.target.value)}
              className={selectCls}
            >
              <option value="created_at:desc">Newest first</option>
              <option value="created_at:asc">Oldest first</option>
              <option value="severity:asc">Severity (A→Z)</option>
              <option value="severity:desc">Severity (Z→A)</option>
              <option value="resource_id:asc">Resource (A→Z)</option>
              <option value="resource_id:desc">Resource (Z→A)</option>
            </select>
          </div>

          {isLoading ? (
            <CardSkeleton />
          ) : events.length === 0 ? (
            <div className="rounded-xl border border-border">
              <EmptyState filtered={isFiltered} />
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {events.map((ev) => (
                <DriftCard
                  key={ev.id}
                  event={ev}
                  expanded={expanded.has(ev.id)}
                  onToggle={() => toggleExpand(ev.id)}
                />
              ))}
            </div>
          )}

          {/* Card pagination */}
          {!isLoading && (
            <Pagination
              page={page} totalPages={totalPages} total={total}
              from={from} to={to} isFetching={isFetching}
              onPage={setPage}
            />
          )}
        </div>
      )}

      {/* Detail drawer — table view only */}
      <DetailDrawer event={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
