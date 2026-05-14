-- vault_items: durable vault state for GitHub Actions orchestrator runs.
--
-- When orchestrator.py runs on GitHub Actions the local filesystem is ephemeral,
-- so vault subdirectories (Needs_Action, Pending_Approval, Done, …) are stored
-- here instead. Each row is one vault file. The 'folder' column mirrors the
-- vault subdirectory; moving a file between subdirectories is an UPDATE, not
-- a filesystem rename.
--
-- Run this once in your Supabase SQL editor to create the table.

create table if not exists vault_items (
  id               uuid         primary key default gen_random_uuid(),
  filename         text         not null unique,
  folder           text         not null,
  content          text         not null default '',
  frontmatter_json jsonb        not null default '{}',
  created_at       timestamptz  not null default now(),
  updated_at       timestamptz  not null default now()
);

-- folder index: fast listing of Needs_Action / Pending_Approval items
create index if not exists vault_items_folder_idx   on vault_items (folder);

-- created_at index: FIFO ordering for list_needs_action()
create index if not exists vault_items_created_idx  on vault_items (created_at);

-- Auto-bump updated_at on every row modification
create or replace function set_vault_items_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists vault_items_updated_at on vault_items;
create trigger vault_items_updated_at
  before update on vault_items
  for each row execute procedure set_vault_items_updated_at();

alter table vault_items enable row level security;

-- Backend automation uses SUPABASE_SERVICE_ROLE_KEY. Do not expose vault state
-- to public browser/API roles.
revoke all on table vault_items from anon, authenticated;
