-- Run after walkthrough_items.sql to add calculated + override columns.

alter table walkthrough_items add column if not exists recommendation_bucket text;
alter table walkthrough_items add column if not exists report_type text;
alter table walkthrough_items add column if not exists roi_confidence text;
alter table walkthrough_items add column if not exists buyer_impact text;
alter table walkthrough_items add column if not exists urgency text;
alter table walkthrough_items add column if not exists project_group text;
alter table walkthrough_items add column if not exists cost_overridden boolean default false;
alter table walkthrough_items add column if not exists priority_overridden boolean default false;
