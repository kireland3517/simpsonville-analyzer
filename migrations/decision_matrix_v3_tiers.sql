-- Decision Matrix v3 — listing readiness tiers on rows.
-- Run in Supabase SQL Editor after decision_matrix_v2.sql.

alter table decision_matrix_rows
  add column if not exists minimum_tier text
    check (minimum_tier in ('must_do', 'should_do', 'nice_to_do', 'aspirational')),
  add column if not exists recommended_tier text
    check (recommended_tier in ('must_do', 'should_do', 'nice_to_do', 'aspirational'));

create index if not exists idx_dm_rows_minimum_tier
  on decision_matrix_rows (matrix_id, minimum_tier);

create index if not exists idx_dm_rows_recommended_tier
  on decision_matrix_rows (matrix_id, recommended_tier);
