-- Decision Matrix v4 — collapse aspirational into nice_to_do (three tiers).
-- Run in Supabase SQL Editor after decision_matrix_v3_tiers.sql.

update decision_matrix_rows
  set minimum_tier = 'nice_to_do'
  where minimum_tier = 'aspirational';

update decision_matrix_rows
  set recommended_tier = 'nice_to_do'
  where recommended_tier = 'aspirational';

alter table decision_matrix_rows
  drop constraint if exists decision_matrix_rows_minimum_tier_check;

alter table decision_matrix_rows
  drop constraint if exists decision_matrix_rows_recommended_tier_check;

alter table decision_matrix_rows
  add constraint decision_matrix_rows_minimum_tier_check
    check (minimum_tier in ('must_do', 'should_do', 'nice_to_do')),
  add constraint decision_matrix_rows_recommended_tier_check
    check (recommended_tier in ('must_do', 'should_do', 'nice_to_do'));
