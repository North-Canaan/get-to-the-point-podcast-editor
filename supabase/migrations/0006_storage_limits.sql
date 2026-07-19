-- Enforce the same hard ceiling as MAX_OUTPUT_BYTES even when clients upload
-- directly with a signed Storage URL and never call output-complete.
update storage.buckets
set file_size_limit = 1000000000
where id = 'podcast-artifacts';
