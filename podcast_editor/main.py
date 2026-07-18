from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .auth import current_user, optional_current_user, personal_feed_token
from .jobs import JobStore, new_job_id, validate_job_id
from .pipeline.no_worker import advance_no_worker_job, submit_no_worker_job
from .pipeline.ingest import IngestError, list_feed_episodes
from .schemas import (
    CreateJobRequest,
    CreateJobResponse,
    CompleteOutputRequest,
    FeedEpisodesResponse,
    FeedLibraryResponse,
    FeedRequest,
    HighlightSelectionRequest,
    JobStatus,
    PrivateFeedRequest,
    ReviewRequest,
    StateResponse,
)
from .pipeline.highlights import PROMPT_VERSION, detect_highlights

settings = get_settings()
store = JobStore(settings)
app = FastAPI(title="Get To The Point Podcast Editor")

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
if (PUBLIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=PUBLIC_DIR / "assets"), name="assets")

INDEX_FALLBACK_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Get To The Point | Podcast Highlight Editor</title>
    <meta name="description" content="Turn long podcast episodes into concise, human-selected edited episodes with transcription, speaker detection, and AI-assisted highlight discovery." />
    <link rel="canonical" href="https://get-to-the-point-podcast-editor.vercel.app/" />
    <meta property="og:title" content="Get To The Point Podcast Editor" />
    <meta property="og:description" content="A podcast highlight editor for turning long episodes into concise edited audio cuts." />
    <meta property="og:type" content="website" />
    <meta property="og:url" content="https://get-to-the-point-podcast-editor.vercel.app/" />
    <meta name="twitter:card" content="summary_large_image" />
    <link rel="stylesheet" href="/assets/styles.css" />
    <script type="application/ld+json">{"@context":"https://schema.org","@type":"SoftwareApplication","name":"Get To The Point Podcast Editor","applicationCategory":"MultimediaApplication","operatingSystem":"Web","description":"Podcast highlight editor that transcribes episodes, suggests high-signal moments, and produces a human-selected audio edit.","offers":{"@type":"Offer","price":"0","priceCurrency":"USD"}}</script>
  </head>
  <body>
    <header class="site-header"><nav class="nav" aria-label="Primary"><a class="brand" href="/">Get To The Point</a><div class="nav-links"><a href="/how-it-works">How it works</a><a href="/faq">FAQ</a><a href="/review.html">Review app</a><a class="account-cta" data-auth-link href="/auth.html">Sign in to save progress</a></div></nav></header>
    <main>
      <section class="hero">
        <div>
          <h1>Podcast Highlight Editor</h1>
          <p>Get To The Point turns long podcast episodes into concise, high-signal highlight cuts. The system automates transcription, diarization, and candidate discovery, while a human editor makes the final decisions.</p>
          <div class="actions"><a class="button" href="/review.html">Open review app</a><a class="button secondary" href="/how-it-works">See the pipeline</a></div>
        </div>
        <div class="waveform" aria-label="Audio editing waveform illustration"><div class="wave-bars" aria-hidden="true"><span style="height:34%"></span><span style="height:62%"></span><span style="height:88%"></span><span style="height:52%"></span><span style="height:74%"></span><span style="height:42%"></span><span style="height:96%"></span><span style="height:58%"></span><span style="height:46%"></span><span style="height:82%"></span><span style="height:66%"></span><span style="height:36%"></span></div></div>
      </section>
      <section><div class="section-inner"><h2>Turn long episodes into the moments that matter</h2><div class="grid"><article class="card"><h3>Timestamped transcription</h3><p>AssemblyAI produces transcripts with speaker diarization and timestamps for accurate review and editing.</p></article><article class="card"><h3>Speaker-aware highlights</h3><p>Diarization keeps speakers separate, and Claude infers host and guest roles from conversational behavior rather than hardcoded labels.</p></article><article class="card"><h3>Your selection</h3><p>Select the moments you want, preview them, and create an edited episode directly from the original audio.</p></article></div></div></section>
    </main>
    <footer class="site-footer">Podcast editing software for finding and sharing the moments that matter.</footer>
  </body>
</html>"""


def public_file(name: str) -> FileResponse:
    path = PUBLIC_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)


@app.get("/", include_in_schema=False)
def home() -> Response:
    path = PUBLIC_DIR / "index.html"
    if path.exists():
        return FileResponse(path)
    return RedirectResponse("/index.html", status_code=307)


@app.get("/how-it-works", include_in_schema=False)
def how_it_works() -> FileResponse:
    return public_file("how-it-works.html")


@app.get("/faq", include_in_schema=False)
def faq() -> FileResponse:
    return public_file("faq.html")


@app.get("/review.html", include_in_schema=False)
def review_html_direct() -> FileResponse:
    return public_file("review.html")


@app.get("/auth.html", include_in_schema=False)
def auth_html() -> FileResponse:
    return public_file("auth.html")


@app.get("/account.html", include_in_schema=False)
def account_html() -> FileResponse:
    return public_file("account.html")


@app.get("/robots.txt", include_in_schema=False)
def robots() -> FileResponse:
    return public_file("robots.txt")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> FileResponse:
    return public_file("sitemap.xml")


@app.post("/jobs", response_model=CreateJobResponse)
def create_job(payload: CreateJobRequest, request: Request) -> CreateJobResponse:
    user = optional_current_user(request, settings)
    job_id = new_job_id()
    store.set_status(
        job_id,
        JobStatus.queued,
        source_url=payload.url,
        extra={"user_id": user["id"], "episode_title": payload.title} if user else None,
    )
    try:
        submit_no_worker_job(job_id, payload.url, store, settings, payload.title, payload.language)
    except Exception as exc:
        store.set_status(job_id, JobStatus.error, error=str(exc), source_url=payload.url)
        raise HTTPException(status_code=502, detail=f"Could not start episode processing: {exc}") from exc
    return CreateJobResponse(job_id=job_id)


@app.post("/feeds/episodes", response_model=FeedEpisodesResponse)
def feed_episodes(request: FeedRequest) -> FeedEpisodesResponse:
    try:
        result = FeedEpisodesResponse.model_validate(list_feed_episodes(request.url))
        store.save_feed(request.url, result.title, len(result.episodes))
        return result
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/feeds", response_model=FeedLibraryResponse)
def feeds(query: str = "") -> FeedLibraryResponse:
    return FeedLibraryResponse(feeds=store.list_feeds(query))


@app.get("/jobs/{job_id}/state", response_model=StateResponse)
def job_state(job_id: str) -> StateResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None

    advance_no_worker_job(job_id, store, settings)
    status = store.get_status(job_id)
    transcript = None
    highlights = None
    if status.status not in {JobStatus.queued, JobStatus.ingesting, JobStatus.transcribing}:
        transcript = store.read_json(job_id, "transcript")
    if status.status in {JobStatus.needs_review, JobStatus.splicing, JobStatus.done, JobStatus.error}:
        highlights = store.read_json(job_id, "highlights")
    input_payload = store.read_json(job_id, "input") or {}
    return StateResponse(
        job_id=job_id,
        status=status.status,
        error=status.error,
        episode_title=input_payload.get("episode_title"),
        transcript=transcript,
        highlights=highlights,
    )


@app.get("/jobs/{job_id}/review", include_in_schema=False)
def review_html(job_id: str) -> RedirectResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    response = RedirectResponse(f"/review.html?job_id={job_id}", status_code=307)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.get("/jobs/{job_id}/audio")
def job_audio(job_id: str, request: Request) -> Response:
    original = store.original_path(job_id)
    if not original or not original.exists():
        input_payload = store.read_json(job_id, "input") or {}
        filename = input_payload.get("original_filename")
        if filename:
            signed_url = store.signed_media_url(job_id, filename)
            if signed_url:
                return RedirectResponse(signed_url)
        resolved_url = input_payload.get("resolved_audio_url")
        if resolved_url:
            response = RedirectResponse(str(resolved_url))
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            return response
        raise HTTPException(status_code=404, detail="audio not found")
    return ranged_file_response(original, request)


@app.post("/jobs/{job_id}/review")
def submit_review(job_id: str, review: ReviewRequest) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    store.write_json(job_id, "review", review.model_dump())
    store.set_status(job_id, JobStatus.splicing)
    return JSONResponse({"ok": True, "job_id": job_id})


@app.post("/jobs/{job_id}/highlights")
def select_highlights(job_id: str, request: HighlightSelectionRequest) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    if not store.read_json(job_id, "transcript"):
        raise HTTPException(status_code=409, detail="transcript is not ready")
    selection = {
        "topic": request.topic,
        "target_minutes": request.target_minutes,
        "prompt_version": PROMPT_VERSION,
    }
    input_payload = store.read_json(job_id, "input") or {}
    audio_url = input_payload.get("resolved_audio_url")
    cached = store.find_cached_highlights(str(audio_url), selection) if audio_url else None
    if cached:
        store.write_json(job_id, "highlights", cached)
        store.set_status(job_id, JobStatus.needs_review)
        return JSONResponse(cached)
    store.set_status(job_id, JobStatus.detecting_highlights)
    try:
        highlights = detect_highlights(
            job_id,
            store,
            settings,
            topic=request.topic,
            target_minutes=request.target_minutes,
        )
    except Exception as exc:
        store.set_status(job_id, JobStatus.error, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Could not select highlights: {exc}") from exc
    store.set_status(job_id, JobStatus.needs_review)
    return JSONResponse(highlights)


@app.post("/jobs/{job_id}/output")
async def upload_output(job_id: str, file: UploadFile = File(...)) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    output = store.artifact_path(job_id, "output")
    output.write_bytes(await file.read())
    store.upload_media(job_id, output, content_type="audio/mpeg")
    store.set_status(
        job_id,
        JobStatus.done,
        extra={
            "output_storage_path": f"{job_id}/output.mp3",
            "output_size_bytes": output.stat().st_size,
        },
    )
    return JSONResponse({"ok": True, "job_id": job_id})


@app.post("/jobs/{job_id}/output-upload-url")
def output_upload_url(job_id: str) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    signed_url = store.signed_media_upload_url(job_id, "output.mp3")
    if not signed_url:
        raise HTTPException(status_code=501, detail="direct upload is unavailable")
    return JSONResponse({"upload_url": signed_url})


@app.post("/jobs/{job_id}/output-complete")
def complete_output(job_id: str, request: CompleteOutputRequest) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    store.set_status(
        job_id,
        JobStatus.done,
        extra={
            "output_storage_path": f"{job_id}/output.mp3",
            "output_size_bytes": request.size_bytes,
        },
    )
    return JSONResponse({"ok": True, "job_id": job_id})


@app.post("/jobs/{job_id}/private-feed")
def add_job_to_private_feed(
    job_id: str, payload: PrivateFeedRequest, request: Request
) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    status = store.get_status(job_id)
    if status.status != JobStatus.done:
        raise HTTPException(status_code=409, detail="edited episode is not ready")
    input_payload = store.read_json(job_id, "input") or {}
    job_record = store.get_job_record(job_id) or {}
    output = store.artifact_path(job_id, "output")
    size_bytes = output.stat().st_size if output.exists() else int(job_record.get("output_size_bytes") or 0)
    title = str(input_payload.get("episode_title") or "Edited episode")
    user = optional_current_user(request, settings)
    token = personal_feed_token(user["id"], settings) if user else payload.token
    store.add_private_feed_item(token, job_id, title, size_bytes, user["id"] if user else None)
    return JSONResponse({"ok": True, "feed_url": f"/private-feed/{token}.xml"})


@app.get("/me")
def me(request: Request) -> JSONResponse:
    user = current_user(request, settings)
    token = personal_feed_token(user["id"], settings)
    return JSONResponse(
        {
            "user": {"id": user["id"], "name": user.get("name"), "email": user.get("email")},
            "feed_url": f"/private-feed/{token}.xml",
            "jobs": store.list_user_jobs(user["id"]),
        }
    )


@app.get("/private-feed/{token}.xml")
def private_feed(token: str, request: Request) -> Response:
    validate_private_feed_token(token)
    items = store.list_private_feed_items(token)
    if items is None:
        raise HTTPException(status_code=404, detail="private feed not found")
    base_url = str(request.base_url).rstrip("/")
    feed_url = f"{base_url}/private-feed/{token}.xml"
    item_xml = []
    for item in items:
        job_id = str(item["job_id"])
        published = parse_feed_datetime(str(item.get("published_at") or ""))
        enclosure_url = f"{base_url}/private-feed/{token}/episodes/{job_id}.mp3"
        item_xml.append(
            "<item>"
            f"<title>{escape(str(item.get('title') or 'Edited episode'))}</title>"
            f"<guid isPermaLink=\"false\">{escape(job_id)}</guid>"
            f"<pubDate>{format_datetime(published)}</pubDate>"
            f"<enclosure url=\"{escape(enclosure_url)}\" "
            f"length=\"{int(item.get('size_bytes') or 0)}\" type=\"audio/mpeg\"/>"
            "</item>"
        )
    now = format_datetime(datetime.now(timezone.utc))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        '<title>My Edited Episodes</title>'
        '<description>Private edited podcast episodes from Get To The Point.</description>'
        f'<link>{escape(feed_url)}</link><lastBuildDate>{now}</lastBuildDate>'
        f"{''.join(item_xml)}"
        '</channel></rss>'
    )
    return Response(
        xml,
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


@app.get("/private-feed/{token}/episodes/{job_id}.mp3")
def private_feed_episode(token: str, job_id: str) -> Response:
    validate_private_feed_token(token)
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="episode not found") from None
    if not store.private_feed_contains(token, job_id):
        raise HTTPException(status_code=404, detail="episode not found")
    return job_output(job_id)


@app.get("/jobs/{job_id}/output")
def job_output(job_id: str) -> FileResponse:
    output = store.artifact_path(job_id, "output")
    if not output.exists():
        signed_url = store.signed_media_url(job_id, "output.mp3")
        if signed_url:
            return RedirectResponse(signed_url)
        raise HTTPException(status_code=404, detail="output not found")
    return FileResponse(
        output,
        media_type="audio/mpeg",
        filename="output.mp3",
        headers={"X-Robots-Tag": "noindex, nofollow"},
    )


def validate_private_feed_token(token: str) -> str:
    if not 32 <= len(token) <= 256 or not all(
        char.isalnum() or char in "_-" for char in token
    ):
        raise HTTPException(status_code=404, detail="private feed not found")
    return token


def parse_feed_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ranged_file_response(path: Path, request: Request) -> Response:
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    headers = {
        "Accept-Ranges": "bytes",
        "X-Robots-Tag": "noindex, nofollow",
    }

    if not range_header:
        return FileResponse(path, media_type="audio/mpeg", headers=headers)

    unit, _, range_value = range_header.partition("=")
    if unit != "bytes":
        return Response(status_code=416, headers=headers)
    start_text, _, end_text = range_value.partition("-")
    try:
        start = int(start_text) if start_text else 0
        end = int(end_text) if end_text else file_size - 1
    except ValueError:
        return Response(status_code=416, headers=headers)

    start = max(0, start)
    end = min(file_size - 1, end)
    if start > end:
        return Response(status_code=416, headers=headers)

    chunk_size = end - start + 1

    def iter_file():
        with path.open("rb") as file:
            file.seek(start)
            remaining = chunk_size
            while remaining > 0:
                chunk = file.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers.update(
        {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(chunk_size),
        }
    )
    return StreamingResponse(iter_file(), status_code=206, media_type="audio/mpeg", headers=headers)
