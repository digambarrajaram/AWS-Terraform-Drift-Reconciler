-- Rows written before 2026-08 stored json.dumps() text *inside* jsonb
-- columns, leaving a jsonb string instead of the intended object/array.
-- Object.entries(string) then iterates characters, so every consumer
-- rendered garbage (e.g. 80+ empty numbered BEFORE/AFTER blocks).
-- Parse those back to real jsonb.  Writer fix already landed in
-- drift_reconciler/drift_history.py (append_entry).

update drift_events set changes_jsonb = (changes_jsonb #>> '{}')::jsonb
  where jsonb_typeof(changes_jsonb) = 'string';

update drift_events set fields_changed = (fields_changed #>> '{}')::jsonb
  where jsonb_typeof(fields_changed) = 'string';

update drift_events set cost_impact = (cost_impact #>> '{}')::jsonb
  where jsonb_typeof(cost_impact) = 'string';
