import { useState, useEffect } from 'react';
import { format, isPast, parseISO } from 'date-fns';
import { toast } from 'sonner';
import {
  Plus, Trash2, Clock, ChevronDown, ChevronUp,
  ShieldCheck, ShieldOff, Inbox, AlertTriangle,
} from 'lucide-react';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';

import { useScope } from '@/hooks/useScope';
import {
  useExceptions, useExceptionsMutation,
  type DriftException, type UnmanagedException,
} from '@/hooks/useExceptions';

// ── Helpers ────────────────────────────────────────────────────────────────

function isExpired(expires: string | null): boolean {
  return !!expires && isPast(parseISO(expires));
}

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

// ── Small shared UI ────────────────────────────────────────────────────────

function ActiveBadge({ active, expires }: { active: boolean; expires?: string | null }) {
  const expired = isExpired(expires ?? null);
  if (expired) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-medium text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
        <ShieldOff size={9} /> Expired
      </span>
    );
  }
  return active ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
      <ShieldCheck size={9} /> Active
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-medium text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
      <ShieldOff size={9} /> Inactive
    </span>
  );
}

function ExpiresCell({ expires }: { expires: string | null }) {
  if (!expires) return <span className="text-muted-foreground">Never</span>;
  const expired = isExpired(expires);
  return (
    <span className={expired ? 'text-destructive font-medium' : 'text-foreground'}>
      {format(parseISO(expires), 'MMM d, yyyy')}
      {expired && ' (expired)'}
    </span>
  );
}

const inputCls =
  'w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring';
const labelCls = 'block text-[11px] font-medium text-muted-foreground mb-1';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className={labelCls}>{label}</label>
      {children}
    </div>
  );
}

function FormError({ msg }: { msg: string }) {
  return (
    <p className="flex items-center gap-1 text-xs text-destructive mt-1">
      <AlertTriangle size={11} className="shrink-0" /> {msg}
    </p>
  );
}

// ── ExpireDialog ───────────────────────────────────────────────────────────

function ExpireDialog({
  open, label, onClose, onConfirm, pending,
}: {
  open: boolean; label: string; onClose: () => void;
  onConfirm: (iso: string) => void; pending: boolean;
}) {
  const [val, setVal] = useState(todayISO);
  useEffect(() => { if (open) setVal(todayISO()); }, [open]);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Set Expiry Date</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground mb-1 break-all">
          Exception: <span className="text-foreground font-mono">{label}</span>
        </p>
        <Field label="Expires on">
          <input
            type="date"
            value={val}
            onChange={(e) => setVal(e.target.value)}
            className={inputCls}
          />
        </Field>
        <DialogFooter className="mt-4">
          <button type="button" onClick={onClose}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-accent transition-colors">
            Cancel
          </button>
          <button type="button" disabled={!val || pending}
            onClick={() => onConfirm(new Date(val + 'T23:59:59').toISOString())}
            className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity">
            {pending ? 'Saving…' : 'Set Expiry'}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── DeleteDialog ───────────────────────────────────────────────────────────

function DeleteDialog({
  open, label, onClose, onConfirm, pending,
}: {
  open: boolean; label: string; onClose: () => void;
  onConfirm: () => void; pending: boolean;
}) {
  return (
    <AlertDialog open={open} onOpenChange={(v) => !v && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete exception?</AlertDialogTitle>
          <AlertDialogDescription>
            This will permanently remove the exception for{' '}
            <span className="font-mono text-foreground break-all">{label}</span>.
            Drift events matching this resource will no longer be suppressed.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            disabled={pending}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50">
            {pending ? 'Deleting…' : 'Delete'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// ── RowActions ─────────────────────────────────────────────────────────────

function RowActions({
  onExpire, onDelete, showExpire = true,
}: {
  onExpire?: () => void; onDelete: () => void; showExpire?: boolean;
}) {
  return (
    <div className="flex items-center gap-1">
      {showExpire && (
        <button type="button" onClick={onExpire}
          title="Set expiry"
          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors">
          <Clock size={12} /> Expire
        </button>
      )}
      <button type="button" onClick={onDelete}
        title="Delete exception"
        className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors">
        <Trash2 size={12} /> Delete
      </button>
    </div>
  );
}

// ── DriftTab ───────────────────────────────────────────────────────────────

const DRIFT_BLANK = {
  resource_address: '', drift_type: '', reason: '',
  approved_by: '', expires: '', auto: false,
};

function DriftTab({
  rows, scope, mutation,
}: {
  rows: DriftException[];
  scope: string;
  mutation: ReturnType<typeof useExceptionsMutation>;
}) {
  const [showForm, setShowForm]   = useState(false);
  const [form, setForm]           = useState(DRIFT_BLANK);
  const [formErr, setFormErr]     = useState('');
  const [expireTarget, setExpireTarget] = useState<DriftException | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DriftException | null>(null);

  function set(k: keyof typeof DRIFT_BLANK, v: string | boolean) {
    setForm((p) => ({ ...p, [k]: v }));
    setFormErr('');
  }

  function validate(): string {
    if (!form.resource_address.trim()) return 'Resource address is required';
    if (!form.reason.trim())          return 'Reason is required';
    if (form.expires) {
      const d = new Date(form.expires);
      if (isNaN(d.getTime()))  return 'Invalid expiry date';
      if (d <= new Date())     return 'Expiry date must be in the future';
    }
    return '';
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    const err = validate();
    if (err) { setFormErr(err); return; }
    try {
      await mutation.mutateAsync({
        scope,
        exception_type: 'drift',
        action: 'add',
        entry: {
          resource_address: form.resource_address.trim(),
          drift_type:       form.drift_type.trim() || null,
          reason:           form.reason.trim(),
          approved_by:      form.approved_by.trim() || null,
          expires:          form.expires ? new Date(form.expires + 'T23:59:59').toISOString() : null,
          auto:             form.auto,
        },
      });
      toast.success('Drift exception added');
      setForm(DRIFT_BLANK);
      setShowForm(false);
    } catch (err) {
      toast.error('Failed to add exception', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function handleExpire(iso: string) {
    if (!expireTarget) return;
    try {
      await mutation.mutateAsync({
        scope,
        exception_type: 'drift',
        action: 'expire',
        entry: { id: expireTarget.id, expires: iso },
      });
      toast.success('Expiry date set');
      setExpireTarget(null);
    } catch (err) {
      toast.error('Failed to set expiry', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      await mutation.mutateAsync({
        scope,
        exception_type: 'drift',
        action: 'delete',
        entry: { id: deleteTarget.id },
      });
      toast.success('Exception deleted');
      setDeleteTarget(null);
    } catch (err) {
      toast.error('Failed to delete exception', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return (
    <div className="space-y-4 mt-4">
      {/* Add form toggle */}
      <button type="button" onClick={() => setShowForm((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-accent transition-colors">
        {showForm ? <ChevronUp size={13} /> : <Plus size={13} />}
        {showForm ? 'Cancel' : 'Add Drift Exception'}
      </button>

      {/* Add form */}
      {showForm && (
        <div className="rounded-xl border border-border bg-card p-5">
          <h3 className="text-sm font-semibold mb-4">New Drift Exception</h3>
          <form onSubmit={handleAdd} className="space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Resource Address *">
                <input type="text" placeholder="aws_s3_bucket.my-bucket"
                  value={form.resource_address} onChange={(e) => set('resource_address', e.target.value)}
                  className={inputCls} />
              </Field>
              <Field label="Drift Type">
                <input type="text" placeholder="e.g. tag_drift, config_drift"
                  value={form.drift_type} onChange={(e) => set('drift_type', e.target.value)}
                  className={inputCls} />
              </Field>
              <Field label="Reason *">
                <textarea placeholder="Why is this exception needed?"
                  value={form.reason} onChange={(e) => set('reason', e.target.value)}
                  rows={2} className={`${inputCls} resize-none`} />
              </Field>
              <Field label="Approved By">
                <input type="text" placeholder="slack handle or email"
                  value={form.approved_by} onChange={(e) => set('approved_by', e.target.value)}
                  className={inputCls} />
              </Field>
              <Field label="Expires (optional — must be future)">
                <input type="date" min={todayISO()}
                  value={form.expires} onChange={(e) => set('expires', e.target.value)}
                  className={inputCls} />
              </Field>
            </div>
            {/* Auto checkbox */}
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input type="checkbox" checked={form.auto}
                onChange={(e) => set('auto', e.target.checked)}
                className="h-3.5 w-3.5 rounded border-input accent-primary" />
              <span className="text-xs text-foreground">Auto-apply</span>
            </label>
            {formErr && <FormError msg={formErr} />}
            <div className="flex gap-2 pt-1">
              <button type="submit" disabled={mutation.isPending}
                className="rounded-md bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity">
                {mutation.isPending ? 'Adding…' : 'Add Exception'}
              </button>
              <button type="button" onClick={() => { setShowForm(false); setFormErr(''); setForm(DRIFT_BLANK); }}
                className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-accent transition-colors">
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Table */}
      <div className="rounded-xl border border-border overflow-hidden">
        {rows.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-14 text-center">
            <Inbox size={32} className="text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">No drift exceptions</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs min-w-[760px]">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  {['Resource Address', 'Drift Type', 'Reason', 'Approved By', 'Expires', 'Auto', 'Status', ''].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => {
                  const expired = isExpired(row.expires);
                  const dim = expired || !row.active;
                  return (
                    <tr key={String(row.id)} className={`transition-colors hover:bg-muted/30 ${dim ? 'opacity-60' : ''}`}>
                      <td className="px-4 py-3 font-mono max-w-[200px]">
                        <span className="block truncate" title={row.resource_address}>{row.resource_address}</span>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{row.drift_type ?? '—'}</td>
                      <td className="px-4 py-3 max-w-[200px]">
                        <span className="block truncate" title={row.reason}>{row.reason}</span>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{row.approved_by ?? '—'}</td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <ExpiresCell expires={row.expires} />
                      </td>
                      <td className="px-4 py-3">
                        {row.auto ? (
                          <span className="inline-flex rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-medium text-violet-700 dark:bg-violet-900/30 dark:text-violet-400">Auto</span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <ActiveBadge active={row.active} expires={row.expires} />
                      </td>
                      <td className="px-4 py-3">
                        <RowActions
                          onExpire={() => setExpireTarget(row)}
                          onDelete={() => setDeleteTarget(row)}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ExpireDialog
        open={!!expireTarget}
        label={expireTarget?.resource_address ?? ''}
        onClose={() => setExpireTarget(null)}
        onConfirm={handleExpire}
        pending={mutation.isPending}
      />
      <DeleteDialog
        open={!!deleteTarget}
        label={deleteTarget?.resource_address ?? ''}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        pending={mutation.isPending}
      />
    </div>
  );
}

// ── UnmanagedTab ───────────────────────────────────────────────────────────

const UNM_BLANK = {
  resource_type: '', resource_id_pattern: '', reason: '',
  approved_by: '', max_monthly_cost_usd: '',
};

function UnmanagedTab({
  rows, scope, mutation,
}: {
  rows: UnmanagedException[];
  scope: string;
  mutation: ReturnType<typeof useExceptionsMutation>;
}) {
  const [showForm, setShowForm]   = useState(false);
  const [form, setForm]           = useState(UNM_BLANK);
  const [formErr, setFormErr]     = useState('');
  const [expireTarget, setExpireTarget] = useState<UnmanagedException | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<UnmanagedException | null>(null);

  function set(k: keyof typeof UNM_BLANK, v: string) {
    setForm((p) => ({ ...p, [k]: v }));
    setFormErr('');
  }

  function validate(): string {
    if (!form.resource_type.trim())        return 'Resource type is required';
    if (!form.resource_id_pattern.trim())  return 'Resource ID pattern is required';
    if (!form.reason.trim())               return 'Reason is required';
    if (form.max_monthly_cost_usd) {
      const n = Number(form.max_monthly_cost_usd);
      if (isNaN(n) || n < 0) return 'Max monthly cost must be a positive number';
    }
    return '';
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    const err = validate();
    if (err) { setFormErr(err); return; }
    try {
      await mutation.mutateAsync({
        scope,
        exception_type: 'unmanaged',
        action: 'add',
        entry: {
          resource_type:        form.resource_type.trim(),
          resource_id_pattern:  form.resource_id_pattern.trim(),
          reason:               form.reason.trim(),
          approved_by:          form.approved_by.trim() || null,
          max_monthly_cost_usd: form.max_monthly_cost_usd ? Number(form.max_monthly_cost_usd) : null,
        },
      });
      toast.success('Unmanaged exception added');
      setForm(UNM_BLANK);
      setShowForm(false);
    } catch (err) {
      toast.error('Failed to add exception', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function handleExpire(iso: string) {
    if (!expireTarget) return;
    try {
      await mutation.mutateAsync({
        scope,
        exception_type: 'unmanaged',
        action: 'expire',
        entry: { id: expireTarget.id, expires: iso },
      });
      toast.success('Expiry date set');
      setExpireTarget(null);
    } catch (err) {
      toast.error('Failed to set expiry', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      await mutation.mutateAsync({
        scope,
        exception_type: 'unmanaged',
        action: 'delete',
        entry: { id: deleteTarget.id },
      });
      toast.success('Exception deleted');
      setDeleteTarget(null);
    } catch (err) {
      toast.error('Failed to delete exception', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return (
    <div className="space-y-4 mt-4">
      <button type="button" onClick={() => setShowForm((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-accent transition-colors">
        {showForm ? <ChevronUp size={13} /> : <Plus size={13} />}
        {showForm ? 'Cancel' : 'Add Unmanaged Exception'}
      </button>

      {showForm && (
        <div className="rounded-xl border border-border bg-card p-5">
          <h3 className="text-sm font-semibold mb-4">New Unmanaged Exception</h3>
          <form onSubmit={handleAdd} className="space-y-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Resource Type *">
                <input type="text" placeholder="e.g. aws_s3_bucket"
                  value={form.resource_type} onChange={(e) => set('resource_type', e.target.value)}
                  className={inputCls} />
              </Field>
              <Field label="Resource ID Pattern *">
                <input type="text" placeholder="e.g. prod-* or exact-bucket-name"
                  value={form.resource_id_pattern} onChange={(e) => set('resource_id_pattern', e.target.value)}
                  className={inputCls} />
              </Field>
              <Field label="Reason *">
                <textarea placeholder="Why is this resource unmanaged?"
                  value={form.reason} onChange={(e) => set('reason', e.target.value)}
                  rows={2} className={`${inputCls} resize-none`} />
              </Field>
              <Field label="Approved By">
                <input type="text" placeholder="slack handle or email"
                  value={form.approved_by} onChange={(e) => set('approved_by', e.target.value)}
                  className={inputCls} />
              </Field>
              <Field label="Max Monthly Cost (USD, optional)">
                <input type="number" min="0" step="0.01" placeholder="e.g. 50.00"
                  value={form.max_monthly_cost_usd} onChange={(e) => set('max_monthly_cost_usd', e.target.value)}
                  className={inputCls} />
              </Field>
            </div>
            {formErr && <FormError msg={formErr} />}
            <div className="flex gap-2 pt-1">
              <button type="submit" disabled={mutation.isPending}
                className="rounded-md bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity">
                {mutation.isPending ? 'Adding…' : 'Add Exception'}
              </button>
              <button type="button" onClick={() => { setShowForm(false); setFormErr(''); setForm(UNM_BLANK); }}
                className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-accent transition-colors">
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="rounded-xl border border-border overflow-hidden">
        {rows.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-14 text-center">
            <Inbox size={32} className="text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">No unmanaged exceptions</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs min-w-[700px]">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  {['Resource Type', 'ID Pattern', 'Reason', 'Approved By', 'Max Cost/mo', 'Status', ''].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-left font-medium text-muted-foreground whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => {
                  const dim = !row.active;
                  return (
                    <tr key={String(row.id)} className={`transition-colors hover:bg-muted/30 ${dim ? 'opacity-60' : ''}`}>
                      <td className="px-4 py-3 font-mono text-foreground">{row.resource_type}</td>
                      <td className="px-4 py-3 font-mono max-w-[180px]">
                        <span className="block truncate" title={row.resource_id_pattern}>{row.resource_id_pattern}</span>
                      </td>
                      <td className="px-4 py-3 max-w-[200px]">
                        <span className="block truncate" title={row.reason}>{row.reason}</span>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{row.approved_by ?? '—'}</td>
                      <td className="px-4 py-3 text-muted-foreground">
                        {row.max_monthly_cost_usd != null
                          ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(row.max_monthly_cost_usd)
                          : '—'}
                      </td>
                      <td className="px-4 py-3">
                        <ActiveBadge active={row.active} />
                      </td>
                      <td className="px-4 py-3">
                        <RowActions
                          onExpire={() => setExpireTarget(row)}
                          onDelete={() => setDeleteTarget(row)}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ExpireDialog
        open={!!expireTarget}
        label={expireTarget ? `${expireTarget.resource_type} / ${expireTarget.resource_id_pattern}` : ''}
        onClose={() => setExpireTarget(null)}
        onConfirm={handleExpire}
        pending={mutation.isPending}
      />
      <DeleteDialog
        open={!!deleteTarget}
        label={deleteTarget ? `${deleteTarget.resource_type} / ${deleteTarget.resource_id_pattern}` : ''}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        pending={mutation.isPending}
      />
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function Exceptions() {
  const { scope }   = useScope();
  const { data, isLoading, error } = useExceptions(scope);
  const mutation    = useExceptionsMutation(scope);

  const driftRows     = data?.drift_exceptions     ?? [];
  const unmanagedRows = data?.unmanaged_exceptions ?? [];

  return (
    <div className="p-6 space-y-4 max-w-6xl">
      <h1 className="text-xl font-semibold">Exceptions</h1>

      {isLoading ? (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-4 text-sm text-destructive">
          <AlertTriangle size={15} className="shrink-0" />
          Failed to load exceptions: {(error as Error).message}
        </div>
      ) : (
        <Tabs defaultValue="drift">
          <TabsList>
            <TabsTrigger value="drift">
              Drift
              {driftRows.length > 0 && (
                <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                  {driftRows.length}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="unmanaged">
              Unmanaged
              {unmanagedRows.length > 0 && (
                <span className="ml-1.5 rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                  {unmanagedRows.length}
                </span>
              )}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="drift">
            <DriftTab rows={driftRows} scope={scope!} mutation={mutation} />
          </TabsContent>

          <TabsContent value="unmanaged">
            <UnmanagedTab rows={unmanagedRows} scope={scope!} mutation={mutation} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
