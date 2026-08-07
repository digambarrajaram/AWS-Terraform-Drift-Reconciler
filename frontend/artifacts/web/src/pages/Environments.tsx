import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { errorMessage } from '@/lib/errorUtils';
import {
  Plus, Pencil, Trash2, Server, CheckCircle2, XCircle,
  Loader2, AlertTriangle, Eye, EyeOff, Inbox,
} from 'lucide-react';
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from '@/components/ui/sheet';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Skeleton } from '@/components/ui/skeleton';
import {
  useEnvironments, useCreateEnvironment, useUpdateEnvironment, useDeleteEnvironment,
} from '@/hooks/useEnvironments';
import type { Environment } from '@/types';

// ── Constants / helpers ─────────────────────────────────────────────────────

const SLUG_RE = /^[a-z0-9][a-z0-9-]*$/;

const inputCls =
  'w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs ' +
  'text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring';
const labelCls = 'block text-[11px] font-medium text-muted-foreground mb-1';
const sectionHeadingCls = 'text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-3 mt-1';

function Field({
  label, required, children, error,
}: {
  label: string; required?: boolean; children: React.ReactNode; error?: string;
}) {
  return (
    <div>
      <label className={labelCls}>
        {label}{required && <span className="text-destructive ml-0.5">*</span>}
      </label>
      {children}
      {error && (
        <p className="flex items-center gap-1 text-xs text-destructive mt-1">
          <AlertTriangle size={11} className="shrink-0" /> {error}
        </p>
      )}
    </div>
  );
}

// ── SecretField ─────────────────────────────────────────────────────────────

function SecretField({
  label, required, value, onChange, configured, masked,
}: {
  label:       string;
  required?:   boolean;
  value:       string;
  onChange:    (v: string) => void;
  configured?: boolean;
  masked?:     string | null;
}) {
  const [replacing, setReplacing] = useState(false);
  const [show,      setShow]      = useState(false);

  // Reset when form is reset (value goes blank AND configured cleared)
  useEffect(() => {
    if (!configured) setReplacing(false);
  }, [configured]);

  if (configured && !replacing) {
    const preview = masked ? `••••••${masked.slice(-4)}` : '••••••••';
    return (
      <div>
        <label className={labelCls}>
          {label}{required && <span className="text-destructive ml-0.5">*</span>}
        </label>
        <div className="flex items-center gap-2 rounded-md border border-input bg-background px-2.5 py-1.5 text-xs text-muted-foreground">
          <span className="font-mono flex-1">{preview}</span>
          <button
            type="button"
            onClick={() => setReplacing(true)}
            className="text-primary text-[11px] hover:underline whitespace-nowrap"
          >
            Replace
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <label className={labelCls}>
        {label}
        {required && <span className="text-destructive ml-0.5">*</span>}
        {configured && (
          <span className="ml-1 text-amber-600 dark:text-amber-400">(replacing existing)</span>
        )}
      </label>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="new-password"
          className={`${inputCls} pr-8`}
        />
        <button
          type="button"
          onClick={() => setShow((v) => !v)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        >
          {show ? <EyeOff size={12} /> : <Eye size={12} />}
        </button>
      </div>
      {configured && (
        <button
          type="button"
          onClick={() => { setReplacing(false); onChange(''); }}
          className="text-[10px] text-muted-foreground hover:text-foreground mt-1 hover:underline"
        >
          Keep existing
        </button>
      )}
    </div>
  );
}

// ── AuthBadge / ActiveBadge ─────────────────────────────────────────────────

function AuthBadge({ type }: { type: Environment['auth_type'] }) {
  const styles: Record<Environment['auth_type'], string> = {
    profile: 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400',
    role:    'bg-sky-100    text-sky-700    dark:bg-sky-900/30    dark:text-sky-400',
    keys:    'bg-amber-100  text-amber-700  dark:bg-amber-900/30  dark:text-amber-400',
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${styles[type]}`}>
      {type}
    </span>
  );
}

function ActiveBadge({ active }: { active: boolean }) {
  return active ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
      <CheckCircle2 size={9} /> Active
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-semibold text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
      <XCircle size={9} /> Inactive
    </span>
  );
}

// ── Form state ──────────────────────────────────────────────────────────────

type FormState = {
  slug:                   string;
  name:                   string;
  aws_account_id:         string;
  region:                 string;
  tf_state_bucket:        string;
  tf_directory_path:      string;
  auth_type:              'profile' | 'role' | 'keys';
  aws_profile:            string;
  aws_role_arn:           string;
  aws_external_id:        string;
  tf_lock_table:          string;
  scan_role_variable:     string;
  apply_role_secret_name: string;
  apply_environment_name: string;
  repo_url:               string;
  repo_branch:            string;
  git_auth_type:          'none' | 'token';
  _github_token:          string;
  _aws_access_key_id:     string;
  _aws_secret_access_key: string;
};

const BLANK_FORM: FormState = {
  slug: '', name: '', aws_account_id: '', region: '',
  tf_state_bucket: '', tf_directory_path: '',
  auth_type: 'role',
  aws_profile: '', aws_role_arn: '', aws_external_id: '',
  tf_lock_table: '', scan_role_variable: '',
  apply_role_secret_name: '', apply_environment_name: '',
  repo_url: '', repo_branch: '',
  git_auth_type: 'none',
  _github_token: '', _aws_access_key_id: '', _aws_secret_access_key: '',
};

function envToForm(e: Environment): FormState {
  return {
    slug:                   e.slug,
    name:                   e.name,
    aws_account_id:         e.aws_account_id,
    region:                 e.region,
    tf_state_bucket:        e.tf_state_bucket,
    tf_directory_path:      e.tf_directory_path,
    auth_type:              e.auth_type,
    aws_profile:            e.aws_profile            ?? '',
    aws_role_arn:           e.aws_role_arn            ?? '',
    aws_external_id:        e.aws_external_id         ?? '',
    tf_lock_table:          e.tf_lock_table           ?? '',
    scan_role_variable:     e.scan_role_variable      ?? '',
    apply_role_secret_name: e.apply_role_secret_name  ?? '',
    apply_environment_name: e.apply_environment_name  ?? '',
    repo_url:               e.repo_url                ?? '',
    repo_branch:            e.repo_branch             ?? '',
    git_auth_type:          e.git_auth_type,
    _github_token:          '',
    _aws_access_key_id:     '',
    _aws_secret_access_key: '',
  };
}

function buildPayload(form: FormState, isEdit: boolean): Record<string, unknown> {
  const p: Record<string, unknown> = {
    name:                   form.name.trim(),
    aws_account_id:         form.aws_account_id.trim(),
    region:                 form.region.trim(),
    tf_state_bucket:        form.tf_state_bucket.trim(),
    tf_directory_path:      form.tf_directory_path.trim(),
    auth_type:              form.auth_type,
    git_auth_type:          form.git_auth_type,
    tf_lock_table:          form.tf_lock_table.trim()          || null,
    scan_role_variable:     form.scan_role_variable.trim()     || null,
    apply_role_secret_name: form.apply_role_secret_name.trim() || null,
    apply_environment_name: form.apply_environment_name.trim() || null,
    repo_url:               form.repo_url.trim()               || null,
    repo_branch:            form.repo_branch.trim()            || null,
  };
  if (!isEdit) p.slug = form.slug.trim();

  if (form.auth_type === 'profile') {
    p.aws_profile = form.aws_profile.trim() || null;
  } else if (form.auth_type === 'role') {
    p.aws_role_arn    = form.aws_role_arn.trim()    || null;
    p.aws_external_id = form.aws_external_id.trim() || null;
  } else if (form.auth_type === 'keys') {
    if (form._aws_access_key_id.trim())     p._aws_access_key_id     = form._aws_access_key_id.trim();
    if (form._aws_secret_access_key.trim()) p._aws_secret_access_key = form._aws_secret_access_key.trim();
  }

  if (form.git_auth_type === 'token' && form._github_token.trim()) {
    p._github_token = form._github_token.trim();
  }

  return p;
}

function validateForm(form: FormState, isEdit: boolean, env?: Environment): string[] {
  const errs: string[] = [];
  if (!isEdit && !SLUG_RE.test(form.slug.trim())) {
    errs.push('Slug must match ^[a-z0-9][a-z0-9-]*$ (lowercase, no underscores, must start with a letter or digit)');
  }
  if (!form.name.trim())             errs.push('Name is required');
  if (!form.aws_account_id.trim())   errs.push('AWS Account ID is required');
  if (!form.region.trim())           errs.push('Region is required');
  if (!form.tf_state_bucket.trim())  errs.push('Terraform state bucket is required');
  if (!form.tf_directory_path.trim()) errs.push('Terraform directory path is required');

  if (!isEdit && !['role', 'keys'].includes(form.auth_type)) {
    errs.push('Auth type must be "role" or "keys" for new environments');
  }

  if (form.auth_type === 'keys') {
    const hasStoredKey    = env?.aws_access_key_configured;
    const hasStoredSecret = env?.aws_secret_key_configured;
    if (!hasStoredKey    && !form._aws_access_key_id.trim())     errs.push('AWS Access Key ID is required');
    if (!hasStoredSecret && !form._aws_secret_access_key.trim()) errs.push('AWS Secret Access Key is required');
  }

  return errs;
}

// ── EnvForm (Sheet) ─────────────────────────────────────────────────────────

function EnvForm({
  open, env, onClose,
}: {
  open:    boolean;
  env:     Environment | null; // null = add mode
  onClose: () => void;
}) {
  const isEdit = env !== null;
  const create = useCreateEnvironment();
  const update = useUpdateEnvironment();
  const pending = create.isPending || update.isPending;

  const [form,   setForm]   = useState<FormState>(BLANK_FORM);
  const [errors, setErrors] = useState<string[]>([]);

  // Sync form when the sheet opens
  useEffect(() => {
    if (open) {
      setForm(env ? envToForm(env) : BLANK_FORM);
      setErrors([]);
    }
  }, [open, env]);

  function set<K extends keyof FormState>(k: K, v: FormState[K]) {
    setForm((p) => ({ ...p, [k]: v }));
    setErrors([]);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const errs = validateForm(form, isEdit, env ?? undefined);
    if (errs.length) { setErrors(errs); return; }

    const payload = buildPayload(form, isEdit);
    try {
      if (isEdit && env) {
        await update.mutateAsync({ id: env.id, ...payload });
        toast.success(`Environment "${env.name}" updated`);
      } else {
        await create.mutateAsync(payload);
        toast.success(`Environment "${form.name}" created`);
      }
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(isEdit ? 'Failed to update environment' : 'Failed to create environment', {
        description: msg,
      });
    }
  }

  const authType    = form.auth_type;
  const gitAuthType = form.git_auth_type;

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full sm:max-w-xl overflow-y-auto flex flex-col gap-0 p-0">
        <SheetHeader className="px-6 py-5 border-b border-border">
          <SheetTitle className="flex items-center gap-2 text-base">
            <Server size={16} className="text-muted-foreground" />
            {isEdit ? `Edit: ${env?.name}` : 'Add Environment'}
          </SheetTitle>
        </SheetHeader>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-6 py-5 space-y-6">

          {/* ── Core fields ──────────────────────────────────────── */}
          <div className="space-y-3">
            <p className={sectionHeadingCls}>Core</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Slug" required error={!isEdit && form.slug && !SLUG_RE.test(form.slug) ? 'lowercase letters, digits, and hyphens only' : undefined}>
                <input
                  type="text"
                  placeholder="scope-e"
                  value={form.slug}
                  onChange={(e) => set('slug', e.target.value)}
                  disabled={isEdit}
                  className={`${inputCls} ${isEdit ? 'opacity-50 cursor-not-allowed' : ''}`}
                />
                {isEdit && (
                  <p className="text-[10px] text-muted-foreground mt-0.5">Slug cannot be changed after creation.</p>
                )}
              </Field>
              <Field label="Display Name" required>
                <input type="text" placeholder="Environment E" value={form.name}
                  onChange={(e) => set('name', e.target.value)} className={inputCls} />
              </Field>
              <Field label="AWS Account ID" required>
                <input type="text" placeholder="123456789012" value={form.aws_account_id}
                  onChange={(e) => set('aws_account_id', e.target.value)} className={inputCls} />
              </Field>
              <Field label="Region" required>
                <input type="text" placeholder="us-east-1" value={form.region}
                  onChange={(e) => set('region', e.target.value)} className={inputCls} />
              </Field>
              <Field label="TF State Bucket" required>
                <input type="text" placeholder="my-tfstate-bucket" value={form.tf_state_bucket}
                  onChange={(e) => set('tf_state_bucket', e.target.value)} className={inputCls} />
              </Field>
              <Field label="TF Directory Path" required>
                <input type="text" placeholder="./terraform/ec2_terraform_account_e" value={form.tf_directory_path}
                  onChange={(e) => set('tf_directory_path', e.target.value)} className={inputCls} />
              </Field>
            </div>
          </div>

          {/* ── Authentication ────────────────────────────────────── */}
          <div className="space-y-3">
            <p className={sectionHeadingCls}>AWS Authentication</p>
            <Field label="Auth Type" required>
              <div className="flex gap-3">
                {((isEdit && env?.auth_type === 'profile'
                  ? ['profile', 'role', 'keys']
                  : ['role', 'keys']) as const).map((t) => (
                  <label key={t} className="flex items-center gap-1.5 cursor-pointer select-none text-xs">
                    <input
                      type="radio"
                      name="auth_type"
                      value={t}
                      checked={authType === t}
                      onChange={() => set('auth_type', t)}
                      className="accent-primary"
                    />
                    {t}
                  </label>
                ))}
              </div>
            </Field>

            {authType === 'profile' && (
              <Field label="AWS Profile">
                <input type="text" placeholder="default" value={form.aws_profile}
                  onChange={(e) => set('aws_profile', e.target.value)} className={inputCls} />
              </Field>
            )}

            {authType === 'role' && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="AWS Role ARN">
                  <input type="text" placeholder="arn:aws:iam::123456789012:role/MyRole"
                    value={form.aws_role_arn}
                    onChange={(e) => set('aws_role_arn', e.target.value)} className={inputCls} />
                </Field>
                <Field label="External ID">
                  <input type="text" placeholder="optional" value={form.aws_external_id}
                    onChange={(e) => set('aws_external_id', e.target.value)} className={inputCls} />
                </Field>
              </div>
            )}

            {authType === 'keys' && (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <SecretField
                  label="Access Key ID"
                  required={!env?.aws_access_key_configured}
                  value={form._aws_access_key_id}
                  onChange={(v) => set('_aws_access_key_id', v)}
                  configured={env?.aws_access_key_configured}
                  masked={env?.aws_access_key_masked}
                />
                <SecretField
                  label="Secret Access Key"
                  required={!env?.aws_secret_key_configured}
                  value={form._aws_secret_access_key}
                  onChange={(v) => set('_aws_secret_access_key', v)}
                  configured={env?.aws_secret_key_configured}
                  masked={env?.aws_secret_key_masked}
                />
              </div>
            )}
          </div>

          {/* ── Terraform options ─────────────────────────────────── */}
          <div className="space-y-3">
            <p className={sectionHeadingCls}>Terraform Options</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Lock Table">
                <input type="text" placeholder="terraform-locks" value={form.tf_lock_table}
                  onChange={(e) => set('tf_lock_table', e.target.value)} className={inputCls} />
              </Field>
              <Field label="Scan Role Variable">
                <input type="text" placeholder="TF_VAR_scan_role" value={form.scan_role_variable}
                  onChange={(e) => set('scan_role_variable', e.target.value)} className={inputCls} />
              </Field>
              <Field label="Apply Role Secret Name">
                <input type="text" placeholder="scope-e-apply-role" value={form.apply_role_secret_name}
                  onChange={(e) => set('apply_role_secret_name', e.target.value)} className={inputCls} />
              </Field>
              <Field label="Apply Environment Name">
                <input type="text" placeholder="scope-e-apply" value={form.apply_environment_name}
                  onChange={(e) => set('apply_environment_name', e.target.value)} className={inputCls} />
              </Field>
            </div>
          </div>

          {/* ── Repository ───────────────────────────────────────── */}
          <div className="space-y-3">
            <p className={sectionHeadingCls}>Repository</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Repo URL">
                <input type="text" placeholder="https://github.com/org/repo"
                  value={form.repo_url}
                  onChange={(e) => set('repo_url', e.target.value)} className={inputCls} />
              </Field>
              <Field label="Branch">
                <input type="text" placeholder="main" value={form.repo_branch}
                  onChange={(e) => set('repo_branch', e.target.value)} className={inputCls} />
              </Field>
            </div>
            <Field label="Git Auth Type">
              <div className="flex gap-4">
                {(['none', 'token'] as const).map((t) => (
                  <label key={t} className="flex items-center gap-1.5 cursor-pointer select-none text-xs">
                    <input
                      type="radio"
                      name="git_auth_type"
                      value={t}
                      checked={gitAuthType === t}
                      onChange={() => set('git_auth_type', t)}
                      className="accent-primary"
                    />
                    {t === 'none' ? 'None (public)' : 'Token'}
                  </label>
                ))}
              </div>
            </Field>
            {gitAuthType === 'token' && (
              <SecretField
                label="GitHub Token"
                value={form._github_token}
                onChange={(v) => set('_github_token', v)}
                configured={env?.github_token_configured}
                masked={env?.github_token_masked}
              />
            )}
          </div>

          {/* ── Errors ───────────────────────────────────────────── */}
          {errors.length > 0 && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 space-y-1">
              {errors.map((e) => (
                <p key={e} className="flex items-start gap-1.5 text-xs text-destructive">
                  <AlertTriangle size={11} className="shrink-0 mt-0.5" /> {e}
                </p>
              ))}
            </div>
          )}

          {/* ── Actions ──────────────────────────────────────────── */}
          <div className="flex gap-2 pt-2 pb-4">
            <button
              type="submit"
              disabled={pending}
              className="flex items-center gap-1.5 rounded-md bg-primary px-4 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {pending && <Loader2 size={12} className="animate-spin" />}
              {isEdit ? 'Save Changes' : 'Create Environment'}
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-accent transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      </SheetContent>
    </Sheet>
  );
}

// ── DeleteDialog ────────────────────────────────────────────────────────────

function DeleteDialog({
  env, onClose,
}: {
  env:     Environment | null;
  onClose: () => void;
}) {
  const del     = useDeleteEnvironment();
  const pending = del.isPending;

  async function handleDelete() {
    if (!env) return;
    try {
      await del.mutateAsync(env.id);
      toast.success(`"${env.name}" deactivated`, {
        description: 'Soft-deleted — reactivate by creating an environment with the same slug.',
      });
      onClose();
    } catch (err: unknown) {
      toast.error('Failed to delete environment', {
        description: err instanceof Error ? err.message : String(err),
      });
    }
  }

  return (
    <AlertDialog open={!!env} onOpenChange={(v) => !v && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Deactivate environment?</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2 text-sm text-muted-foreground">
              <p>
                This will soft-delete{' '}
                <span className="font-semibold text-foreground">{env?.name}</span>{' '}
                (slug: <span className="font-mono text-foreground">{env?.slug}</span>).
                The row stays in the database as inactive.
              </p>
              <p>
                You can reactivate it later by creating a new environment with the same slug —
                the backend will restore the existing row.
              </p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onClose}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDelete}
            disabled={pending}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
          >
            {pending && <Loader2 size={12} className="animate-spin mr-1.5" />}
            {pending ? 'Deactivating…' : 'Deactivate'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function Environments() {
  const { allEnvironments, isLoading, error } = useEnvironments();
  const [formOpen, setFormOpen]   = useState(false);
  const [editEnv,  setEditEnv]    = useState<Environment | null>(null);
  const [deleteEnv, setDeleteEnv] = useState<Environment | null>(null);

  function openAdd() {
    setEditEnv(null);
    setFormOpen(true);
  }

  function openEdit(env: Environment) {
    setEditEnv(env);
    setFormOpen(true);
  }

  function closeForm() {
    setFormOpen(false);
    // Keep editEnv alive until sheet animation ends
    setTimeout(() => setEditEnv(null), 300);
  }

  return (
    <div className="p-6 space-y-5 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <h1 className="text-xl font-semibold">Environments</h1>
        <button
          type="button"
          onClick={openAdd}
          className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 transition-opacity"
        >
          <Plus size={13} /> Add Environment
        </button>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-2">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-12 w-full rounded-xl" />)}
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-xs text-destructive">
          <AlertTriangle size={13} className="shrink-0" />
          Failed to load environments: {errorMessage(error)}
        </div>
      ) : allEnvironments.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-border py-16 text-center">
          <Inbox size={32} className="text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">No environments yet</p>
          <button type="button" onClick={openAdd}
            className="text-xs text-primary hover:underline">
            Add your first environment
          </button>
        </div>
      ) : (
        <div className="rounded-xl border border-border overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[700px]">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  {['Name', 'Slug', 'Account ID', 'Region', 'Auth', 'Status', ''].map((h) => (
                    <th key={h}
                      className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {allEnvironments.map((env) => (
                  <tr
                    key={env.id}
                    className={`transition-colors hover:bg-muted/30 ${!env.is_active ? 'opacity-50' : ''}`}
                  >
                    <td className="px-4 py-3">
                      <span className="font-medium text-foreground">{env.name}</span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                      {env.slug}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                      {env.aws_account_id}
                    </td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">
                      {env.region}
                    </td>
                    <td className="px-4 py-3">
                      <AuthBadge type={env.auth_type || 'profile'} />
                    </td>
                    <td className="px-4 py-3">
                      <ActiveBadge active={env.is_active} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => openEdit(env)}
                          title="Edit"
                          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                        >
                          <Pencil size={12} /> Edit
                        </button>
                        {env.is_active && (
                          <button
                            type="button"
                            onClick={() => setDeleteEnv(env)}
                            title="Deactivate"
                            className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
                          >
                            <Trash2 size={12} /> Delete
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Inactive note */}
          {allEnvironments.some((e) => !e.is_active) && (
            <div className="border-t border-border bg-muted/20 px-4 py-2 text-[11px] text-muted-foreground">
              Inactive environments are shown greyed out. Re-create with the same slug to reactivate.
            </div>
          )}
        </div>
      )}

      {/* Sheets / Dialogs */}
      <EnvForm open={formOpen} env={editEnv} onClose={closeForm} />
      <DeleteDialog env={deleteEnv} onClose={() => setDeleteEnv(null)} />
    </div>
  );
}
