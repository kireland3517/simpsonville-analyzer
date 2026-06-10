-- Run once in Supabase SQL editor.
-- Property-specific seller walkthrough checklist rows.

create table if not exists walkthrough_items (
  id uuid primary key default gen_random_uuid(),
  property_id text not null,
  zone text not null,
  component text not null,
  layer text not null,
  category text,
  condition_score integer,
  action text default 'assess',
  owner_note text,
  buyer_visibility text,
  inspection_risk text,
  estimated_cost_low integer,
  estimated_cost_high integer,
  priority_score integer,
  sort_order integer default 0,
  include_in_report boolean default true,
  source text default 'template',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists walkthrough_items_property_idx
  on walkthrough_items (property_id);

create unique index if not exists walkthrough_items_unique_seed
  on walkthrough_items (property_id, zone, component, layer);

-- Calculated + override columns (also in walkthrough_items_v2.sql for existing installs)
alter table walkthrough_items add column if not exists recommendation_bucket text;
alter table walkthrough_items add column if not exists report_type text;
alter table walkthrough_items add column if not exists roi_confidence text;
alter table walkthrough_items add column if not exists buyer_impact text;
alter table walkthrough_items add column if not exists urgency text;
alter table walkthrough_items add column if not exists project_group text;
alter table walkthrough_items add column if not exists cost_overridden boolean default false;
alter table walkthrough_items add column if not exists priority_overridden boolean default false;

-- v3 automation columns (also in walkthrough_items_v3.sql for existing installs)
alter table walkthrough_items add column if not exists condition_label text;
alter table walkthrough_items add column if not exists looks_fine boolean default false;
alter table walkthrough_items add column if not exists condition_overridden boolean default false;
alter table walkthrough_items add column if not exists category_overridden boolean default false;
alter table walkthrough_items add column if not exists visibility_overridden boolean default false;
alter table walkthrough_items add column if not exists risk_overridden boolean default false;
alter table walkthrough_items add column if not exists action_overridden boolean default false;
alter table walkthrough_items add column if not exists observations jsonb;
