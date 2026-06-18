-- Persist actual spend per decision matrix row.

alter table decision_matrix_rows
  add column if not exists actual_spend numeric;
