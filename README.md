# Get To The Point Podcast Editor

Hebrew-first podcast highlight editor. It ingests a podcast URL, transcribes and diarizes Hebrew
audio, asks Claude for guest-centered highlight candidates, lets a human approve/edit the final
segments, and splices an MP3 from the original audio.

## Stack

- Python 3.11
- FastAPI + Uvicorn
- yt-dlp and feedparser for ingest
- WhisperX large-v3 with Hebrew settings and pyannote diarization
- Anthropic Claude for highlight detection
- ffmpeg for final audio splicing
- Static HTML, CSS, and vanilla JavaScript frontend
- Supabase Postgres + Storage as the recommended free-tier production data layer

## Local Setup

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env
uvicorn podcast_editor.main:app --reload
```

For the heavy worker stages:

```bash
uv pip install -r requirements-worker.txt
```

You also need `ffmpeg` and `yt-dlp` available on PATH.

## Environment

Required for full pipeline:

```text
ANTHROPIC_API_KEY=
HF_TOKEN=
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cpu
REASON_LANGUAGE=he
```

For Supabase production metadata and artifact storage:

```text
STATE_BACKEND=supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_BUCKET=podcast-artifacts
```

Run `supabase/migrations/0001_jobs.sql` in Supabase SQL editor, or with the Supabase CLI, before
using the Supabase backend.

## API

- `POST /jobs` with `{ "url": "..." }`
- `GET /jobs/{job_id}/state`
- `GET /jobs/{job_id}/audio`
- `GET /jobs/{job_id}/review`
- `POST /jobs/{job_id}/review`
- `GET /jobs/{job_id}/output`

## Frontend

Public SEO/GEO pages:

- `/`
- `/how-it-works`
- `/faq`

Private review app:

- `/jobs/{job_id}/review`
- `/review.html`

Job-specific pages and media use `noindex` headers and are excluded from the sitemap.

## Deployment Note

Vercel is used for the public frontend and lightweight FastAPI surface. The WhisperX/pyannote/ffmpeg
pipeline is implemented in this codebase but should run in a worker environment with enough CPU/GPU,
disk, and execution time for long podcast episodes. The worker and the Vercel app can share Supabase
Postgres and Storage.
