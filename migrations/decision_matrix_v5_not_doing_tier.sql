-- Allow seller-managed "Not Doing" rows in the decision matrix.
-- This is intentionally separate from report-generation tiers.

alter table decision_matrix_rows
  drop constraint if exists decision_matrix_rows_minimum_tier_check;

alter table decision_matrix_rows
  drop constraint if exists decision_matrix_rows_recommended_tier_check;

alter table decision_matrix_rows
  add constraint decision_matrix_rows_minimum_tier_check
    check (minimum_tier in ('must_do', 'should_do', 'nice_to_do', 'not_doing')),
  add constraint decision_matrix_rows_recommended_tier_check
    check (recommended_tier in ('must_do', 'should_do', 'nice_to_do', 'not_doing'));
