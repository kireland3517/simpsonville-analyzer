-- Run once in Supabase SQL editor after walkthrough_items_v3.sql
-- Default Include OFF for untouched rows; backfill untouched template rows.

alter table walkthrough_items alter column include_in_report set default false;

update walkthrough_items
set include_in_report = false
where owner_note is null
  and coalesce(looks_fine, false) = false
  and coalesce(include_in_report, true) = true;
