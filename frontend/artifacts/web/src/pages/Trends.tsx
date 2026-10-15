import { useState } from 'react';
import { eachDayOfInterval, format, subDays } from 'date-fns';
import { errorMessage } from '@/lib/errorUtils';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip,
  AreaChart, Area, ResponsiveContainer, Cell, LabelList,
} from 'recharts';
import {
  Activity, GitPullRequest, CheckCircle, Clock, Layers,
  TrendingUp, AlertTriangle,
} from 'lucide-react';

import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useScope } from '@/hooks/useScope';
import {
  useMostDrifted, useMTTRBySeverity, useDriftVolumeDaily, useDriftSummary,
  usePendingApprovalsSummary,
  type MostDriftedRow, type MTTRRow, type VolumeRow, type DriftSummary,
  type PendingApprovalsSummary,
} from '@/hooks/useTrendsData';
import { PAGE_MAX_WIDTH } from '@/lib/pageLayout';
import { shortenResourceId } from '@/lib/resourceId';

// ── Palette ────────────────────────────────────────────────────────────────

const SEV_COLOR: Record<string, string> = {
  HIGH:   '#ef4444',
  MEDIUM: '#f59e0b',
  LOW:    '#3b82f6',
};

const CHART_BLUE      = '#3b82f6';
const CHART_BLUE_MUTED = '#3b82f630';

// ── DayPicker ──────────────────────────────────────────────────────────────

const DAY_OPTIONS = [
  { label: '7d',  value: 7  },
  { label: '30d', value: 30 },
  { label: '90d', value: 90 },
];

function DayPicker({ value, onChange }: { value: number; onChange: (d: number) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5 gap-0.5">
      {DAY_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={[
            'rounded-md px-3 py-1 text-xs font-medium transition-colors',
            value === opt.value
              ? 'bg-background text-foreground shadow-sm'
              : 'text-muted-foreground hover:text-foreground',
          ].join(' ')}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function safeDate(value: string | number, pattern: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return format(date, pattern);
}

// ── ChartCard ──────────────────────────────────────────────────────────────

function ChartCard({
  title,
  subtitle,
  loading,
  error,
  empty,
  children,
}: {
  title:    string;
  subtitle?: string;
  loading:  boolean;
  error:    Error | null;
  empty:    boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-5 space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-card-foreground">{title}</h2>
        {subtitle && (
          <p className="text-[11px] text-muted-foreground mt-0.5">{subtitle}</p>
        )}
      </div>
      {loading ? (
        <div className="space-y-2 py-6">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-4 text-xs text-destructive">
          <AlertTriangle size={13} className="shrink-0" />
          <span className="truncate">{errorMessage(error)}</span>
        </div>
      ) : empty ? (
        <div className="flex items-center justify-center py-10 text-sm text-muted-foreground">
          No data for this period
        </div>
      ) : (
        children
      )}
    </div>
  );
}

// ── Stat tiles ─────────────────────────────────────────────────────────────

interface TileProps {
  label:   string;
  value:   number | string;
  icon:    React.ReactNode;
  accent?: string;
  hint?:   string;
  /** Full text shown on hover when inline hint is abbreviated. */
  hintDetail?: string;
}

function StatTile({ label, value, icon, accent, hint, hintDetail }: TileProps) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 flex items-start gap-3 h-full min-h-[5.5rem]">
      <div className={`flex h-9 w-9 items-center justify-center rounded-lg shrink-0 ${accent ?? 'bg-muted text-muted-foreground'}`}>
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-[11px] text-muted-foreground leading-none mb-1">{label}</p>
        <p className="text-xl font-semibold text-foreground leading-none tabular-nums">{value}</p>
        {hint && hintDetail ? (
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="mt-1.5 block w-full min-w-0 text-left text-[10px] text-muted-foreground leading-snug underline decoration-dotted decoration-muted-foreground/50 underline-offset-2 cursor-help"
                  aria-label={hintDetail}
                >
                  {hint}
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-xs text-xs leading-relaxed">
                {hintDetail}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ) : hint ? (
          <p className="mt-1.5 text-[10px] text-muted-foreground leading-snug">{hint}</p>
        ) : null}
      </div>
    </div>
  );
}

const OTHER_STATUS_KEYS = ['reverted', 'manual_revert_required', 'suppressed', 'unknown'] as const;

function otherBreakdown(s: DriftSummary): string | undefined {
  const by = s.other_by_status ?? {};
  const parts: string[] = [];
  for (const key of OTHER_STATUS_KEYS) {
    const n = by[key] ?? 0;
    if (n > 0) {
      const label = key === 'manual_revert_required' ? 'manual revert' : key;
      parts.push(`${n} ${label}`);
    }
  }
  // Any statuses outside the known set (forward-compatible).
  for (const [key, n] of Object.entries(by)) {
    if ((OTHER_STATUS_KEYS as readonly string[]).includes(key)) continue;
    if (n > 0) parts.push(`${n} ${key}`);
  }
  return parts.length > 0 ? parts.join(' · ') : undefined;
}

const TRENDS_TILE_GRID =
  'grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6 auto-rows-fr';

function pendingHint(p: PendingApprovalsSummary | undefined): string | undefined {
  if (!p || p.total === 0) return undefined;
  const parts: string[] = [];
  if (p.unmanaged > 0) parts.push(`${p.unmanaged} unmanaged`);
  if (p.security > 0) parts.push(`${p.security} security`);
  if (p.other > 0) parts.push(`${p.other} other`);
  return parts.join(' · ');
}

function StatTiles({
  summary, pending, loading, error,
}: {
  summary: DriftSummary | undefined;
  pending?: PendingApprovalsSummary;
  loading: boolean;
  error?: Error | null;
}) {
  if (loading) {
    return (
      <div className={TRENDS_TILE_GRID}>
        {[...Array(6)].map((_, i) => (
          <div key={i} className="rounded-xl border border-border bg-card p-4 min-h-[5.5rem]">
            <Skeleton className="h-9 w-9 rounded-lg mb-3" />
            <Skeleton className="h-3 w-20 mb-2" />
            <Skeleton className="h-6 w-12" />
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return <p className="text-sm text-destructive">Unable to load trend summary: {errorMessage(error)}</p>;
  }

  const s = summary ?? {
    total: 0, uniqueResources: 0, resolved: 0, open: 0, other: 0, rollback: 0,
  };
  // Prefer server `other`; fall back so older payloads still add up.
  const other = s.other ?? Math.max(0, s.total - s.resolved - s.open);

  return (
    <div className={TRENDS_TILE_GRID}>
      <StatTile
        label="Total Drifts"
        value={s.total.toLocaleString()}
        hint="Accepted + Open + Other"
        icon={<Activity size={16} />}
        accent="bg-primary/10 text-primary"
      />
      <StatTile
        label="Unique Resources"
        value={s.uniqueResources.toLocaleString()}
        icon={<Layers size={16} />}
        accent="bg-violet-100 text-violet-600 dark:bg-violet-900/30 dark:text-violet-400"
      />
      <StatTile
        label="Accepted"
        value={s.resolved.toLocaleString()}
        hint="Finding status = resolved"
        icon={<CheckCircle size={16} />}
        accent="bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400"
      />
      <StatTile
        label="Open"
        value={s.open.toLocaleString()}
        hint={pendingHint(pending) ?? 'Fix/batch findings only'}
        hintDetail={
          pending && pending.total > 0
            ? `${pending.total} awaiting approval on Approvals (unmanaged/security/fix) — not included in Open`
            : 'Drift finding status=open (excludes unmanaged & security_only)'
        }
        icon={<Clock size={16} />}
        accent="bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400"
      />
      <StatTile
        label="Non-open"
        value={other.toLocaleString()}
        hint="Reverted · suppressed · manual"
        hintDetail={otherBreakdown({ ...s, other })}
        icon={<AlertTriangle size={16} />}
        accent="bg-zinc-100 text-zinc-600 dark:bg-zinc-900/30 dark:text-zinc-400"
      />
      <StatTile
        label="Rollback type"
        value={s.rollback.toLocaleString()}
        hint="Subset of total (overlaps status buckets)"
        icon={<GitPullRequest size={16} />}
        accent="bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400"
      />
    </div>
  );
}

// ── Custom tooltips ────────────────────────────────────────────────────────

function VolumeTooltip({ active, payload, label }: {
  active?: boolean; payload?: { value: number }[]; label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="text-muted-foreground mb-1">
        {label ? safeDate(label, 'MMM d, yyyy') : ''}
      </p>
      <p className="font-semibold text-foreground">{payload[0].value} events</p>
    </div>
  );
}

function MTTRTooltip({ active, payload }: {
  active?: boolean;
  payload?: { payload: MTTRRow }[];
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-md space-y-1">
      <p className="font-semibold" style={{ color: SEV_COLOR[d.severity] ?? '#6b7280' }}>
        {d.severity}
      </p>
      <p className="text-foreground">
        Avg time to resolve:{' '}
        <strong>
          {d.count === 0 || d.avg_hours == null ? '—' : `${Number(d.avg_hours).toFixed(1)} h`}
        </strong>
      </p>
      <p className="text-muted-foreground">{d.count} event{d.count !== 1 ? 's' : ''}</p>
    </div>
  );
}

function DriftedTooltip({ active, payload }: {
  active?: boolean; payload?: { payload: MostDriftedRow }[];
}) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-md space-y-1">
      <p className="font-mono text-foreground break-all">{d.resource_id}</p>
      <p className="text-muted-foreground"><strong className="text-foreground">{d.drift_count}</strong> drifts</p>
    </div>
  );
}

// ── Axis tick helpers ──────────────────────────────────────────────────────

const TICK_STYLE = { fontSize: 11, fill: 'hsl(var(--muted-foreground))' };

type MTTRChartRow = MTTRRow & { chart_hours: number | null };

/** Zero-fill missing days so the volume chart shows every date in the range. */
function fillVolumeDays(rows: VolumeRow[], days: number): VolumeRow[] {
  const end = new Date();
  end.setHours(0, 0, 0, 0);
  const start = subDays(end, Math.max(0, days - 1));
  const byDay = new Map(
    rows.map((r) => [r.day.slice(0, 10), Number(r.count) || 0]),
  );
  return eachDayOfInterval({ start, end }).map((d) => {
    const key = format(d, 'yyyy-MM-dd');
    return { day: key, count: byDay.get(key) ?? 0 };
  });
}

function VolumeTick({ x, y, payload }: { x?: number; y?: number; payload?: { value: string } }) {
  if (!payload?.value) return null;
  const date = new Date(payload.value);
  if (Number.isNaN(date.getTime())) return null;
  return (
    <text x={x} y={y! + 10} textAnchor="middle" style={TICK_STYLE}>
      {format(date, 'MMM d')}
    </text>
  );
}

function ResourceTick({ x, y, payload }: { x?: number; y?: number; payload?: { value: string } }) {
  if (!payload?.value) return null;
  return (
    <text x={x! - 6} y={y! + 4} textAnchor="end" style={TICK_STYLE}>
      <title>{payload.value}</title>
      {shortenResourceId(payload.value, 40)}
    </text>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Trends() {
  const { scope } = useScope();
  const [days, setDays] = useState(30);

  const mostDrifted = useMostDrifted(scope, days);
  const mttr        = useMTTRBySeverity(scope, days);
  const volume      = useDriftVolumeDaily(scope, days);
  const summary     = useDriftSummary(scope, days);
  const pending     = usePendingApprovalsSummary(scope, days);

  // ── Most drifted — horizontal bar chart ────────────────────────────────
  // Reverse so highest bar is at top
  const mostDriftedData = [...(mostDrifted.data ?? [])].reverse();

  // ── MTTR — sorted HIGH → MEDIUM → LOW ─────────────────────────────────
  const SEV_ORDER = ['HIGH', 'MEDIUM', 'LOW'];
  const mttrData = [...(mttr.data ?? [])].sort(
    (a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity),
  );
  const mttrChartData: MTTRChartRow[] = mttrData.map((row) => ({
    ...row,
    chart_hours:
      row.count > 0 && row.avg_hours != null && !Number.isNaN(row.avg_hours)
        ? row.avg_hours
        : null,
  }));
  const mttrResolvedCount = mttrData.reduce((n, r) => n + r.count, 0);
  const mttrEmpty = !mttr.isLoading && mttrResolvedCount === 0;

  const volumeData = fillVolumeDays(volume.data ?? [], days);

  const mostDriftedChartHeight = Math.max(288, mostDriftedData.length * 34);
  const mostDriftedLabelWidth = Math.min(
    280,
    Math.max(
      160,
      ...mostDriftedData.map((r) => shortenResourceId(r.resource_id, 40).length * 6.2),
    ),
  );

  return (
    <div className={`p-6 space-y-6 ${PAGE_MAX_WIDTH}`}>
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <TrendingUp size={18} className="text-muted-foreground" />
          <h1 className="text-xl font-semibold">Trends</h1>
        </div>
        <DayPicker value={days} onChange={setDays} />
      </div>

      {/* Stat tiles */}
      <StatTiles
        summary={summary.data}
        pending={pending.data}
        loading={summary.isLoading}
        error={summary.error as Error | null}
      />

      {/* Two-column charts */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">

        {/* Most Drifted Resources */}
        <ChartCard
          title="Most Drifted Resources"
          subtitle="Total events by resource in the selected period"
          loading={mostDrifted.isLoading}
          error={mostDrifted.error as Error | null}
          empty={!mostDrifted.isLoading && (mostDrifted.data?.length ?? 0) === 0}
        >
          <div style={{ height: mostDriftedChartHeight }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={mostDriftedData}
                layout="vertical"
                margin={{ top: 4, right: 24, bottom: 4, left: 8 }}
              >
                <CartesianGrid
                  horizontal={false}
                  strokeDasharray="3 3"
                  stroke="hsl(var(--border))"
                />
                <XAxis
                  type="number"
                  allowDecimals={false}
                  tick={TICK_STYLE}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="resource_id"
                  width={mostDriftedLabelWidth}
                  interval={0}
                  tick={<ResourceTick />}
                  axisLine={false}
                  tickLine={false}
                />
                <RechartsTooltip
                  content={<DriftedTooltip />}
                  cursor={{ fill: 'hsl(var(--muted))', radius: 4 }}
                />
                <Bar
                  dataKey="drift_count"
                  name="Drifts"
                  fill={CHART_BLUE}
                  radius={[0, 4, 4, 0]}
                  maxBarSize={18}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>

        {/* MTTR by Severity */}
        <ChartCard
          title="Mean Time to Resolve by Severity"
          subtitle="Resolved fix/batch PRs with resolved_at in this period. No resolved events → empty chart (not zero-hour bars)."
          loading={mttr.isLoading}
          error={mttr.error as Error | null}
          empty={mttrEmpty}
        >
          {mttrEmpty ? null : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={mttrChartData}
                margin={{ top: 20, right: 24, bottom: 4, left: 8 }}
              >
                <CartesianGrid
                  vertical={false}
                  strokeDasharray="3 3"
                  stroke="hsl(var(--border))"
                />
                <XAxis
                  dataKey="severity"
                  tick={TICK_STYLE}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={TICK_STYLE}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}h`}
                />
                <RechartsTooltip
                  content={<MTTRTooltip />}
                  cursor={{ fill: 'hsl(var(--muted))', radius: 4 }}
                />
                <Bar
                  dataKey="chart_hours"
                  name="Avg hours"
                  radius={[4, 4, 0, 0]}
                  maxBarSize={52}
                >
                  {mttrChartData.map((entry) => (
                    <Cell
                      key={entry.severity}
                      fill={SEV_COLOR[entry.severity] ?? '#6b7280'}
                    />
                  ))}
                  <LabelList
                    dataKey="chart_hours"
                    position="top"
                    style={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                    formatter={(v: number) =>
                      v == null || Number.isNaN(v) || v <= 0 ? '' : `${v.toFixed(1)}h`}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          )}
          {!mttrEmpty && mttrData.some((r) => r.count > 0 && (r.avg_hours ?? 0) <= 0) && (
            <p className="text-[11px] text-muted-foreground text-center">
              Resolved events in this period had near-zero time to resolve (same-day fixes).
            </p>
          )}
          {/* Legend */}
          {!mttrEmpty && mttrData.length > 0 && (
            <div className="flex gap-4 justify-center pt-1">
              {mttrData.map((row) => (
                <div key={row.severity} className="flex items-center gap-1.5">
                  <span
                    className="h-2.5 w-2.5 rounded-sm shrink-0"
                    style={{ background: SEV_COLOR[row.severity] ?? '#6b7280' }}
                  />
                  <span className="text-[11px] text-muted-foreground">
                    {row.severity}
                    {row.count > 0 && row.avg_hours != null
                      ? ` · ${row.avg_hours.toFixed(1)}h (${row.count})`
                      : ' · no resolved events'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </ChartCard>
      </div>

      {/* Drift Volume Over Time — full width */}
      <ChartCard
        title="Drift Volume Over Time"
        loading={volume.isLoading}
        error={volume.error as Error | null}
        empty={!volume.isLoading && (volume.data?.length ?? 0) === 0}
      >
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={volumeData}
              margin={{ top: 4, right: 24, bottom: 4, left: 8 }}
            >
              <defs>
                <linearGradient id="driftGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={CHART_BLUE} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={CHART_BLUE} stopOpacity={0}    />
                </linearGradient>
              </defs>
              <CartesianGrid
                vertical={false}
                strokeDasharray="3 3"
                stroke="hsl(var(--border))"
              />
              <XAxis
                dataKey="day"
                tick={<VolumeTick />}
                axisLine={false}
                tickLine={false}
                interval={days <= 31 ? 0 : 'preserveStartEnd'}
                minTickGap={days <= 31 ? 0 : 28}
              />
              <YAxis
                allowDecimals={false}
                tick={TICK_STYLE}
                axisLine={false}
                tickLine={false}
              />
              <RechartsTooltip
                content={<VolumeTooltip />}
                cursor={{ stroke: 'hsl(var(--border))', strokeWidth: 1 }}
              />
              <Area
                type="monotone"
                dataKey="count"
                name="Events"
                stroke={CHART_BLUE}
                strokeWidth={2}
                fill="url(#driftGradient)"
                dot={false}
                activeDot={{ r: 4, fill: CHART_BLUE, stroke: CHART_BLUE_MUTED, strokeWidth: 6 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    </div>
  );
}
