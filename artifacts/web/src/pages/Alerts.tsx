import { useState } from 'react';
import { toast } from 'sonner';
import {
  CheckCircle2, XCircle, Eye, EyeOff, Send, Save,
  Bell, Globe, SlidersHorizontal, Loader2,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { useScope } from '@/hooks/useScope';
import {
  useNotificationSettings, useSaveCredential, useSendTest,
  useRoutingRules, useSaveRoutingRule,
  type Severity, type Channel, type RoutingRule,
} from '@/hooks/useAlerts';

// ── Constants ──────────────────────────────────────────────────────────────

const SEVERITIES: Severity[] = ['HIGH', 'MEDIUM', 'LOW'];
const CHANNELS: { value: Channel; label: string }[] = [
  { value: 'pagerduty', label: 'PagerDuty' },
  { value: 'slack',     label: 'Slack'     },
  { value: 'none',      label: 'None'      },
];

const SEV_STYLE: Record<Severity, string> = {
  HIGH:   'bg-red-100   text-red-700   dark:bg-red-900/30   dark:text-red-400',
  MEDIUM: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  LOW:    'bg-blue-100  text-blue-700  dark:bg-blue-900/30  dark:text-blue-400',
};

// ── Helpers ────────────────────────────────────────────────────────────────

function SevBadge({ sev }: { sev: Severity }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${SEV_STYLE[sev]}`}>
      {sev}
    </span>
  );
}

function ConfiguredDot({ ok }: { ok: boolean }) {
  return ok
    ? <CheckCircle2 size={14} className="text-emerald-500 shrink-0" />
    : <XCircle     size={14} className="text-muted-foreground/40 shrink-0" />;
}

// ── CredentialRow ──────────────────────────────────────────────────────────

function CredentialRow({
  label,
  field,
  channel,
  configured,
  masked,
  scope,
}: {
  label:      string;
  field:      string;
  channel:    'pagerduty' | 'slack';
  configured: boolean;
  masked:     string | null;
  scope:      string | null;
}) {
  const [value,    setValue]    = useState('');
  const [showVal,  setShowVal]  = useState(false);
  const [testing,  setTesting]  = useState(false);

  const save    = useSaveCredential();
  const sendTest = useSendTest();

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!value.trim()) return;
    try {
      await save.mutateAsync({ field, value: value.trim() });
      toast.success(`${label} updated`);
      setValue('');
    } catch (err) {
      toast.error(`Failed to save ${label}`, {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function handleTest() {
    setTesting(true);
    try {
      await sendTest.mutateAsync({ channel, ...(scope ? { scope } : {}) });
      toast.success(`Test ${label} sent successfully`);
    } catch (err) {
      toast.error(`${label} test failed`, {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-card p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <ConfiguredDot ok={configured} />
          <span className="text-sm font-semibold text-card-foreground">{label}</span>
          {configured
            ? <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">Configured</span>
            : <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-muted-foreground">Not configured</span>}
        </div>
        {/* Send test */}
        <button
          type="button"
          onClick={handleTest}
          disabled={!configured || testing}
          className="flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent disabled:pointer-events-none disabled:opacity-40"
        >
          {testing ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
          Send Test
        </button>
      </div>

      {/* Masked current value */}
      {masked && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-mono tracking-widest">
            {showVal ? masked : masked.replace(/[^•]/g, '•').slice(0, 8) + masked.slice(-4)}
          </span>
          <button type="button" onClick={() => setShowVal((v) => !v)}
            className="text-muted-foreground hover:text-foreground transition-colors">
            {showVal ? <EyeOff size={12} /> : <Eye size={12} />}
          </button>
        </div>
      )}

      {/* Update form */}
      <form onSubmit={handleSave} className="flex items-center gap-2">
        <input
          type={showVal ? 'text' : 'password'}
          placeholder={`Enter new ${label}…`}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoComplete="off"
          className="flex-1 rounded-md border border-input bg-background px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring min-w-0"
        />
        <button
          type="submit"
          disabled={!value.trim() || save.isPending}
          className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap"
        >
          {save.isPending ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          Save
        </button>
      </form>
    </div>
  );
}

// ── RoutingRuleRow ─────────────────────────────────────────────────────────

function RoutingRuleRow({
  severity,
  scopeLabel,
  scopeValue,
  existingRule,
  onSave,
  saving,
}: {
  severity:     Severity;
  scopeLabel:   string;
  scopeValue:   string | null; // null = global
  existingRule: RoutingRule | undefined;
  onSave:       (severity: Severity, channel: Channel, scope: string | null) => void;
  saving:       boolean;
}) {
  const current = existingRule?.channel ?? 'none';
  const [channel, setChannel] = useState<Channel>(current);
  const dirty = channel !== current;

  return (
    <tr className="border-b border-border last:border-0">
      <td className="px-4 py-3">
        <SevBadge sev={severity} />
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {scopeValue === null
            ? <><Globe size={12} className="shrink-0" /> Global default</>
            : <><SlidersHorizontal size={12} className="shrink-0" /> {scopeLabel}</>}
        </div>
      </td>
      <td className="px-4 py-3">
        <select
          value={channel}
          onChange={(e) => setChannel(e.target.value as Channel)}
          className="rounded-md border border-input bg-background px-2 py-1 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        >
          {CHANNELS.map((c) => (
            <option key={c.value} value={c.value}>{c.label}</option>
          ))}
        </select>
      </td>
      <td className="px-4 py-3">
        <button
          type="button"
          disabled={!dirty || saving}
          onClick={() => onSave(severity, channel, scopeValue)}
          className="flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40 disabled:pointer-events-none transition-opacity"
        >
          {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
          Save
        </button>
      </td>
    </tr>
  );
}

// ── RoutingRulesSection ────────────────────────────────────────────────────

function RoutingRulesSection({
  scope,
  scopeLabel,
  rules,
  loading,
  error,
}: {
  scope:      string | null;
  scopeLabel: string;
  rules:      RoutingRule[];
  loading:    boolean;
  error:      Error | null;
}) {
  const saveRule   = useSaveRoutingRule(scope);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  // Helper: find an existing rule for severity + scope
  function findRule(severity: Severity, scopeVal: string | null): RoutingRule | undefined {
    return rules.find(
      (r) => r.severity === severity && r.scope === scopeVal,
    );
  }

  async function handleSave(severity: Severity, channel: Channel, scopeVal: string | null) {
    const key = `${severity}-${scopeVal ?? 'global'}`;
    setSavingKey(key);
    try {
      await saveRule.mutateAsync({ severity, channel, scope: scopeVal });
      toast.success(
        `${severity} → ${channel === 'none' ? 'No alert' : channel} rule saved`,
        { description: scopeVal ? `Scope: ${scopeVal}` : 'Global default' },
      );
    } catch (err) {
      toast.error('Failed to save rule', {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setSavingKey(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-2 py-4">
        {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
      </div>
    );
  }

  if (error) {
    return (
      <p className="text-xs text-destructive py-3">
        Failed to load routing rules: {error.message}
      </p>
    );
  }

  return (
    <div className="rounded-xl border border-border overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40">
            <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground w-28">Severity</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Scope</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground w-40">Channel</th>
            <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground w-24"></th>
          </tr>
        </thead>
        <tbody>
          {SEVERITIES.map((sev) => {
            const globalKey = `${sev}-global`;
            const scopeKey  = `${sev}-${scope}`;
            return (
              <>
                {/* Global default row */}
                <RoutingRuleRow
                  key={globalKey}
                  severity={sev}
                  scopeLabel="Global"
                  scopeValue={null}
                  existingRule={findRule(sev, null)}
                  onSave={handleSave}
                  saving={savingKey === globalKey}
                />
                {/* Scope-specific override — only when a scope is selected */}
                {scope && (
                  <RoutingRuleRow
                    key={scopeKey}
                    severity={sev}
                    scopeLabel={scopeLabel}
                    scopeValue={scope}
                    existingRule={findRule(sev, scope)}
                    onSave={handleSave}
                    saving={savingKey === scopeKey}
                  />
                )}
              </>
            );
          })}
        </tbody>
      </table>

      {/* Legend */}
      <div className="border-t border-border bg-muted/20 px-4 py-2.5 flex flex-wrap gap-4 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1"><Globe size={11} /> Global default — applies to all scopes unless overridden</span>
        <span className="flex items-center gap-1"><SlidersHorizontal size={11} /> Scope override — takes precedence for <strong className="text-foreground">{scopeLabel}</strong></span>
      </div>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Alerts() {
  const { scope } = useScope();
  const settings  = useNotificationSettings();
  const routing   = useRoutingRules(scope);

  const ns = settings.data;

  return (
    <div className="p-6 space-y-8 max-w-3xl">
      <h1 className="text-xl font-semibold">Alerts</h1>

      {/* ── Credentials ───────────────────────────────────────────────── */}
      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <Bell size={15} className="text-muted-foreground" />
          <h2 className="text-sm font-semibold">Notification Credentials</h2>
        </div>

        {settings.isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-32 w-full rounded-xl" />
            <Skeleton className="h-32 w-full rounded-xl" />
          </div>
        ) : settings.error ? (
          <p className="text-xs text-destructive">
            Failed to load settings: {(settings.error as Error).message}
          </p>
        ) : (
          <div className="space-y-3">
            <CredentialRow
              label="PagerDuty Routing Key"
              field="pagerduty_routing_key"
              channel="pagerduty"
              configured={ns?.pagerduty_configured ?? false}
              masked={ns?.pagerduty_masked ?? null}
              scope={scope}
            />
            <CredentialRow
              label="Slack Webhook URL"
              field="slack_webhook_url"
              channel="slack"
              configured={ns?.slack_configured ?? false}
              masked={ns?.slack_masked ?? null}
              scope={scope}
            />
          </div>
        )}
      </section>

      {/* ── Routing rules ──────────────────────────────────────────────── */}
      <section className="space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <SlidersHorizontal size={15} className="text-muted-foreground" />
            <h2 className="text-sm font-semibold">Severity Routing Rules</h2>
          </div>
          {scope && (
            <span className="rounded-full border border-border bg-muted px-2.5 py-0.5 text-[11px] text-muted-foreground">
              Scope: <strong className="text-foreground">{scope}</strong>
            </span>
          )}
        </div>

        <p className="text-xs text-muted-foreground">
          Each severity has a global default channel and, if a scope is selected, an
          optional scope-specific override. The override takes precedence when the
          selected scope matches.
        </p>

        <RoutingRulesSection
          scope={scope}
          scopeLabel={scope ?? ''}
          rules={routing.data ?? []}
          loading={routing.isLoading}
          error={routing.error as Error | null}
        />
      </section>
    </div>
  );
}
