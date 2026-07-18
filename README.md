# Get To The Point Podcast Editor

Hebrew-first podcast highlight editor. It ingests a podcast URL, transcribes and diarizes Hebrew
audio with AssemblyAI, asks Claude for guest-centered highlight candidates, lets a human approve and
tighten the final segments, then splices an MP3 in the browser with ffmpeg.wasm.

## Production Architecture

- Vercel: public pages, review UI, and FastAPI routes
- Supabase: job metadata, JSON artifacts, and output MP3 storage
- AssemblyAI: async Hebrew transcription and speaker diarization
- Anthropic Claude: highlight detection and role inference
- Browser ffmpeg.wasm: final approved-segment splice

This is the no-owned-worker path. The older Python/WhisperX worker code remains in the repo as a
fallback, but the main product flow does not require running a daemon.

## Stack

- Python 3.11+ for local development and FastAPI
- FastAPI + Vercel Python runtime
- feedparser for RSS feed resolution
- AssemblyAI for Hebrew transcription + diarization
- Anthropic Claude for highlight detection
- ffmpeg.wasm for browser-side MP3 splicing
- Static HTML, CSS, and vanilla JavaScript frontend
- Supabase Postgres + Storage

## Local Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn podcast_editor.main:app --reload
```

## Environment

Required for the no-owned-worker production flow:

```text
STATE_BACKEND=supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_BUCKET=podcast-artifacts
ASSEMBLYAI_API_KEY=
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5
REASON_LANGUAGE=he
```

Run `supabase/migrations/0001_jobs.sql` in Supabase SQL editor, or with the Supabase CLI, before
using the Supabase backend.

## Job Lifecycle

1. `POST /jobs` resolves RSS/direct audio and submits the resolved URL to AssemblyAI.
2. `/jobs/{job_id}/state` polls AssemblyAI while the job is transcribing.
3. When AssemblyAI completes, the API stores `transcript.json`, calls Claude, stores
   `highlights.json`, and marks the job `needs_review`.
4. The review UI plays the original audio, lets the human approve/reject/nudge highlights, and saves
   `review.json`.
5. The review UI loads ffmpeg.wasm, creates `output.mp3` in the browser, uploads it to
   `/jobs/{job_id}/output`, and the API marks the job `done`.

## API

- `POST /feeds/episodes` with `{ "url": "https://example.com/feed.xml" }`
- `GET /feeds?query=search` (search the saved feed library)
- `POST /jobs` with `{ "url": "..." }`
- `GET /jobs/{job_id}/state`
- `GET /jobs/{job_id}/audio`
- `GET /jobs/{job_id}/transcript`
- `GET /jobs/{job_id}/review`
- `POST /jobs/{job_id}/review`
- `POST /jobs/{job_id}/output`
- `GET /jobs/{job_id}/output`

## Frontend

Public SEO/GEO pages:

- `/`
- `/how-it-works`
- `/faq`

Private review app:

- `/jobs/{job_id}/review`
- `/review.html`

Diagnostic spike page:

- `/splice-spike.html`

Job-specific pages and media use `noindex` headers and are excluded from the sitemap.

## Fallback Worker

The Python worker remains available for a future fallback path:

```bash
pip install -r requirements-worker.txt
python -m podcast_editor.worker run
```

That path uses WhisperX/pyannote/native ffmpeg and is not required for the current no-owned-worker
architecture.
