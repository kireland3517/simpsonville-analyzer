-- Decision Matrix v2 — options, scenarios, seller overrides.
-- Run in Supabase SQL Editor after decision_matrix_v1.sql.

create table if not exists decision_matrix_options (
  id                      uuid primary key default gen_random_uuid(),
  row_id                  uuid not null references decision_matrix_rows(id) on delete cascade,
  option_key              text not null
                          check (option_key in (
                            'leave_as_is','clean','repair','refresh','replace','further_inspect'
                          )),
  cost_low                numeric not null default 0,
  cost_high               numeric not null default 0,
  buyer_impact            text not null,
  inspection_risk_impact  text not null,
  marketability_impact    text not null,
  roi_quality             text not null,
  feasibility             text not null
                          check (feasibility in ('recommended','viable','discouraged','blocked')),
  is_recommended          boolean not null default false,
  rationale               jsonb not null default '{}',
  created_at              timestamptz not null default now(),
  unique (row_id, option_key)
);

create index if not exists idx_dm_options_row on decision_matrix_options (row_id);

alter table decision_matrix_rows
  add column if not exists available_options text[] default '{}',
  add column if not exists blocked_options text[] default '{}',
  add column if not exists seller_override boolean not null default false,
  add column if not exists seller_override_at timestamptz,
  add column if not exists seller_override_note text,
  add column if not exists selected_option_key text,
  add column if not exists needs_manual_review boolean not null default false;

create table if not exists decision_matrix_scenarios (
  id                    uuid primary key default gen_random_uuid(),
  matrix_id             uuid not null references decision_matrices(id) on delete cascade,
  scenario              text not null,
  buyer_profile         text not null default 'general',
  selection_policy      jsonb not null default '{}',
  selected_rows         jsonb not null default '[]',
  excluded_rows         jsonb not null default '[]',
  total_cost_low        numeric,
  total_cost_high       numeric,
  created_at            timestamptz not null default now(),
  unique (matrix_id, scenario, buyer_profile)
);

create index if not exists idx_dm_scenarios_matrix on decision_matrix_scenarios (matrix_id);
