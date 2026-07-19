-- Defense in depth for every application table exposed through the public schema.
alter table public.jobs force row level security;
alter table public.feeds force row level security;
alter table public.private_feeds force row level security;
alter table public.private_feed_items force row level security;
alter table public."user" force row level security;
alter table public.session force row level security;
alter table public.account force row level security;
alter table public.verification force row level security;

revoke all on table public.jobs, public.feeds, public.private_feeds,
  public.private_feed_items, public."user", public.session, public.account,
  public.verification from public, anon, authenticated;

-- Persistent API rate limiting for serverless Python functions.
create table if not exists public.api_rate_limits (
  key text primary key,
  window_started_at timestamptz not null default now(),
  request_count integer not null default 0 check (request_count >= 0),
  updated_at timestamptz not null default now()
);

alter table public.api_rate_limits enable row level security;
alter table public.api_rate_limits force row level security;
revoke all on table public.api_rate_limits from public, anon, authenticated;

drop policy if exists "service role owns api rate limits" on public.api_rate_limits;
create policy "service role owns api rate limits"
on public.api_rate_limits for all to service_role
using (true) with check (true);

create or replace function public.consume_api_rate_limit(
  rate_key text,
  window_seconds integer,
  maximum integer
) returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  current_count integer;
begin
  if length(rate_key) > 512 or window_seconds < 1 or maximum < 1 then
    return false;
  end if;

  insert into public.api_rate_limits as limits (key, window_started_at, request_count, updated_at)
  values (rate_key, now(), 1, now())
  on conflict (key) do update set
    window_started_at = case
      when limits.window_started_at <= now() - make_interval(secs => window_seconds)
        then now()
      else limits.window_started_at
    end,
    request_count = case
      when limits.window_started_at <= now() - make_interval(secs => window_seconds)
        then 1
      else limits.request_count + 1
    end,
    updated_at = now()
  returning request_count into current_count;

  return current_count <= maximum;
end;
$$;

revoke all on function public.consume_api_rate_limit(text, integer, integer) from public;
grant execute on function public.consume_api_rate_limit(text, integer, integer) to service_role;

-- Better Auth's serverless-safe database rate limiter.
create table if not exists public."rateLimit" (
  id text primary key,
  key text not null,
  count integer not null,
  "lastRequest" bigint not null
);
create index if not exists rate_limit_key_idx on public."rateLimit" (key);
alter table public."rateLimit" enable row level security;
alter table public."rateLimit" force row level security;
revoke all on table public."rateLimit" from public, anon, authenticated;
grant select, insert, update, delete on public."rateLimit" to better_auth_app;

-- The artifact bucket must never become public accidentally.
update storage.buckets set public = false where id = 'podcast-artifacts';
