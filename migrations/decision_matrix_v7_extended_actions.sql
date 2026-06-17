-- Migration v7: expand action/option_key check constraints to include
-- paint, needs_assessment, not_doing.
-- Run once in the Supabase SQL editor.

-- 1. decision_matrix_rows.recommended_action
alter table decision_matrix_rows
  drop constraint if exists decision_matrix_rows_recommended_action_check;

alter table decision_matrix_rows
  add constraint decision_matrix_rows_recommended_action_check
  check (recommended_action in (
    'leave_as_is', 'clean', 'repair', 'refresh',
    'replace', 'further_inspect',
    'paint', 'needs_assessment', 'not_doing'
  ));

-- 2. decision_matrix_options.option_key
alter table decision_matrix_options
  drop constraint if exists decision_matrix_options_option_key_check;

alter table decision_matrix_options
  add constraint decision_matrix_options_option_key_check
  check (option_key in (
    'leave_as_is', 'clean', 'repair', 'refresh',
    'replace', 'further_inspect',
    'paint', 'needs_assessment', 'not_doing'
  ));
