-- API keys for TranscribeAI (run once in Supabase > SQL Editor).
-- Only a SHA-256 hash of each key is stored; the key itself is shown once at creation.
create table if not exists public.api_keys (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null default 'My key',
  key_hash text not null unique,
  key_prefix text not null,
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at timestamptz
);
create index if not exists api_keys_user_idx on public.api_keys (user_id);

-- RLS on, no policies: the public (anon) key cannot read or write this table.
-- The server uses the service_role key, which bypasses RLS.
alter table public.api_keys enable row level security;
