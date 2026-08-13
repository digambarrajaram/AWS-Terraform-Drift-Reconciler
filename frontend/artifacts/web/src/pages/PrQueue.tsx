import { useState, useEffect } from 'react';
import { format, formatDistanceToNow } from 'date-fns';
import {
  Search, ChevronUp, ChevronDown, ChevronsUpDown,
  ExternalLink, ChevronLeft, ChevronRight, Inbox,
  CheckCircle, XCircle, ShieldCheck, ShieldX,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from '@/components/ui/sheet';
import { useScope } from '@/hooks/useScope';
import { useEnvironments } from '@/hooks/useEnvironments';
import { useAppConfig } from '@/api/config';
import {
  useDriftEvents, PAGE_SIZE, type SortColumn, type DriftFilters, type DriftSort,
} from '@/hooks/useDriftEvents';
import type { DriftEvent } from '@/types';

// ── Badges ─────────────────────────────────────────────────────────────────

const SEV: Record<string, string> = {
  HIGH:   'bg-red-100    text-red-700    dark:bg-red-900/30   dark:text-red-400',
  MEDIUM: 'bg-amber-100  text-amber-700  dark:bg-amber-900/30 dark:text-amber-400',
  LOW:    'bg-blue-100   text-blue-700   dark:bg-blue-900/30  dark:text-blue-400',
};
const STATUS: Record<string, string> = {
  open:       'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  resolved:   'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
  suppressed: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
};

function Badge({ value, map }: { value: string; map: Record<string, string> }) {
  const cls = map[value] ?? 'bg-muted text-muted-foreground';
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {value}
    </span>
  );
}

// ── Sort header ────────────────────────────────────────────────────────────

function SortTh({
  col, label, sort, onSort,
}: {
  col: SortColumn; label: string; sort: DriftSort; onSort: (c: SortColumn) => void;
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

// ── Detail drawer ──────────────────────────────────────────────────────────

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
 * a JSON-encoded string, or null.  Normalise so .map() never throws.
 */
function normalizeFields(fields: unknown): string[] {
  if (Array.isArray(fields)) return fields as string[];
  if (typeof fields === 'string') {
    try { const parsed = JSON.parse(fields); return Array.isArray(parsed) ? parsed : []; }
    catch { return []; }
  }
  return [];
}

/**
 * Build a GitHub PR URL from either an explicit repo URL (e.g. from the
 * environments table) or a short "owner/repo" string (from the global
 * GITHUB_REPO env var).  Returns null when neither is available or when
 * prNumber is missing.
 */
function buildPrUrl(
  repoUrl: string | null,
  githubRepo: string | undefined,
  prNumber: number | null,
): string | null {
  if (!prNumber) return null;

  let base = repoUrl;
  if (!base && githubRepo) {
    base = `https://github.com/${githubRepo}`;
  }
  if (!base) return null;

  // Strip trailing .git and /
  const clean = base.replace(/\.git$/, '').replace(/\/+$/, '');
  return `${clean}/pull/${prNumber}`;
}

function DetailDrawer({ event, onClose, repoUrl, githubRepo }: {
  event: DriftEvent | null;
  onClose: () => void;
  repoUrl: string | null;
  githubRepo: string | undefined;
}) {
  const open   = !!event;
  const e      = event;
  const fields = e ? normalizeFields(e.fields_changed) : [];
  const prUrl  = e ? buildPrUrl(repoUrl, githubRepo, e.pr_number) : null;

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full sm:max-w-xl overflow-y-auto">
        {e && (
          <>
            <SheetHeader className="mb-4">
              <SheetTitle className="text-base break-all">{e.resource_id}</SheetTitle>
            </SheetHeader>

            {/* ── View PR on GitHub (prominent action) ── */}
            {prUrl && (
              <a
                href={prUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="mb-4 inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent"
              >
                <ExternalLink size={12} />
                View PR #{e.pr_number} on GitHub
              </a>
            )}
            {!prUrl && e.pr_number && (
              <span className="mb-4 inline-flex items-center gap-1.5 rounded-md border border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground"
                    title="Set GITHUB_REPO in .env or repo_url on this scope's environment to enable PR links.">
                <ExternalLink size={12} />
                PR #{e.pr_number} — repo not configured
              </span>
            )}

            <div className="space-y-6 text-sm">
              {/* General */}
              <section className="space-y-3">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
                  General
                </h3>
                <div className="grid grid-cols-2 gap-3">
                  {kv('Status',   <Badge value={e.status} map={STATUS} />)}
                  {kv('Severity', <Badge value={e.severity} map={SEV} />)}
                  {kv('Type',     e.pr_type ?? '—')}
                  {kv('Region',   e.region)}
                  {kv('Account',  e.account)}
                  {kv('File',     e.file_path)}
                  {kv('Created',  format(new Date(e.created_at), 'MMM d, yyyy, HH:mm'))}
                  {kv('Unmanaged', e.unmanaged ? 'Yes' : 'No')}
                </div>
                {e.resolution && kv('Resolution', e.resolution)}
              </section>

              {/* Drift summary */}
              {e.drift_summary && (
                <section className="space-y-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
                    Drift Summary
                  </h3>
                  <p className="text-sm leading-relaxed text-foreground whitespace-pre-wrap">
                    {e.drift_summary}
                  </p>
                </section>
              )}

              {/* Fields changed */}
              {fields.length > 0 && (
                <section className="space-y-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
                    Fields Changed
                  </h3>
                  <div className="flex flex-wrap gap-1.5">
                    {fields.map((f) => (
                      <span key={f} className="rounded-md bg-muted px-2 py-0.5 text-xs font-mono text-muted-foreground">
                        {f}
                      </span>
                    ))}
                  </div>
                </section>
              )}

              {/* Before / after diff */}
              {e.changes_jsonb && Object.keys(e.changes_jsonb).length > 0 && (
                <section className="space-y-3">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
                    Changes
                  </h3>
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

              {/* Cost impact */}
              {e.cost_impact?.monthly_estimate_usd != null && (
                <section className="space-y-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
                    Cost Impact
                  </h3>
                  <p className="text-lg font-semibold text-foreground">
                    {new Intl.NumberFormat('en-US', {
                      style: 'currency', currency: 'USD', maximumFractionDigits: 2,
                    }).format(e.cost_impact.monthly_estimate_usd)}
                    <span className="ml-1 text-xs font-normal text-muted-foreground">/ mo est.</span>
                  </p>
                </section>
              )}

              {/* Trivy */}
              {(e.trivy_passed != null || e.trivy_summary) && (
                <section className="space-y-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground border-b border-border pb-1">
                    Trivy
                  </h3>
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
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

// ── Filter bar ─────────────────────────────────────────────────────────────

const selectCls =
  'rounded-md border border-input bg-background px-2 py-1.5 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring';

function FilterBar({
  filters,
  onFilters,
  searchInput,
  onSearchInput,
}: {
  filters: DriftFilters;
  onFilters: (f: Partial<DriftFilters>) => void;
  searchInput: string;
  onSearchInput: (v: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2 items-center">
      {/* Status */}
      <select
        value={filters.statusFilter}
        onChange={(e) => onFilters({ statusFilter: e.target.value })}
        className={selectCls}
      >
        <option value="all">All statuses</option>
        <option value="open">Open</option>
        <option value="resolved">Resolved</option>
        <option value="suppressed">Suppressed</option>
      </select>

      {/* Severity */}
      <select
        value={filters.severityFilter}
        onChange={(e) => onFilters({ severityFilter: e.target.value })}
        className={selectCls}
      >
        <option value="all">All severities</option>
        <option value="HIGH">High</option>
        <option value="MEDIUM">Medium</option>
        <option value="LOW">Low</option>
      </select>

      {/* Type */}
      <select
        value={filters.typeFilter}
        onChange={(e) => onFilters({ typeFilter: e.target.value })}
        className={selectCls}
      >
        <option value="all">All types</option>
        <option value="fix">Fix</option>
        <option value="batch">Batch</option>
        <option value="rollback">Rollback</option>
        <option value="unmanaged">Unmanaged</option>
        <option value="security_only">Security Only</option>
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
          className="rounded-md border border-input bg-background pl-7 pr-3 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring w-52"
        />
      </div>
    </div>
  );
}

// ── Empty state ────────────────────────────────────────────────────────────

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <tr>
      <td colSpan={6}>
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
          <Inbox size={36} className="text-muted-foreground/40" />
          <p className="text-sm font-medium text-muted-foreground">
            {filtered ? 'No events match your filters' : 'No drift events for this scope'}
          </p>
          {filtered && (
            <p className="text-xs text-muted-foreground/70">Try clearing some filters.</p>
          )}
        </div>
      </td>
    </tr>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

const DEFAULT_FILTERS: DriftFilters = {
  statusFilter: 'all', severityFilter: 'all', typeFilter: 'all', search: '',
};
const DEFAULT_SORT: DriftSort = { column: 'created_at', ascending: false };

export default function PrQueue() {
  const { scope } = useScope();
  const { activeEnvironments } = useEnvironments();
  const { data: config } = useAppConfig();

  // Resolve the GitHub repo for PR links — prefer the environment-level
  // repo_url, fall back to the global GITHUB_REPO from config.
  const repoUrl    = activeEnvironments.find((e) => e.slug === scope)?.repo_url ?? null;
  const githubRepo = config?.githubRepo;

  const [filters,     setFilters]     = useState<DriftFilters>(DEFAULT_FILTERS);
  const [sort,        setSort]        = useState<DriftSort>(DEFAULT_SORT);
  const [page,        setPage]        = useState(0);
  const [searchInput, setSearchInput] = useState('');
  const [selected,    setSelected]    = useState<DriftEvent | null>(null);

  // Debounce search input → filters.search
  useEffect(() => {
    const id = setTimeout(() => {
      setFilters((prev) => ({ ...prev, search: searchInput }));
      setPage(0);
    }, 300);
    return () => clearTimeout(id);
  }, [searchInput]);

  // Reset page when filters / sort change
  // Decompose sort into primitives — same reasoning as Explorer.tsx.
  useEffect(() => { setPage(0); }, [
    filters.statusFilter, filters.severityFilter, filters.typeFilter,
    sort.column, sort.ascending,
  ]);

  const { data, isLoading, isFetching } = useDriftEvents(scope, filters, sort, page);
  const events = data?.events ?? [];
  const total  = data?.count  ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = page * PAGE_SIZE + 1;
  const to   = Math.min((page + 1) * PAGE_SIZE, total);

  function updateFilters(patch: Partial<DriftFilters>) {
    setFilters((prev) => ({ ...prev, ...patch }));
  }

  function handleSort(col: SortColumn) {
    setSort((prev) =>
      prev.column === col
        ? { column: col, ascending: !prev.ascending }
        : { column: col, ascending: col === 'created_at' ? false : true },
    );
  }

  const isFiltered = (
    filters.statusFilter !== 'all' ||
    filters.severityFilter !== 'all' ||
    filters.typeFilter !== 'all' ||
    !!filters.search
  );

  return (
    <div className="p-6 space-y-4 max-w-7xl">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-xl font-semibold">PR Queue</h1>
        {!isLoading && (
          <span className="text-xs text-muted-foreground">
            {total.toLocaleString()} result{total !== 1 ? 's' : ''}
            {isFetching && ' · updating…'}
          </span>
        )}
      </div>

      <FilterBar
        filters={filters}
        onFilters={updateFilters}
        searchInput={searchInput}
        onSearchInput={setSearchInput}
      />

      <div className="rounded-xl border border-border overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[700px]">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <SortTh col="resource_id" label="Resource"   sort={sort} onSort={handleSort} />
                <SortTh col="severity"    label="Severity"   sort={sort} onSort={handleSort} />
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Type</th>
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Status</th>
                <SortTh col="created_at"  label="Created"    sort={sort} onSort={handleSort} />
                <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">PR</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {isLoading ? (
                [...Array(8)].map((_, i) => (
                  <tr key={i}>
                    {[...Array(6)].map((_, j) => (
                      <td key={j} className="px-4 py-3">
                        <Skeleton className="h-4" style={{ width: `${60 + (j * 17 + i * 11) % 40}%` }} />
                      </td>
                    ))}
                  </tr>
                ))
              ) : events.length === 0 ? (
                <EmptyState filtered={isFiltered} />
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
                    {/* Resource ID */}
                    <td className="px-4 py-3 font-mono text-xs max-w-[220px]">
                      <span className="block truncate" title={ev.resource_id}>
                        {ev.resource_id}
                      </span>
                    </td>

                    {/* Severity */}
                    <td className="px-4 py-3">
                      <Badge value={ev.severity} map={SEV} />
                    </td>

                    {/* Type */}
                    <td className="px-4 py-3 text-xs text-muted-foreground capitalize">
                      {ev.pr_type ?? '—'}
                    </td>

                    {/* Status */}
                    <td className="px-4 py-3">
                      <Badge value={ev.status} map={STATUS} />
                    </td>

                    {/* Created */}
                    <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
                      <span title={format(new Date(ev.created_at), 'MMM d, yyyy, HH:mm')}>
                        {formatDistanceToNow(new Date(ev.created_at), { addSuffix: true })}
                      </span>
                    </td>

                    {/* PR link */}
                    <td className="px-4 py-3 text-xs" onClick={(e) => e.stopPropagation()}>
                      {ev.pr_number ? (
                        buildPrUrl(repoUrl, githubRepo, ev.pr_number) ? (
                          <a
                            href={buildPrUrl(repoUrl, githubRepo, ev.pr_number)!}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent no-underline whitespace-nowrap"
                          >
                            <ExternalLink size={11} />
                            View PR #{ev.pr_number}
                          </a>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded-md border border-dashed border-muted-foreground/20 bg-muted/30 px-2.5 py-1 text-[11px] text-muted-foreground whitespace-nowrap"
                                title="Set GITHUB_REPO in .env or repo_url on the environment to enable PR links.">
                            PR #{ev.pr_number}
                          </span>
                        )
                      ) : (
                        <span className="text-[11px] text-muted-foreground/40">—</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {!isLoading && total > 0 && (
          <div className="flex items-center justify-between border-t border-border bg-muted/20 px-4 py-2.5">
            <span className="text-xs text-muted-foreground">
              {from}–{to} of {total.toLocaleString()}
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
                className="flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                <ChevronLeft size={13} /> Prev
              </button>
              <span className="px-2 text-xs text-muted-foreground">
                {page + 1} / {totalPages}
              </span>
              <button
                type="button"
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => p + 1)}
                className="flex items-center gap-1 rounded-md px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                Next <ChevronRight size={13} />
              </button>
            </div>
          </div>
        )}
      </div>

      <DetailDrawer event={selected} onClose={() => setSelected(null)} repoUrl={repoUrl} githubRepo={githubRepo} />
    </div>
  );
}
