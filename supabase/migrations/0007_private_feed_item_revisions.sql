alter table public.private_feed_items
add column if not exists updated_at timestamptz not null default now();

drop trigger if exists private_feed_items_set_updated_at on public.private_feed_items;
create trigger private_feed_items_set_updated_at
before update on public.private_feed_items
for each row execute function public.set_updated_at();

-- Refresh the enclosure that was cached after its HEAD request failed. This changes
-- only its RSS GUID revision; the audio object and publication date stay unchanged.
update public.private_feed_items
set updated_at = now()
where job_id = 'f628c49d-73dd-49be-96b7-c850e6016cc5'::uuid;
