create table if not exists public.jobs (
  id uuid primary key,
  status text not null check (
    status in (
      'queued',
      'ingesting',
      'transcribing',
      'detecting_highlights',
      'needs_review',
      'splicing',
      'done',
      'error'
    )
  ),
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists jobs_set_updated_at on public.jobs;
create trigger jobs_set_updated_at
before update on public.jobs
for each row execute function public.set_updated_at();

alter table public.jobs enable row level security;

drop policy if exists "service role owns jobs" on public.jobs;
create policy "service role owns jobs"
on public.jobs
for all
using (auth.role() = 'service_role')
with check (auth.role() = 'service_role');

insert into storage.buckets (id, name, public)
values ('podcast-artifacts', 'podcast-artifacts', false)
on conflict (id) do nothing;
