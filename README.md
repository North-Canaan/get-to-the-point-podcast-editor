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

## Running Workers

Vercel serves the public pages, review UI, and lightweight API. It should not run long WhisperX,
pyannote, and ffmpeg jobs. In production, `POST /jobs` writes the job to Supabase and returns a
`job_id`; a separate Python 3.11 worker polls Supabase and runs the heavy stages.

Set these environment variables on the worker machine:

```text
STATE_BACKEND=supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_BUCKET=podcast-artifacts
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5
HF_TOKEN=
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
REASON_LANGUAGE=he
DATA_DIR=data
```

Install and run directly:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-worker.txt
python -m podcast_editor.worker run
```

Run one poll and exit:

```bash
python -m podcast_editor.worker run --once
```

Process a known local/filesystem job manually:

```bash
python -m podcast_editor.worker process <job_id> --stage initial
python -m podcast_editor.worker process <job_id> --stage splice
```

Build a worker container:

```bash
docker build -f Dockerfile.worker -t podcast-editor-worker .
docker run --env-file .env podcast-editor-worker
```

For GPU transcription, run the container on a host with NVIDIA runtime support and set
`WHISPER_DEVICE=cuda`.

The worker lifecycle is:

1. Claim `queued` jobs from Supabase.
2. Download/resolve the episode, transcribe, diarize, detect highlights.
3. Upload artifacts to Supabase Storage and mark the job `needs_review`.
4. After the web UI writes `review.json` and marks the job `splicing`, claim the job again.
5. Download the original audio if needed, splice `output.mp3`, upload it, and mark the job `done`.

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

Vercel is used for the public frontend, review UI, and lightweight FastAPI surface. The
WhisperX/pyannote/ffmpeg pipeline runs through `python -m podcast_editor.worker run` in an external
worker environment with enough CPU/GPU, disk, and execution time for long podcast episodes. The
worker and the Vercel app share Supabase Postgres and Storage.
