import type { DriftEvent } from '@/types';

// Rows written before 2026-08 have JSON-encoded text *inside* the jsonb
// columns (drift_history.append_entry used json.dumps), so PostgREST
// returns a string where consumers expect an object/array — which turned
// Object.entries() into one block per character.  Parse those strings
// here, at the data boundary, so every page sees the real shape.
// Idempotent: dict/array values pass through untouched; unparseable
// strings fall through to the caller's existing handling.
function parseJson<T>(v: unknown): T | null {
  if (typeof v !== 'string') return null;
  try {
    const parsed = JSON.parse(v) as T;
    return typeof parsed === 'object' && parsed !== null ? parsed : null;
  } catch {
    return null;
  }
}

/** Mirrors dashboard ``_monthly_estimate_usd`` — safe for UI totals and badges. */
export function monthlyEstimateUsd(costImpact: unknown): number | null {
  let value = costImpact;
  if (typeof value === 'string') {
    const parsed = parseJson<Record<string, unknown>>(value);
    if (!parsed) return null;
    value = parsed;
  }
  if (!value || typeof value !== 'object') return null;
  const raw = (value as { monthly_estimate_usd?: unknown }).monthly_estimate_usd;
  if (typeof raw === 'boolean') return null;
  const n = typeof raw === 'number' ? raw : typeof raw === 'string' ? Number(raw) : NaN;
  if (!Number.isFinite(n) || n < 0) return null;
  return n;
}

function normalizeCostImpact(
  costImpact: DriftEvent['cost_impact'],
): DriftEvent['cost_impact'] {
  const parsed =
    parseJson<NonNullable<DriftEvent['cost_impact']>>(costImpact) ?? costImpact;
  if (!parsed || typeof parsed !== 'object') return parsed;
  const monthly = monthlyEstimateUsd(parsed);
  if (monthly == null) return parsed;
  return { ...parsed, monthly_estimate_usd: monthly };
}

export function normalizeDriftEvent(e: DriftEvent): DriftEvent {
  return {
    ...e,
    changes_jsonb:
      parseJson<NonNullable<DriftEvent['changes_jsonb']>>(e.changes_jsonb) ??
      e.changes_jsonb,
    fields_changed:
      parseJson<NonNullable<DriftEvent['fields_changed']>>(e.fields_changed) ??
      e.fields_changed,
    cost_impact: normalizeCostImpact(e.cost_impact),
  };
}

/** Prefer a real stored region; fall back to the environment config for
 *  legacy rows that persisted the literal ``unknown``. */
export function displayRegion(
  eventRegion: string | null | undefined,
  envRegion?: string | null,
): string {
  const stored = (eventRegion || '').trim();
  if (stored && stored.toLowerCase() !== 'unknown') return stored;
  const fallback = (envRegion || '').trim();
  if (fallback) return fallback;
  return stored || '—';
}
