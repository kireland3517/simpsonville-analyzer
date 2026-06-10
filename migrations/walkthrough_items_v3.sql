-- Run once in Supabase SQL editor after walkthrough_items_v2.sql

alter table walkthrough_items add column if not exists condition_label text;
alter table walkthrough_items add column if not exists looks_fine boolean default false;
alter table walkthrough_items add column if not exists condition_overridden boolean default false;
alter table walkthrough_items add column if not exists category_overridden boolean default false;
alter table walkthrough_items add column if not exists visibility_overridden boolean default false;
alter table walkthrough_items add column if not exists risk_overridden boolean default false;
alter table walkthrough_items add column if not exists action_overridden boolean default false;
alter table walkthrough_items add column if not exists observations jsonb;

-- Backfill condition_label from legacy condition_score
update walkthrough_items set condition_label = case
  when condition_score is null then 'unknown'
  when condition_score >= 4 then 'good'
  when condition_score = 3 then 'fair'
  when condition_score = 2 then 'poor'
  when condition_score <= 1 then 'replace'
  else 'unknown'
end
where condition_label is null;
