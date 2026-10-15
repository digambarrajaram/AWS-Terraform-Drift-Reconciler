import { useMemo } from 'react';
import { format, formatDistanceToNow } from 'date-fns';
import {
  AlertTriangle, RotateCcw, DollarSign, Clock,
  Inbox, ServerCrash, Globe,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { useScope } from '@/hooks/useScope';
import { useOverviewData } from '@/hooks/useOverviewData';
import { useEnvironments } from '@/hooks/useEnvironments';
import { useExceptions } from '@/hooks/useExceptions';
import { Link, useLocation } from 'react-router-dom';
import { errorMessage, isRetryable } from '@/lib/errorUtils';
import { PAGE_MAX_WIDTH } from '@/lib/pageLayout';

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatCost(usd: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD',
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(usd);
}

function relativeTime(iso: string): string {
  try {
    return formatDistanceToNow(new Date(iso), { addSuffix: true });
  } catch {
    return '—';
  }
}

const SEVERITY_STYLE: Record<string, { label: string; bg: string; text: string; dot: string }> = {
  HIGH:   { label: 'High',   bg: 'bg-red-50   dark:bg-red-950/40',    text: 'text-red-700   dark:text-red-400',    dot: 'bg-red-500'   },
  MEDIUM: { label: 'Medium', bg: 'bg-amber-50  dark:bg-amber-950/40', text: 'text-amber-700 dark:text-amber-400', dot: 'bg-amber-500' },
  LOW:    { label: 'Low',    bg: 'bg-blue-50   dark:bg-blue-950/40',   text: 'text-blue-700  dark:text-blue-400',  dot: 'bg-blue-500'  },
  OTHER:  { label: 'Other',  bg: 'bg-zinc-50  dark:bg-zinc-950/40',  text: 'text-zinc-700  dark:text-zinc-400',  dot: 'bg-zinc-500'  },
};
const SEVERITY_ORDER = ['HIGH', 'MEDIUM', 'LOW', 'OTHER'];

// ── StatCard ─────────────────────────────────────────────────────────────────

function StatCard({
  icon: Icon, label, value, loading, error, children,
}: {
  icon:      React.ElementType;
  label:     string;
  value?:    React.ReactNode;
  loading:   boolean;
  error?:    unknown;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-sm flex flex-col">
      <div className="mb-3 flex items-center gap-2 text-muted-foreground">
        <Icon size={14} />
        <span className="text-xs font-medium uppercase tracking-wider">{label}</span>
      </div>

      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-8 w-24" />
          {children && <Skeleton className="h-4 w-32" />}
        </div>
      ) : error ? (
        <div className="flex items-start gap-1.5">
          <ServerCrash size={14} className="shrink-0 mt-0.5 text-destructive/70" />
          <p className="text-xs text-destructive/80 leading-snug break-words">
            {errorMessage(error)}
          </p>
        </div>
      ) : (
        <>
          {value !== undefined && (
            <p className="text-2xl font-semibold tabular-nums text-card-foreground">{value}</p>
          )}
          {children}
        </>
      )}
    </div>
  );
}

// ── Empty states ─────────────────────────────────────────────────────────────

/** No environments have been added to Drift yet. */
function NoEnvironmentsState() {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
      <div className="flex items-center justify-center h-14 w-14 rounded-full bg-muted">
        <Globe size={24} className="text-muted-foreground" />
      </div>
      <div className="space-y-1">
        <p className="text-base font-semibold text-foreground">No environments configured</p>
        <p className="text-sm text-muted-foreground max-w-xs">
          Add an environment on the{' '}
          <a href="/environments" className="text-primary hover:underline">Environments</a>{' '}
          page to start monitoring infrastructure drift.
        </p>
      </div>
    </div>
  );
}

/** Environment exists but has no drift events yet. */
function NoEventsState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
      <Inbox size={40} className="text-muted-foreground/50" />
      <p className="text-base font-medium text-muted-foreground">No drift events found</p>
      <p className="text-sm text-muted-foreground/70">
        Run a scan for this scope to start tracking drift.
      </p>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function Overview() {
  const { search } = useLocation();
  const { scope } = useScope();
  const { activeEnvironments, isLoading: envsLoading } = useEnvironments();
  const {
    severitySummary, openCount, lastScanDrift, rollbackCount, lastScan,
    costImpact, costResourceCount,
  } = useOverviewData(scope);
  const { data: exceptionsData } = useExceptions(scope);
  const unmanagedExcCount = exceptionsData?.unmanaged_exceptions?.length ?? 0;
  const securityExcCount = exceptionsData?.security_exceptions?.length ?? 0;

  const isLoading =
    envsLoading ||
    !scope ||
    severitySummary.isLoading ||
    lastScanDrift.isLoading ||
    rollbackCount.isLoading ||
    lastScan.isLoading ||
    costImpact.isLoading;

  // Any query error — show inline per-card, plus a top banner if 502/network
  const anyError =
    severitySummary.error ??
    lastScanDrift.error   ??
    rollbackCount.error   ??
    lastScan.error        ??
    costImpact.error      ?? null;

  const topBannerMsg =
    anyError && isRetryable(anyError) ? errorMessage(anyError) : null;

  // Severity from last scan findings (current reality), not open tickets.
  const severityMap = useMemo(() => {
    const map: Record<string, number> = {};
    for (const row of lastScanDrift.data?.severity ?? []) {
      const severity = (row.severity ?? 'OTHER').toString().toUpperCase();
      const key = SEVERITY_STYLE[severity] ? severity : 'OTHER';
      const count = Number(row.count);
      if (Number.isFinite(count)) {
        map[key] = (map[key] ?? 0) + count;
      }
    }
    return map;
  }, [lastScanDrift.data?.severity]);

  const currentDrift = lastScanDrift.data?.count ?? 0;
  const driftSkipped = !!lastScanDrift.data?.skipped;
  const unresolvedOpen = openCount.data ?? 0;

  const costTotal = costImpact.data ?? 0;
  const costResources = costResourceCount.data ?? 0;
  const activeEnv = activeEnvironments.find((e) => e.slug === scope);
  const rollbackEligible = rollbackCount.data ?? 0;
  const lastScanAtLabel = lastScan.data
    ? format(new Date(lastScan.data), 'MMM d, HH:mm')
    : null;
  const lastScanAtFull = lastScan.data
    ? format(new Date(lastScan.data), 'MMM d, yyyy HH:mm')
    : null;

  // No environments at all
  if (!envsLoading && activeEnvironments.length === 0) {
    return (
      <div className={`p-6 ${PAGE_MAX_WIDTH}`}>
        <h1 className="text-xl font-semibold text-foreground">Overview</h1>
        <NoEnvironmentsState />
      </div>
    );
  }

  // Scope has no events yet (all queries succeeded with empty results)
  const isEmpty =
    !isLoading &&
    !anyError &&
    severitySummary.isSuccess &&
    lastScan.isSuccess &&
    lastScan.data === null &&
    currentDrift === 0 &&
    unresolvedOpen === 0;

  return (
    <div className={`p-6 space-y-6 ${PAGE_MAX_WIDTH}`}>
      <div>
        <h1 className="text-xl font-semibold text-foreground">Overview</h1>
        {scope && (
          <p className="mt-0.5 text-sm text-muted-foreground">
            {activeEnv ? (
              <>
                <span className="text-foreground font-medium">{activeEnv.name}</span>
                <span className="font-mono text-xs ml-1.5">({activeEnv.slug})</span>
              </>
            ) : (
              <>Scope: <span className="font-mono text-xs">{scope}</span></>
            )}
          </p>
        )}
      </div>

      {/* Transient/network error banner — retryable errors warrant a prominent note */}
      {topBannerMsg && (
        <div className="flex items-center gap-2.5 rounded-xl border border-amber-300/60 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700/40 dark:bg-amber-900/20 dark:text-amber-300">
          <AlertTriangle size={15} className="shrink-0" />
          <span>{topBannerMsg} — some cards may show stale data.</span>
        </div>
      )}

      {isEmpty ? (
        <NoEventsState />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4 items-start">
          {/* ── Current drift from last scan (matches Scan History) ───── */}
          <StatCard
            icon={AlertTriangle}
            label="Current Drift"
            loading={isLoading}
            error={lastScanDrift.error}
          >
            {!isLoading && !lastScanDrift.error && (
              <div className="space-y-2">
                {driftSkipped ? (
                  <>
                    <p className="text-2xl font-semibold tabular-nums text-muted-foreground">—</p>
                    <p className="text-xs text-muted-foreground leading-snug">
                      {lastScanDrift.data?.reason ?? 'Last scan did not check configuration drift'}
                    </p>
                  </>
                ) : lastScan.data === null ? (
                  <>
                    <p className="text-2xl font-semibold tabular-nums text-muted-foreground">—</p>
                    <p className="text-xs text-muted-foreground">No completed scan yet</p>
                  </>
                ) : (
                  <>
                    <p className="text-2xl font-semibold tabular-nums text-card-foreground">
                      {currentDrift}
                    </p>
                    {currentDrift > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {SEVERITY_ORDER.map((sev) => {
                          const style = SEVERITY_STYLE[sev];
                          const count = severityMap[sev] ?? 0;
                          if (count === 0) return null;
                          return (
                            <span
                              key={sev}
                              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${style.bg} ${style.text}`}
                            >
                              <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
                              {style.label}: {count}
                            </span>
                          );
                        })}
                      </div>
                    )}
                    <p className="text-xs text-muted-foreground leading-snug">
                      {currentDrift} config drift
                      {lastScanAtLabel ? ` (scan ${lastScanAtLabel})` : ''}.
                      {' '}
                      {unresolvedOpen} open unmanaged/security ticket
                      {unresolvedOpen === 1 ? '' : 's'}.
                    </p>
                  </>
                )}
              </div>
            )}
          </StatCard>

          {/* ── Eligible-for-rollback count (resolved fixes, not backlog) ─ */}
          <StatCard
            icon={RotateCcw}
            label="Eligible for Rollback"
            loading={isLoading}
            error={rollbackCount.error}
          >
            {!isLoading && !rollbackCount.error && (
              <div className="space-y-1">
                <p className="text-2xl font-semibold tabular-nums text-card-foreground">
                  {rollbackEligible}
                </p>
                <p className="text-xs text-muted-foreground leading-snug">
                  {rollbackEligible === 0
                    ? 'No resolved fix/batch PRs with a rollback baseline on the Rollback page'
                    : 'Resolved PRs with a stored baseline — open Rollback to preview or execute'}
                </p>
              </div>
            )}
          </StatCard>

          {/* ── Monthly cost impact ────────────────────────────────────── */}
          <StatCard
            icon={DollarSign}
            label="Est. Monthly Cost"
            loading={isLoading}
            error={costImpact.error}
          >
            {!isLoading && !costImpact.error && (
              costResources === 0 ? (
                <div className="space-y-1">
                  <p className="text-2xl font-semibold tabular-nums text-muted-foreground">—</p>
                  <p className="text-xs text-muted-foreground leading-snug">
                    No priced open findings for this scope. Estimates apply to unmanaged resources
                    (EC2, NAT gateway, etc.) after a scan — see{' '}
                    <Link to="/explorer" className="text-primary hover:underline">Explorer</Link>
                    . Exception cost caps only affect suppression, not pricing.
                  </p>
                </div>
              ) : (
                <div className="space-y-1">
                  <p className="text-2xl font-semibold tabular-nums text-card-foreground">
                    {formatCost(costTotal)}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    across {costResources} resource{costResources === 1 ? '' : 's'}
                  </p>
                </div>
              )
            )}
          </StatCard>

          {/* ── Last scan timestamp ────────────────────────────────────── */}
          <StatCard
            icon={Clock}
            label="Last Scan"
            value={
              isLoading
                ? undefined
                : lastScan.data
                  ? relativeTime(lastScan.data)
                  : '—'
            }
            loading={isLoading}
            error={lastScan.error}
          >
            {!isLoading && !lastScan.error && lastScanAtFull && (
              <p className="mt-1 text-xs text-muted-foreground tabular-nums">{lastScanAtFull}</p>
            )}
          </StatCard>
        </div>
      )}

      {!isEmpty && !isLoading && (
        <section className="rounded-xl border border-border bg-card p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-card-foreground mb-3">Quick links</h2>
          <div className="flex flex-wrap gap-2">
            {[
              { to: '/scan', label: 'Run scan' },
              { to: '/explorer', label: 'Explorer' },
              { to: '/pr-queue', label: 'PR queue' },
              { to: '/trends', label: 'Trends' },
              {
                to: '/rollback',
                label: rollbackEligible > 0 ? `Rollback (${rollbackEligible})` : 'Rollback',
              },
              {
                to: '/exceptions',
                label: (unmanagedExcCount + securityExcCount) > 0
                  ? `Exceptions (${unmanagedExcCount} unm. · ${securityExcCount} sec.)`
                  : 'Exceptions',
              },
            ].map(({ to, label }) => (
              <Link
                key={to}
                to={{ pathname: to, search }}
                className="rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-accent transition-colors"
              >
                {label}
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
