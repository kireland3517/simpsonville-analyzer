-- Persist seller-edited cost overrides directly on decision matrix rows.

alter table decision_matrix_rows
  add column if not exists cost_low numeric,
  add column if not exists cost_high numeric;
