import { useState } from 'react';
import { format, parseISO } from 'date-fns';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  AreaChart, Area, ResponsiveContainer, Cell,
} from 'recharts';
import {
  Activity, GitPullRequest, CheckCircle, Clock, Layers,
  TrendingUp, AlertTriangle,
} from 'lucide-react';

import { Skeleton } from '@/components/ui/skeleton';
import { useScope } from '@/hooks/useScope';
import {
  useMostDrifted, useMTTRBySeverity, useDriftVolumeDaily, useDriftSummary,
  type MostDriftedRow, type MTTRRow, type VolumeRow, type DriftSummary,
} from '@/hooks/useTrendsData';

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

// ── ChartCard ──────────────────────────────────────────────────────────────

function ChartCard({
  title,
  loading,
  error,
  empty,
  children,
}: {
  title:    string;
  loading:  boolean;
  error:    Error | null;
  empty:    boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-5 space-y-3">
      <h2 className="text-sm font-semibold text-card-foreground">{title}</h2>
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
          <span className="truncate">{error.message}</span>
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
}

function StatTile({ label, value, icon, accent }: TileProps) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 flex items-center gap-3">
      <div className={`flex h-9 w-9 items-center justify-center rounded-lg shrink-0 ${accent ?? 'bg-muted text-muted-foreground'}`}>
        {icon}
      </div>
      <div>
        <p className="text-[11px] text-muted-foreground leading-none mb-1">{label}</p>
        <p className="text-xl font-semibold text-foreground leading-none">{value}</p>
      </div>
    </div>
  );
}

function StatTiles({ summary, loading }: { summary: DriftSummary | undefined; loading: boolean }) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {[...Array(5)].map((_, i) => (
          <div key={i} className="rounded-xl border border-border bg-card p-4">
            <Skeleton className="h-9 w-9 rounded-lg mb-3" />
            <Skeleton className="h-3 w-20 mb-2" />
            <Skeleton className="h-6 w-12" />
          </div>
        ))}
      </div>
    );
  }

  const s = summary ?? { total: 0, uniqueResources: 0, resolved: 0, open: 0, rollback: 0 };

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <StatTile
        label="Total Drifts"
        value={s.total.toLocaleString()}
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
        label="Resolved"
        value={s.resolved.toLocaleString()}
        icon={<CheckCircle size={16} />}
        accent="bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400"
      />
      <StatTile
        label="Unresolved"
        value={s.open.toLocaleString()}
        icon={<Clock size={16} />}
        accent="bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400"
      />
      <StatTile
        label="Rollbacks"
        value={s.rollback.toLocaleString()}
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
        {label ? format(parseISO(label), 'MMM d, yyyy') : ''}
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
        Avg time to resolve: <strong>{d.avg_hours.toFixed(1)} h</strong>
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

function truncate(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

const TICK_STYLE = { fontSize: 11, fill: 'hsl(var(--muted-foreground))' };

function VolumeTick({ x, y, payload }: { x?: number; y?: number; payload?: { value: string } }) {
  if (!payload?.value) return null;
  return (
    <text x={x} y={y! + 10} textAnchor="middle" style={TICK_STYLE}>
      {format(parseISO(payload.value), 'MMM d')}
    </text>
  );
}

function ResourceTick({ x, y, payload }: { x?: number; y?: number; payload?: { value: string } }) {
  if (!payload?.value) return null;
  return (
    <text x={x! - 6} y={y! + 4} textAnchor="end" style={TICK_STYLE}>
      {truncate(payload.value, 28)}
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

  // ── Most drifted — horizontal bar chart ────────────────────────────────
  // Reverse so highest bar is at top
  const mostDriftedData = [...(mostDrifted.data ?? [])].reverse();

  // ── MTTR — sorted HIGH → MEDIUM → LOW ─────────────────────────────────
  const SEV_ORDER = ['HIGH', 'MEDIUM', 'LOW'];
  const mttrData = [...(mttr.data ?? [])].sort(
    (a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity),
  );

  // ── Volume — sorted chronologically ───────────────────────────────────
  const volumeData = [...(volume.data ?? [])].sort(
    (a, b) => a.day.localeCompare(b.day),
  );

  return (
    <div className="p-6 space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <TrendingUp size={18} className="text-muted-foreground" />
          <h1 className="text-xl font-semibold">Trends</h1>
        </div>
        <DayPicker value={days} onChange={setDays} />
      </div>

      {/* Stat tiles */}
      <StatTiles summary={summary.data} loading={summary.isLoading} />

      {/* Two-column charts */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">

        {/* Most Drifted Resources */}
        <ChartCard
          title="Most Drifted Resources"
          loading={mostDrifted.isLoading}
          error={mostDrifted.error as Error | null}
          empty={!mostDrifted.isLoading && (mostDrifted.data?.length ?? 0) === 0}
        >
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={mostDriftedData}
                layout="vertical"
                margin={{ top: 4, right: 24, bottom: 4, left: 180 }}
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
                  width={176}
                  tick={<ResourceTick />}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
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
          loading={mttr.isLoading}
          error={mttr.error as Error | null}
          empty={!mttr.isLoading && (mttr.data?.length ?? 0) === 0}
        >
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={mttrData}
                margin={{ top: 4, right: 24, bottom: 4, left: 8 }}
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
                <Tooltip
                  content={<MTTRTooltip />}
                  cursor={{ fill: 'hsl(var(--muted))', radius: 4 }}
                />
                <Bar
                  dataKey="avg_hours"
                  name="Avg hours"
                  radius={[4, 4, 0, 0]}
                  maxBarSize={52}
                >
                  {mttrData.map((entry) => (
                    <Cell
                      key={entry.severity}
                      fill={SEV_COLOR[entry.severity] ?? '#6b7280'}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          {/* Legend */}
          {mttrData.length > 0 && (
            <div className="flex gap-4 justify-center pt-1">
              {mttrData.map((row) => (
                <div key={row.severity} className="flex items-center gap-1.5">
                  <span
                    className="h-2.5 w-2.5 rounded-sm shrink-0"
                    style={{ background: SEV_COLOR[row.severity] ?? '#6b7280' }}
                  />
                  <span className="text-[11px] text-muted-foreground">
                    {row.severity} · {row.count}
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
                // Show at most ~7 evenly-spaced ticks regardless of range
                interval={Math.max(0, Math.floor(volumeData.length / 7) - 1)}
              />
              <YAxis
                allowDecimals={false}
                tick={TICK_STYLE}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
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
