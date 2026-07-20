create or replace function public.claim_anonymous_private_feed(
  anonymous_hash text,
  account_hash text,
  account_user_id uuid
) returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  source_feed public.private_feeds%rowtype;
  target_feed public.private_feeds%rowtype;
  claimed_count integer := 0;
begin
  if anonymous_hash = account_hash then
    return 0;
  end if;

  select * into source_feed
  from public.private_feeds
  where token_hash = anonymous_hash
  for update;

  if not found then
    return 0;
  end if;
  if source_feed.user_id is not null then
    if source_feed.user_id = account_user_id then
      return 0;
    end if;
    raise exception 'feed is already associated with another account';
  end if;

  select * into target_feed
  from public.private_feeds
  where user_id = account_user_id
  for update;

  if not found then
    select * into target_feed
    from public.private_feeds
    where token_hash = account_hash
    for update;
    if found and target_feed.user_id is distinct from account_user_id then
      raise exception 'account feed token belongs to another account';
    end if;
  end if;

  if target_feed.id is null then
    insert into public.private_feeds (token_hash, user_id)
    values (account_hash, account_user_id)
    returning * into target_feed;
  end if;

  insert into public.private_feed_items (feed_id, job_id, title, size_bytes, published_at)
  select target_feed.id, item.job_id, item.title, item.size_bytes, item.published_at
  from public.private_feed_items item
  where item.feed_id = source_feed.id
  on conflict (feed_id, job_id) do update set
    title = excluded.title,
    size_bytes = excluded.size_bytes,
    published_at = least(public.private_feed_items.published_at, excluded.published_at);

  get diagnostics claimed_count = row_count;

  update public.jobs job
  set user_id = account_user_id
  where job.user_id is null
    and exists (
      select 1 from public.private_feed_items item
      where item.feed_id = source_feed.id and item.job_id = job.id
    );

  delete from public.private_feeds where id = source_feed.id;
  return claimed_count;
end;
$$;

revoke all on function public.claim_anonymous_private_feed(text, text, uuid) from public;
grant execute on function public.claim_anonymous_private_feed(text, text, uuid) to service_role;
