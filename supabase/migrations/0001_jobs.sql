create table if not exists public.jobs (
  id uuid primary key,
  source_url text,
  resolved_audio_url text,
  assemblyai_transcript_id text,
  output_storage_path text,
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
  worker_id text,
  locked_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.jobs add column if not exists source_url text;
alter table public.jobs add column if not exists resolved_audio_url text;
alter table public.jobs add column if not exists assemblyai_transcript_id text;
alter table public.jobs add column if not exists output_storage_path text;
alter table public.jobs add column if not exists worker_id text;
alter table public.jobs add column if not exists locked_at timestamptz;

create index if not exists jobs_available_idx
on public.jobs (status, worker_id, created_at);

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
