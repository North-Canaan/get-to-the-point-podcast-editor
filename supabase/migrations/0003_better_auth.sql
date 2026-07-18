create table if not exists public."user" (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  email text not null unique,
  "emailVerified" boolean not null default false,
  image text,
  "createdAt" timestamptz not null default now(),
  "updatedAt" timestamptz not null default now()
);

create table if not exists public.session (
  id uuid primary key default gen_random_uuid(),
  "userId" uuid not null references public."user"(id) on delete cascade,
  token text not null unique,
  "expiresAt" timestamptz not null,
  "ipAddress" text,
  "userAgent" text,
  "createdAt" timestamptz not null default now(),
  "updatedAt" timestamptz not null default now()
);

create index if not exists session_user_id_idx on public.session ("userId");

create table if not exists public.account (
  id uuid primary key default gen_random_uuid(),
  "userId" uuid not null references public."user"(id) on delete cascade,
  "accountId" text not null,
  "providerId" text not null,
  "accessToken" text,
  "refreshToken" text,
  "accessTokenExpiresAt" timestamptz,
  "refreshTokenExpiresAt" timestamptz,
  scope text,
  "idToken" text,
  password text,
  "createdAt" timestamptz not null default now(),
  "updatedAt" timestamptz not null default now()
);

create index if not exists account_user_id_idx on public.account ("userId");

create table if not exists public.verification (
  id uuid primary key default gen_random_uuid(),
  identifier text not null,
  value text not null,
  "expiresAt" timestamptz not null,
  "createdAt" timestamptz not null default now(),
  "updatedAt" timestamptz not null default now()
);

alter table public.jobs add column if not exists user_id uuid references public."user"(id) on delete set null;
alter table public.jobs add column if not exists episode_title text;
create index if not exists jobs_user_id_created_idx on public.jobs (user_id, created_at desc);

alter table public.private_feeds add column if not exists user_id uuid references public."user"(id) on delete cascade;
create unique index if not exists private_feeds_user_id_idx on public.private_feeds (user_id) where user_id is not null;

alter table public."user" enable row level security;
alter table public.session enable row level security;
alter table public.account enable row level security;
alter table public.verification enable row level security;

create policy "service role owns auth users" on public."user" for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');
create policy "service role owns auth sessions" on public.session for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');
create policy "service role owns auth accounts" on public.account for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');
create policy "service role owns auth verifications" on public.verification for all using (auth.role() = 'service_role') with check (auth.role() = 'service_role');
