import { useMemo } from 'react';
import { formatDistanceToNow } from 'date-fns';
import { AlertTriangle, RotateCcw, DollarSign, Clock, Inbox } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { useScope } from '@/hooks/useScope';
import { useOverviewData } from '@/hooks/useOverviewData';

// ── Helpers ────────────────────────────────────────────────────────────────

function formatCost(usd: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
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
  HIGH:   { label: 'High',   bg: 'bg-red-50   dark:bg-red-950/40',    text: 'text-red-700   dark:text-red-400',    dot: 'bg-red-500'    },
  MEDIUM: { label: 'Medium', bg: 'bg-amber-50  dark:bg-amber-950/40', text: 'text-amber-700 dark:text-amber-400', dot: 'bg-amber-500'  },
  LOW:    { label: 'Low',    bg: 'bg-blue-50   dark:bg-blue-950/40',   text: 'text-blue-700  dark:text-blue-400',  dot: 'bg-blue-500'   },
};
const SEVERITY_ORDER = ['HIGH', 'MEDIUM', 'LOW'];

// ── Sub-components ─────────────────────────────────────────────────────────

function StatCard({
  icon: Icon,
  label,
  value,
  loading,
  children,
}: {
  icon: React.ElementType;
  label: string;
  value?: React.ReactNode;
  loading: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2 text-muted-foreground">
        <Icon size={14} />
        <span className="text-xs font-medium uppercase tracking-wider">{label}</span>
      </div>
      {loading ? (
        <div className="space-y-2">
          <Skeleton className="h-8 w-24" />
          {children && <Skeleton className="h-4 w-32" />}
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

// ── Empty state ────────────────────────────────────────────────────────────

function EmptyState() {
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

// ── Page ───────────────────────────────────────────────────────────────────

export default function Overview() {
  const { scope } = useScope();
  const { severitySummary, rollbackCount, lastScan, costImpact } = useOverviewData(scope);

  const isLoading =
    severitySummary.isLoading ||
    rollbackCount.isLoading ||
    lastScan.isLoading ||
    costImpact.isLoading;

  // Build a severity → count map
  const severityMap = useMemo(() => {
    const map: Record<string, number> = {};
    for (const row of severitySummary.data ?? []) {
      map[row.severity.toUpperCase()] = Number(row.count);
    }
    return map;
  }, [severitySummary.data]);

  // Total open drift events across all severities
  const totalDrift = useMemo(
    () => Object.values(severityMap).reduce((a, b) => a + b, 0),
    [severityMap],
  );

  // Empty state: queries succeeded but no events exist for this scope
  const isEmpty =
    !isLoading &&
    severitySummary.isSuccess &&
    lastScan.isSuccess &&
    lastScan.data === null &&
    totalDrift === 0;

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">Overview</h1>
        {scope && (
          <p className="mt-0.5 text-sm text-muted-foreground">Scope: {scope}</p>
        )}
      </div>

      {isEmpty ? (
        <EmptyState />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {/* ── Severity breakdown ─────────────────────────────────────── */}
          <StatCard icon={AlertTriangle} label="Open Drift" loading={isLoading}>
            {!isLoading && (
              <div className="space-y-2">
                <p className="text-2xl font-semibold tabular-nums text-card-foreground">
                  {totalDrift}
                </p>
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {SEVERITY_ORDER.map((sev) => {
                    const style  = SEVERITY_STYLE[sev];
                    const count  = severityMap[sev] ?? 0;
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
              </div>
            )}
          </StatCard>

          {/* ── Open rollback count ────────────────────────────────────── */}
          <StatCard
            icon={RotateCcw}
            label="Open Rollbacks"
            value={isLoading ? undefined : (rollbackCount.data ?? 0)}
            loading={isLoading}
          />

          {/* ── Monthly cost impact ────────────────────────────────────── */}
          <StatCard
            icon={DollarSign}
            label="Est. Monthly Cost"
            value={isLoading ? undefined : formatCost(costImpact.data ?? 0)}
            loading={isLoading}
          />

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
          />
        </div>
      )}
    </div>
  );
}
