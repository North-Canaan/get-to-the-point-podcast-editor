-- Some podcast clients require both a new GUID revision and a newer publication
-- date before they surface a previously cached enclosure again.
update public.private_feed_items
set published_at = now(),
    updated_at = now()
where job_id = 'f628c49d-73dd-49be-96b7-c850e6016cc5'::uuid;
