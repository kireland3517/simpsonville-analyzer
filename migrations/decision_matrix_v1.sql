-- Decision Matrix MVP — run once in Supabase SQL editor.

create table if not exists decision_matrices (
  id                uuid primary key default gen_random_uuid(),
  property_id       text not null,
  version           int not null,
  status            text not null default 'draft'
                    check (status in ('draft', 'finalized', 'stale')),
  evidence_snapshot jsonb not null,
  evidence_hash     text not null,
  actionable_count  int not null default 0,
  walkthrough_count int not null default 0,
  photo_only_count  int not null default 0,
  created_at        timestamptz not null default now(),
  unique (property_id, version)
);

create index if not exists idx_decision_matrices_property
  on decision_matrices (property_id, version desc);

create table if not exists decision_matrix_rows (
  id                  uuid primary key default gen_random_uuid(),
  matrix_id           uuid not null references decision_matrices(id) on delete cascade,
  component_id        text not null,
  walkthrough_item_id uuid,
  zone                text not null,
  component           text not null,
  confidence_tier     text not null,
  evidence_sources    jsonb not null default '[]',
  walkthrough_notes   text,
  photo_evidence      jsonb not null default '[]',
  current_state       text not null,
  buyer_impact        text not null,
  inspection_risk     text not null,
  marketability_risk  text not null,
  decision_status     text not null
                      check (decision_status in (
                        'required_action', 'decision_required', 'monitor', 'informational'
                      )),
  recommended_action  text not null
                      check (recommended_action in (
                        'leave_as_is', 'clean', 'repair', 'refresh',
                        'replace', 'further_inspect'
                      )),
  created_at          timestamptz not null default now(),
  unique (matrix_id, component_id)
);

create index if not exists idx_dm_rows_matrix on decision_matrix_rows (matrix_id);

create table if not exists property_decision_state (
  property_id           text primary key,
  current_matrix_id     uuid references decision_matrices(id),
  current_evidence_hash text,
  updated_at            timestamptz not null default now()
);
