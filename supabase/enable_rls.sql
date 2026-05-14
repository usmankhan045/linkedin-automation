-- ============================================================
-- Supabase RLS hardening for LinkedIn Automation
-- Fixes Security Advisor warning: rls_disabled_in_public
--
-- This project runs backend automation with SUPABASE_SERVICE_ROLE_KEY.
-- No browser/client users need direct table access, so these tables should
-- have RLS enabled with no anon/authenticated policies.
--
-- Run in Supabase SQL Editor for project qtxefvmeexxwemjlztvs.
-- Safe to re-run.
-- ============================================================

do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'posts',
    'stories',
    'processed_comments',
    'config',
    'content_backlog',
    'leads_seen',
    'vault_items'
  ]
  loop
    if to_regclass(format('public.%I', table_name)) is not null then
      execute format('alter table public.%I enable row level security', table_name);

      -- Remove direct browser/API access for public Supabase roles.
      -- The backend service role keeps working and bypasses RLS.
      execute format('revoke all on table public.%I from anon, authenticated', table_name);
    end if;
  end loop;
end $$;
