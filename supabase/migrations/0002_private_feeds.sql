alter table public.jobs add column if not exists output_size_bytes bigint;

create table if not exists public.private_feeds (
  id uuid primary key default gen_random_uuid(),
  token_hash text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.private_feed_items (
  feed_id uuid not null references public.private_feeds(id) on delete cascade,
  job_id uuid not null references public.jobs(id) on delete cascade,
  title text not null,
  size_bytes bigint not null default 0,
  published_at timestamptz not null default now(),
  primary key (feed_id, job_id)
);

drop trigger if exists private_feeds_set_updated_at on public.private_feeds;
create trigger private_feeds_set_updated_at
before update on public.private_feeds
for each row execute function public.set_updated_at();

alter table public.private_feeds enable row level security;
alter table public.private_feed_items enable row level security;

drop policy if exists "service role owns private feeds" on public.private_feeds;
create policy "service role owns private feeds"
on public.private_feeds for all
using (auth.role() = 'service_role')
with check (auth.role() = 'service_role');

drop policy if exists "service role owns private feed items" on public.private_feed_items;
create policy "service role owns private feed items"
on public.private_feed_items for all
using (auth.role() = 'service_role')
with check (auth.role() = 'service_role');
