-- Atomic admission, baseline tracking, and terminal-state publication guards.
alter table public.feed_subscriptions
  add column if not exists baseline_completed_at timestamptz;

create table if not exists public.automatic_daily_usage (
  usage_day date primary key,
  source_minutes integer not null default 0 check (source_minutes >= 0),
  updated_at timestamptz not null default now()
);

create table if not exists public.automatic_source_admissions (
  source_episode_id uuid primary key references public.source_episodes(id) on delete cascade,
  usage_day date not null references public.automatic_daily_usage(usage_day) on delete restrict,
  source_minutes integer not null check (source_minutes > 0),
  created_at timestamptz not null default now()
);

alter table public.automatic_daily_usage enable row level security;
alter table public.automatic_daily_usage force row level security;
alter table public.automatic_source_admissions enable row level security;
alter table public.automatic_source_admissions force row level security;
revoke all on table public.automatic_daily_usage from public, anon, authenticated;
revoke all on table public.automatic_source_admissions from public, anon, authenticated;
drop policy if exists "service role owns automatic daily usage" on public.automatic_daily_usage;
create policy "service role owns automatic daily usage" on public.automatic_daily_usage
  for all to service_role using (true) with check (true);
drop policy if exists "service role owns automatic source admissions" on public.automatic_source_admissions;
create policy "service role owns automatic source admissions" on public.automatic_source_admissions
  for all to service_role using (true) with check (true);

create or replace function public.admit_automatic_delivery(
  target_subscription_id uuid,
  target_source_episode_id uuid,
  recipe_snapshot jsonb,
  policy_version integer,
  source_minutes integer,
  daily_source_minutes_limit integer,
  daily_delivery_limit integer
) returns jsonb
language plpgsql security definer set search_path = public, pg_temp
as $$
declare
  delivery_id uuid;
  new_job_id uuid := gen_random_uuid();
  owner_id uuid;
  episode_title text;
  today date := (now() at time zone 'utc')::date;
  used_minutes integer;
  deliveries_today integer;
begin
  if source_minutes < 1 or daily_source_minutes_limit < 1 or daily_delivery_limit < 1 then
    raise exception 'invalid automatic admission limits';
  end if;

  -- Serialize all admissions for a subscription and UTC day. The daily usage row
  -- below serializes the global budget across subscriptions and worker invocations.
  perform pg_advisory_xact_lock(hashtextextended(target_subscription_id::text || ':' || today::text, 0));

  select subscription.user_id, episode.title into owner_id, episode_title
  from public.feed_subscriptions subscription
  join public.source_episodes episode on episode.id = target_source_episode_id
  where subscription.id = target_subscription_id
    and subscription.source_feed_id = episode.source_feed_id
    and subscription.status = 'active';
  if owner_id is null then raise exception 'ineligible automatic delivery'; end if;

  select id into delivery_id from public.subscription_deliveries
  where subscription_id = target_subscription_id and source_episode_id = target_source_episode_id;
  if delivery_id is not null then
    return jsonb_build_object('id', delivery_id, 'created', false, 'admitted', true);
  end if;

  select count(*) into deliveries_today
  from public.subscription_deliveries
  where subscription_id = target_subscription_id
    and created_at >= today::timestamptz
    and created_at < (today + 1)::timestamptz;
  if deliveries_today >= daily_delivery_limit then
    return jsonb_build_object('created', false, 'admitted', false, 'reason', 'subscription_daily_limit');
  end if;

  if not exists (
    select 1 from public.automatic_source_admissions
    where source_episode_id = target_source_episode_id
  ) then
    insert into public.automatic_daily_usage (usage_day) values (today)
      on conflict (usage_day) do nothing;
    select automatic_daily_usage.source_minutes into used_minutes
      from public.automatic_daily_usage where usage_day = today for update;
    if used_minutes + source_minutes > daily_source_minutes_limit then
      return jsonb_build_object('created', false, 'admitted', false, 'reason', 'global_daily_limit');
    end if;
    insert into public.automatic_source_admissions (source_episode_id, usage_day, source_minutes)
      values (target_source_episode_id, today, source_minutes);
    update public.automatic_daily_usage
      set source_minutes = used_minutes + $5,
          updated_at = now()
      where usage_day = today;
  end if;

  insert into public.jobs (id, status, user_id, episode_title)
  values (new_job_id, 'queued', owner_id, coalesce(episode_title, 'Edited episode'));
  insert into public.subscription_deliveries
    (subscription_id, source_episode_id, job_id, recipe_snapshot_json, selection_policy_version)
  values (target_subscription_id, target_source_episode_id, new_job_id, recipe_snapshot, policy_version)
  returning id into delivery_id;
  return jsonb_build_object('id', delivery_id, 'created', true, 'admitted', true);
end;
$$;
revoke all on function public.admit_automatic_delivery(uuid, uuid, jsonb, integer, integer, integer, integer) from public;
grant execute on function public.admit_automatic_delivery(uuid, uuid, jsonb, integer, integer, integer, integer) to service_role;

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
declare target_job_id uuid; owner_id uuid; episode_title text; target_feed_id uuid; changed integer;
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
  get diagnostics changed = row_count;
  if changed = 0 then return false; end if;

  update public.jobs set status = 'done', output_storage_path = 'r2:' || target_r2_key,
    output_size_bytes = target_size_bytes, error = null where id = target_job_id;
  select id into target_feed_id from public.private_feeds where user_id = owner_id for update;
  if target_feed_id is null then
    insert into public.private_feeds (token_hash, user_id)
      values (personal_feed_token_hash, owner_id) returning id into target_feed_id;
  else
    update public.private_feeds set token_hash = personal_feed_token_hash
      where id = target_feed_id;
  end if;
  insert into public.private_feed_items (feed_id, job_id, title, size_bytes)
    values (target_feed_id, target_job_id, coalesce(episode_title, 'Edited episode'), target_size_bytes)
    on conflict (feed_id, job_id) do update set size_bytes = excluded.size_bytes, updated_at = now();
  return true;
end;
$$;
revoke all on function public.publish_automatic_delivery(uuid, text, bigint, numeric, text, text) from public;
grant execute on function public.publish_automatic_delivery(uuid, text, bigint, numeric, text, text) to service_role;
