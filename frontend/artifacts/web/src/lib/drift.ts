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

export function normalizeDriftEvent(e: DriftEvent): DriftEvent {
  return {
    ...e,
    changes_jsonb:
      parseJson<NonNullable<DriftEvent['changes_jsonb']>>(e.changes_jsonb) ??
      e.changes_jsonb,
    fields_changed:
      parseJson<NonNullable<DriftEvent['fields_changed']>>(e.fields_changed) ??
      e.fields_changed,
    cost_impact:
      parseJson<NonNullable<DriftEvent['cost_impact']>>(e.cost_impact) ??
      e.cost_impact,
  };
}
