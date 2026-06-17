-- Migration v7: expand recommended_action check constraint to include
-- paint, needs_assessment (and not_doing for seller exclusions).
-- Run once in the Supabase SQL editor.

alter table decision_matrix_rows
  drop constraint if exists decision_matrix_rows_recommended_action_check;

alter table decision_matrix_rows
  add constraint decision_matrix_rows_recommended_action_check
  check (recommended_action in (
    'leave_as_is', 'clean', 'repair', 'refresh',
    'replace', 'further_inspect',
    'paint', 'needs_assessment', 'not_doing'
  ));
