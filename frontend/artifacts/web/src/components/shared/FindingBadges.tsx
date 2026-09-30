import type { ElementType } from 'react';
import {
  ShieldCheck, Ban, RotateCcw, Server, Layers,
} from 'lucide-react';
import { findingStatusLabel } from '@/lib/statusLabels';
import type { DriftEvent } from '@/types';

/** Shared finding-status colors — PR Queue, Explorer, drawer details. */
export const FINDING_STATUS_CLS: Record<DriftEvent['status'], string> = {
  open:       'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  resolved:   'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
  suppressed: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
  reverted:   'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  manual_revert_required: 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400',
};

const TYPE_META: Record<string, {
  label: string;
  icon: ElementType;
  cls: string;
  kind: 'scan' | 'resource';
}> = {
  security_only: {
    label: 'Security',
    icon: ShieldCheck,
    cls: 'bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-300',
    kind: 'scan',
  },
  manual: {
    label: 'Manual review',
    icon: ShieldCheck,
    cls: 'bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-300',
    kind: 'scan',
  },
  unmanaged: {
    label: 'Unmanaged',
    icon: Ban,
    cls: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
    kind: 'resource',
  },
  rollback: {
    label: 'Rollback',
    icon: RotateCcw,
    cls: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300',
    kind: 'resource',
  },
  batch: {
    label: 'Batch',
    icon: Layers,
    cls: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300',
    kind: 'resource',
  },
  fix: {
    label: 'Fix',
    icon: Server,
    cls: 'bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300',
    kind: 'resource',
  },
};

const BADGE =
  'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-semibold whitespace-nowrap';

export function FindingStatusBadge({ status }: { status: DriftEvent['status'] | string }) {
  const cls = FINDING_STATUS_CLS[status as DriftEvent['status']]
    ?? 'bg-muted text-muted-foreground';
  return (
    <span className={`${BADGE} ${cls}`}>
      {findingStatusLabel(status)}
    </span>
  );
}

export function TypeBadge({ prType }: { prType: string | null | undefined }) {
  if (!prType) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  const meta = TYPE_META[prType];
  if (!meta) {
    return (
      <span className={`${BADGE} bg-muted text-muted-foreground capitalize`}>
        {prType.replace(/_/g, ' ')}
      </span>
    );
  }
  const Icon = meta.icon;
  return (
    <span
      className={`${BADGE} ${meta.cls}`}
      title={meta.kind === 'scan' ? 'Security scan finding (not resource drift)' : 'Resource drift finding'}
    >
      <Icon size={11} className="shrink-0" />
      {meta.label}
    </span>
  );
}

/** Resource id with a shield cue for security/scan findings. */
export function ResourceCell({
  resourceId, prType,
}: {
  resourceId: string;
  prType: string | null | undefined;
}) {
  const isScan = prType === 'security_only' || prType === 'manual';
  return (
    <span className="flex items-center gap-1.5 min-w-0" title={resourceId}>
      {isScan && (
        <ShieldCheck
          size={12}
          className="shrink-0 text-teal-600 dark:text-teal-400"
          aria-label="Security scan finding"
        />
      )}
      <span className={`block truncate ${isScan ? 'text-teal-900 dark:text-teal-200' : ''}`}>
        {resourceId}
      </span>
    </span>
  );
}
