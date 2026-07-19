-- Better Auth does not provide an id when it creates database-backed rate-limit
-- rows. Without a database default, every session lookup fails before auth runs.
alter table public."rateLimit"
alter column id set default gen_random_uuid()::text;
