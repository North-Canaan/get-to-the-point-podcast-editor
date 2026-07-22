-- Durable state for automatic podcast subscriptions. Modal execution is at-least-once;
-- uniqueness constraints and these transactional RPCs make re-entry safe.
create table if not exists public.source_feeds (
  id uuid primary key default gen_random_uuid(),
  normalized_url text not null unique,
  title text,
  etag text,
  last_modified text,
  last_polled_at timestamptz,
  last_poll_error text,
  consecutive_failures integer not null default 0 check (consecutive_failures >= 0),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.feed_subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public."user"(id) on delete cascade,
  source_feed_id uuid not null references public.source_feeds(id) on delete cascade,
  status text not null default 'active' check (status in ('active', 'paused', 'deleted')),
  recipe_json jsonb not null,
  start_after timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists feed_subscriptions_active_unique
  on public.feed_subscriptions (user_id, source_feed_id) where status <> 'deleted';
create index if not exists feed_subscriptions_source_status_idx
  on public.feed_subscriptions (source_feed_id, status);

create table if not exists public.source_episodes (
  id uuid primary key default gen_random_uuid(),
  source_feed_id uuid not null references public.source_feeds(id) on delete cascade,
  rss_guid text,
  identity_hash text not null unique,
  enclosure_url text not null,
  enclosure_url_hash text not null,
  title text,
  published_at timestamptz,
  language text,
  duration_seconds integer check (duration_seconds is null or duration_seconds between 1 and 21600),
  analysis_status text not null default 'queued'
    check (analysis_status in ('queued', 'analyzing', 'ready', 'failed')),
  analysis_version integer not null default 1 check (analysis_version > 0),
  assemblyai_transcript_id text,
  transcript_storage_path text,
  highlights_storage_path text,
  analysis_attempts integer not null default 0 check (analysis_attempts >= 0),
  analysis_error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists source_episodes_feed_published_idx
  on public.source_episodes (source_feed_id, published_at desc);
create index if not exists source_episodes_reconcile_idx
  on public.source_episodes (analysis_status, updated_at);

create table if not exists public.subscription_deliveries (
  id uuid primary key default gen_random_uuid(),
  subscription_id uuid not null references public.feed_subscriptions(id) on delete cascade,
  source_episode_id uuid not null references public.source_episodes(id) on delete cascade,
  job_id uuid not null unique references public.jobs(id) on delete cascade,
  status text not null default 'waiting'
    check (status in ('waiting', 'processing', 'published', 'no_matching_highlights', 'failed')),
  recipe_snapshot_json jsonb not null,
  selection_policy_version integer not null check (selection_policy_version > 0),
  selected_highlight_ids_json jsonb,
  expected_duration_seconds numeric,
  modal_call_id text,
  attempts integer not null default 0 check (attempts >= 0),
  last_error_code text,
  r2_object_key text,
  output_size_bytes bigint check (output_size_bytes is null or output_size_bytes > 0),
  output_duration_seconds numeric,
  output_sha256 text,
  published_at timestamptz,
  expires_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (subscription_id, source_episode_id)
);
create index if not exists subscription_deliveries_reconcile_idx
  on public.subscription_deliveries (status, updated_at);

do $$
declare table_name text;
begin
  foreach table_name in array array['source_feeds', 'feed_subscriptions', 'source_episodes', 'subscription_deliveries']
  loop
    execute format('drop trigger if exists %I_set_updated_at on public.%I', table_name, table_name);
    execute format('create trigger %I_set_updated_at before update on public.%I for each row execute function public.set_updated_at()', table_name, table_name);
    execute format('alter table public.%I enable row level security', table_name);
    execute format('alter table public.%I force row level security', table_name);
    execute format('revoke all on table public.%I from public, anon, authenticated', table_name);
    execute format('drop policy if exists "service role owns %s" on public.%I', replace(table_name, '_', ' '), table_name);
    execute format('create policy "service role owns %s" on public.%I for all to service_role using (true) with check (true)', replace(table_name, '_', ' '), table_name);
  end loop;
end $$;

create or replace function public.create_automatic_delivery(
  target_subscription_id uuid,
  target_source_episode_id uuid,
  recipe_snapshot jsonb,
  policy_version integer
) returns uuid
language plpgsql security definer set search_path = public, pg_temp
as $$
declare delivery_id uuid; new_job_id uuid := gen_random_uuid(); owner_id uuid; episode_title text;
begin
  select subscription.user_id, episode.title into owner_id, episode_title
  from public.feed_subscriptions subscription
  join public.source_episodes episode on episode.id = target_source_episode_id
  where subscription.id = target_subscription_id
    and subscription.source_feed_id = episode.source_feed_id
    and subscription.status = 'active';
  if owner_id is null then raise exception 'ineligible automatic delivery'; end if;

  select id into delivery_id from public.subscription_deliveries
  where subscription_id = target_subscription_id and source_episode_id = target_source_episode_id;
  if delivery_id is not null then return delivery_id; end if;

  insert into public.jobs (id, status, user_id, episode_title)
  values (new_job_id, 'queued', owner_id, coalesce(episode_title, 'Edited episode'));
  insert into public.subscription_deliveries
    (subscription_id, source_episode_id, job_id, recipe_snapshot_json, selection_policy_version)
  values (target_subscription_id, target_source_episode_id, new_job_id, recipe_snapshot, policy_version)
  on conflict (subscription_id, source_episode_id) do nothing returning id into delivery_id;
  if delivery_id is null then
    delete from public.jobs where id = new_job_id;
    select id into delivery_id from public.subscription_deliveries
      where subscription_id = target_subscription_id and source_episode_id = target_source_episode_id;
  end if;
  return delivery_id;
end;
$$;
revoke all on function public.create_automatic_delivery(uuid, uuid, jsonb, integer) from public;
grant execute on function public.create_automatic_delivery(uuid, uuid, jsonb, integer) to service_role;

create or replace function public.publish_automatic_delivery(
  target_delivery_id uuid,
  target_r2_key text,
  target_size_bytes bigint,
  target_duration_seconds numeric,
  target_sha256 text,
  personal_feed_token_hash text
) returns boolean
language plpgsql security definer set search_path = public, pg_temp
as $$
declare target_job_id uuid; owner_id uuid; episode_title text; target_feed_id uuid;
begin
  select delivery.job_id, subscription.user_id, episode.title
    into target_job_id, owner_id, episode_title
  from public.subscription_deliveries delivery
  join public.feed_subscriptions subscription on subscription.id = delivery.subscription_id
  join public.source_episodes episode on episode.id = delivery.source_episode_id
  where delivery.id = target_delivery_id for update of delivery;
  if target_job_id is null then return false; end if;

  update public.subscription_deliveries set
    status = 'published', r2_object_key = target_r2_key,
    output_size_bytes = target_size_bytes, output_duration_seconds = target_duration_seconds,
    output_sha256 = target_sha256, published_at = coalesce(published_at, now()),
    expires_at = coalesce(expires_at, now() + interval '90 days'), last_error_code = null
  where id = target_delivery_id and status in ('waiting', 'processing', 'published');
  update public.jobs set status = 'done', output_storage_path = 'r2:' || target_r2_key,
    output_size_bytes = target_size_bytes, error = null where id = target_job_id;
  insert into public.private_feeds (token_hash, user_id)
    values (personal_feed_token_hash, owner_id)
    on conflict (token_hash) do update set user_id = excluded.user_id
    returning id into target_feed_id;
  insert into public.private_feed_items (feed_id, job_id, title, size_bytes)
    values (target_feed_id, target_job_id, coalesce(episode_title, 'Edited episode'), target_size_bytes)
    on conflict (feed_id, job_id) do update set size_bytes = excluded.size_bytes, updated_at = now();
  return true;
end;
$$;
revoke all on function public.publish_automatic_delivery(uuid, text, bigint, numeric, text, text) from public;
grant execute on function public.publish_automatic_delivery(uuid, text, bigint, numeric, text, text) to service_role;

create or replace function public.retry_automatic_delivery(
  target_job_id uuid,
  target_user_id uuid
) returns boolean
language plpgsql security definer set search_path = public, pg_temp
as $$
declare changed integer;
begin
  update public.subscription_deliveries delivery set
    status = 'waiting', attempts = 0, last_error_code = null
  from public.feed_subscriptions subscription
  where delivery.subscription_id = subscription.id
    and delivery.job_id = target_job_id
    and subscription.user_id = target_user_id
    and delivery.status = 'failed';
  get diagnostics changed = row_count;
  if changed = 0 then return false; end if;
  update public.jobs set status = 'queued', error = null where id = target_job_id;
  return true;
end;
$$;
revoke all on function public.retry_automatic_delivery(uuid, uuid) from public;
grant execute on function public.retry_automatic_delivery(uuid, uuid) to service_role;
