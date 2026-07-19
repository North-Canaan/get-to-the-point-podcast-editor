alter table public.private_feed_items
alter column updated_at drop default,
alter column updated_at drop not null;

-- Existing entries retain their original GUID. Only the enclosure with the cached
-- failed download receives a revision suffix and is presented as refreshed.
update public.private_feed_items
set updated_at = null
where job_id <> 'f628c49d-73dd-49be-96b7-c850e6016cc5'::uuid;

update public.private_feed_items
set updated_at = now()
where job_id = 'f628c49d-73dd-49be-96b7-c850e6016cc5'::uuid;
