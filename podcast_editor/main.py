from pathlib import Path
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import re
from xml.sax.saxutils import escape

import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .auth import (
    current_user,
    optional_current_user,
    personal_feed_token,
    personal_feed_token_for_user,
)
from .jobs import JobStore, new_job_id, validate_job_id
from .email_delivery import send_private_feed_email
from .pipeline.no_worker import advance_no_worker_job, submit_no_worker_job
from .pipeline.ingest import IngestError, list_feed_episodes
from .schemas import (
    CreateJobRequest,
    CreateJobResponse,
    ClaimAnonymousFeedRequest,
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
from .security import (
    SECURITY_HEADERS,
    client_rate_key,
    enforce_same_origin,
    public_http_request,
    trusted_origins,
    validate_public_http_url,
)
from .pipeline.highlights import (
    PROMPT_VERSION,
    RetryableHighlightDetectionError,
    detect_highlights,
)

settings = get_settings()
store = JobStore(settings)
app = FastAPI(title="Get To The Point Podcast Editor")

RATE_RULES = (
    ("POST", re.compile(r"^/jobs$"), 3600, 6),
    ("POST", re.compile(r"^/feeds/episodes$"), 60, 20),
    ("POST", re.compile(r"^/jobs/[0-9a-f-]+/highlights$"), 3600, 4),
    (
        "POST",
        re.compile(r"^/jobs/[0-9a-f-]+/(?:output|output-upload-url|output-complete)$"),
        3600,
        30,
    ),
    ("POST", re.compile(r"^/jobs/[0-9a-f-]+/advance$"), 60, 30),
    ("POST", re.compile(r"^/jobs/[0-9a-f-]+/(?:review|edits|private-feed)$"), 3600, 30),
    ("POST", re.compile(r"^/jobs/[0-9a-f-]+/private-feed/email$"), 3600, 3),
    ("POST", re.compile(r"^/me/claim-anonymous-feed$"), 3600, 10),
    ("GET", re.compile(r"^/jobs/[0-9a-f-]+/state$"), 60, 30),
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if (
        request.url.path.startswith("/jobs/")
        and request.headers.get("sec-fetch-site", "").casefold() == "cross-site"
    ):
        return JSONResponse({"detail": "cross-site request blocked"}, status_code=403)
    try:
        enforce_same_origin(request, settings)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    for method, pattern, window, maximum in RATE_RULES:
        if request.method == method and pattern.fullmatch(request.url.path):
            key = f"{method}:{pattern.pattern}:{client_rate_key(request, settings)}"
            try:
                allowed = store.consume_rate_limit(key, window, maximum)
            except httpx.HTTPError:
                allowed = False
            if not allowed:
                response = JSONResponse(
                    {"detail": "Too many requests. Please try again later."}, status_code=429
                )
                response.headers["Retry-After"] = str(window)
                break
    else:
        response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith(("/jobs/", "/me", "/private-feed/")):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
if (PUBLIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=PUBLIC_DIR / "assets"), name="assets")


def mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return email
    visible = local[:1]
    return f"{visible}{'•' * max(3, len(local) - 1)}@{domain}"


def canonical_base_url() -> str:
    configured = settings.app_base_url.rstrip("/")
    if not configured.startswith(("http://localhost", "https://localhost")):
        return configured
    production_origins = sorted(
        origin for origin in trusted_origins(settings) if origin.startswith("https://")
    )
    if production_origins:
        return production_origins[0]
    return configured


def authorize_job_access(request: Request, job_id: str) -> dict:
    record = store.get_job_record(job_id) or {}
    if store.cloud and not record:
        raise HTTPException(status_code=404, detail="job not found")
    owner_id = record.get("user_id")
    if owner_id:
        user = current_user(request, settings)
        if str(user.get("id")) != str(owner_id):
            raise HTTPException(status_code=404, detail="job not found")
    return record


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
    validate_public_http_url(payload.url)
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
        raise HTTPException(
            status_code=502, detail=f"Could not start episode processing: {exc}"
        ) from exc
    return CreateJobResponse(job_id=job_id)


@app.post("/feeds/episodes", response_model=FeedEpisodesResponse)
def feed_episodes(request: FeedRequest) -> FeedEpisodesResponse:
    validate_public_http_url(request.url)
    try:
        result = FeedEpisodesResponse.model_validate(list_feed_episodes(request.url))
        store.save_feed(request.url, result.title, len(result.episodes))
        return result
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/feeds", response_model=FeedLibraryResponse)
def feeds(query: str = Query(default="", max_length=100)) -> FeedLibraryResponse:
    return FeedLibraryResponse(feeds=store.list_feeds(query))


@app.get("/jobs/{job_id}/state", response_model=StateResponse)
def job_state(job_id: str, request: Request) -> StateResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None

    job_record = authorize_job_access(request, job_id)
    status = store.get_status(job_id)
    if status.status == JobStatus.splicing:
        try:
            output_size = store.artifact_size(job_id, "output")
        except httpx.HTTPError:
            output_size = None
        if output_size:
            store.set_status(
                job_id,
                JobStatus.done,
                extra={
                    "output_storage_path": f"{job_id}/output.mp3",
                    "output_size_bytes": output_size,
                },
            )
            status = store.get_status(job_id)
            job_record = store.get_job_record(job_id) or job_record
        elif job_status_is_stale(job_record.get("updated_at"), minutes=15):
            store.set_status(job_id, JobStatus.needs_review)
            status = store.get_status(job_id)
            job_record = store.get_job_record(job_id) or job_record
    transcript = None
    highlights = None
    if status.status in {
        JobStatus.needs_review,
        JobStatus.splicing,
        JobStatus.done,
        JobStatus.error,
    }:
        transcript = store.read_json(job_id, "transcript")
    if status.status in {
        JobStatus.needs_review,
        JobStatus.splicing,
        JobStatus.done,
        JobStatus.error,
    }:
        highlights = store.read_json(job_id, "highlights")
    input_payload = store.read_json(job_id, "input") or {}
    return StateResponse(
        job_id=job_id,
        status=status.status,
        error=status.error,
        created_at=job_record.get("created_at"),
        status_updated_at=job_record.get("updated_at"),
        email_delivery_available=bool(settings.resend_api_key),
        episode_title=input_payload.get("episode_title"),
        transcript=transcript,
        highlights=highlights,
    )


def job_status_is_stale(updated_at: object, minutes: int) -> bool:
    if not updated_at:
        return False
    try:
        value = str(updated_at).replace("Z", "+00:00")
        updated = datetime.fromisoformat(value)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - updated > timedelta(minutes=minutes)


@app.post("/jobs/{job_id}/advance")
def advance_job(job_id: str, request: Request) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    job_record = authorize_job_access(request, job_id)
    status = store.get_status(job_id)
    if status.status not in {JobStatus.transcribing, JobStatus.detecting_highlights}:
        return JSONResponse({"started": False, "status": status.status.value})

    worker_id = f"web-{new_job_id()}"
    if store.cloud:
        locked_at = job_record.get("locked_at")
        if job_record.get("worker_id") and not job_status_is_stale(locked_at, minutes=6):
            return JSONResponse(
                {"started": False, "status": status.status.value}, status_code=202
            )
        if job_record.get("worker_id"):
            store.cloud.release_job(job_id, str(job_record["worker_id"]))
        if not store.cloud.claim_job(job_id, status.status, worker_id):
            return JSONResponse(
                {"started": False, "status": status.status.value}, status_code=202
            )

    try:
        advance_no_worker_job(job_id, store, settings)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="processing provider is unavailable") from exc
    finally:
        if store.cloud:
            store.cloud.release_job(job_id, worker_id)

    current = store.get_status(job_id)
    return JSONResponse({"started": True, "status": current.status.value})


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
    authorize_job_access(request, job_id)
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


@app.get("/jobs/{job_id}/audio-metadata")
def job_audio_metadata(job_id: str, request: Request) -> JSONResponse:
    authorize_job_access(request, job_id)
    input_payload = store.read_json(job_id, "input") or {}
    transcript = store.read_json(job_id, "transcript") or {}
    audio_url = input_payload.get("resolved_audio_url")
    if not audio_url:
        raise HTTPException(status_code=404, detail="audio not found")
    try:
        response = public_http_request(
            "GET", str(audio_url), headers={"Range": "bytes=0-0"}, max_bytes=1024
        )
    except (httpx.HTTPError, HTTPException) as exc:
        raise HTTPException(status_code=502, detail="could not inspect source audio") from exc
    content_range = response.headers.get("content-range", "")
    match = re.search(r"/(\d+)$", content_range)
    if not match:
        raise HTTPException(status_code=502, detail="source audio does not support ranges")
    return JSONResponse(
        {
            "size_bytes": int(match.group(1)),
            "duration": float(transcript.get("duration") or 0),
        }
    )


@app.get("/jobs/{job_id}/audio-range")
def job_audio_range(
    job_id: str,
    request: Request,
    start: int = Query(ge=0),
    end: int = Query(ge=0),
) -> Response:
    authorize_job_access(request, job_id)
    if end < start or end - start + 1 > 4_000_000:
        raise HTTPException(status_code=422, detail="invalid audio range")
    input_payload = store.read_json(job_id, "input") or {}
    audio_url = input_payload.get("resolved_audio_url")
    if not audio_url:
        raise HTTPException(status_code=404, detail="audio not found")
    try:
        upstream = public_http_request(
            "GET",
            str(audio_url),
            headers={"Range": f"bytes={start}-{end}"},
            max_bytes=4_000_000,
        )
    except (httpx.HTTPError, HTTPException) as exc:
        raise HTTPException(status_code=502, detail="could not fetch audio range") from exc
    if upstream.status_code != 206:
        raise HTTPException(status_code=502, detail="source audio does not support ranges")
    return Response(
        content=upstream.content,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/jobs/{job_id}/review")
def submit_review(job_id: str, review: ReviewRequest, request: Request) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    authorize_job_access(request, job_id)
    store.write_json(job_id, "review", review.model_dump())
    store.set_status(job_id, JobStatus.splicing)
    return JSONResponse({"ok": True, "job_id": job_id})


@app.post("/jobs/{job_id}/edits", response_model=CreateJobResponse)
def create_additional_edit(job_id: str, request: Request) -> CreateJobResponse:
    job_record = authorize_job_access(request, job_id)
    input_payload = store.read_json(job_id, "input")
    transcript = store.read_json(job_id, "transcript")
    highlights = store.read_json(job_id, "highlights")
    if not input_payload or not transcript or not highlights:
        raise HTTPException(status_code=409, detail="episode is not ready for another edit")

    new_id = new_job_id()
    cloned_input = {**input_payload, "derived_from_job_id": job_id}
    store.write_json(new_id, "input", cloned_input)
    store.write_json(new_id, "transcript", transcript)
    store.write_json(new_id, "highlights", highlights)
    extra = {
        key: value
        for key, value in {
            "user_id": job_record.get("user_id"),
            "episode_title": cloned_input.get("episode_title"),
        }.items()
        if value is not None
    }
    store.set_status(
        new_id,
        JobStatus.needs_review,
        source_url=cloned_input.get("source_url") or job_record.get("source_url"),
        extra=extra or None,
    )
    return CreateJobResponse(job_id=new_id)


@app.post("/jobs/{job_id}/highlights")
def select_highlights(
    job_id: str, payload: HighlightSelectionRequest, request: Request
) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    authorize_job_access(request, job_id)
    if not store.read_json(job_id, "transcript"):
        raise HTTPException(status_code=409, detail="transcript is not ready")
    selection = {"mode": "library", "prompt_version": PROMPT_VERSION}
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
        )
    except RetryableHighlightDetectionError:
        return JSONResponse(
            {"started": True, "status": JobStatus.detecting_highlights.value}, status_code=202
        )
    except Exception as exc:
        store.set_status(job_id, JobStatus.error, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Could not select highlights: {exc}") from exc
    store.set_status(job_id, JobStatus.needs_review)
    return JSONResponse(highlights)


@app.post("/jobs/{job_id}/output")
async def upload_output(
    job_id: str, request: Request, file: UploadFile = File(...)
) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    authorize_job_access(request, job_id)
    if file.content_type not in {"audio/mpeg", "audio/mp3", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="MP3 audio is required")
    content = await file.read(settings.max_output_bytes + 1)
    if len(content) > settings.max_output_bytes:
        raise HTTPException(status_code=413, detail="output is too large")
    output = store.artifact_path(job_id, "output")
    output.write_bytes(content)
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
def output_upload_url(job_id: str, request: Request) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    authorize_job_access(request, job_id)
    signed_url = store.signed_media_upload_url(job_id, "output.mp3")
    if not signed_url:
        raise HTTPException(status_code=501, detail="direct upload is unavailable")
    return JSONResponse({"upload_url": signed_url})


@app.post("/jobs/{job_id}/output-complete")
def complete_output(job_id: str, payload: CompleteOutputRequest, request: Request) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    authorize_job_access(request, job_id)
    actual_size = store.artifact_size(job_id, "output")
    if actual_size is None:
        raise HTTPException(status_code=409, detail="uploaded output was not found")
    if actual_size > settings.max_output_bytes:
        store.delete_media(job_id, "output.mp3")
        raise HTTPException(status_code=413, detail="uploaded output is too large")
    if actual_size != payload.size_bytes:
        store.delete_media(job_id, "output.mp3")
        raise HTTPException(status_code=409, detail="uploaded output size does not match")
    store.set_status(
        job_id,
        JobStatus.done,
        extra={
            "output_storage_path": f"{job_id}/output.mp3",
            "output_size_bytes": actual_size,
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
    authorize_job_access(request, job_id)
    status = store.get_status(job_id)
    if status.status != JobStatus.done:
        raise HTTPException(status_code=409, detail="edited episode is not ready")
    input_payload = store.read_json(job_id, "input") or {}
    job_record = store.get_job_record(job_id) or {}
    output = store.artifact_path(job_id, "output")
    size_bytes = (
        output.stat().st_size if output.exists() else int(job_record.get("output_size_bytes") or 0)
    )
    title = str(input_payload.get("episode_title") or "Edited episode")
    user = optional_current_user(request, settings)
    token = personal_feed_token_for_user(user, settings) if user else payload.token
    store.add_private_feed_item(token, job_id, title, size_bytes, user["id"] if user else None)
    return JSONResponse({"ok": True, "feed_url": f"/private-feed/{token}.xml"})


@app.post("/jobs/{job_id}/private-feed/email")
def email_private_feed(job_id: str, request: Request) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    user = current_user(request, settings)
    email = str(user.get("email") or "")
    if not email:
        raise HTTPException(status_code=422, detail="Your account does not have an email address")
    authorize_job_access(request, job_id)
    status = store.get_status(job_id)
    if status.status != JobStatus.done:
        raise HTTPException(status_code=409, detail="edited episode is not ready")

    token = personal_feed_token_for_user(user, settings)
    input_payload = store.read_json(job_id, "input") or {}
    job_record = store.get_job_record(job_id) or {}
    output = store.artifact_path(job_id, "output")
    size_bytes = (
        output.stat().st_size if output.exists() else int(job_record.get("output_size_bytes") or 0)
    )
    title = str(input_payload.get("episode_title") or "Edited episode")
    store.add_private_feed_item(token, job_id, title, size_bytes, user["id"])
    feed_url = f"{canonical_base_url()}/private-feed/{token}.xml"
    try:
        send_private_feed_email(email, feed_url, settings)
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "email": mask_email(email)})


@app.get("/me")
def me(request: Request) -> JSONResponse:
    user = current_user(request, settings)
    token = personal_feed_token_for_user(user, settings)
    derived_token = personal_feed_token(str(user["id"]), settings)
    if token != derived_token:
        store.claim_private_feed(derived_token, token, str(user["id"]))
    return JSONResponse(
        {
            "user": {"id": user["id"], "name": user.get("name"), "email": user.get("email")},
            "feed_url": f"/private-feed/{token}.xml",
            "jobs": store.list_user_jobs(user["id"]),
        }
    )


@app.post("/me/claim-anonymous-feed")
def claim_anonymous_feed(payload: ClaimAnonymousFeedRequest, request: Request) -> JSONResponse:
    user = current_user(request, settings)
    account_token = personal_feed_token_for_user(user, settings)
    claimed = store.claim_private_feed(payload.token, account_token, str(user["id"]))
    return JSONResponse(
        {
            "ok": True,
            "claimed_episodes": claimed,
            "feed_url": f"/private-feed/{account_token}.xml",
        }
    )


@app.get("/private-feed/{token}.xml")
def private_feed(token: str, request: Request) -> Response:
    validate_private_feed_token(token)
    items = store.list_private_feed_items(token)
    if items is None:
        raise HTTPException(status_code=404, detail="private feed not found")
    base_url = canonical_base_url()
    feed_url = f"{base_url}/private-feed/{token}.xml"
    item_xml = []
    for item in items:
        job_id = str(item["job_id"])
        published = parse_feed_datetime(str(item.get("published_at") or ""))
        revision = re.sub(r"[^0-9]", "", str(item.get("updated_at") or ""))
        guid = f"{job_id}-{revision}" if revision else job_id
        enclosure_url = f"{base_url}/private-feed/{token}/episodes/{job_id}.mp3"
        item_xml.append(
            "<item>"
            f"<title>{escape(str(item.get('title') or 'Edited episode'))}</title>"
            f'<guid isPermaLink="false">{escape(guid)}</guid>'
            f"<pubDate>{format_datetime(published)}</pubDate>"
            f'<enclosure url="{escape(enclosure_url)}" '
            f'length="{int(item.get("size_bytes") or 0)}" type="audio/mpeg"/>'
            "</item>"
        )
    now = format_datetime(datetime.now(timezone.utc))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        "<title>My Edited Episodes</title>"
        "<description>Private edited podcast episodes from Get To The Point.</description>"
        f"<link>{escape(feed_url)}</link><lastBuildDate>{now}</lastBuildDate>"
        f"{''.join(item_xml)}"
        "</channel></rss>"
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
    return _job_output_response(job_id)


@app.head("/private-feed/{token}/episodes/{job_id}.mp3")
def private_feed_episode_head(token: str, job_id: str) -> Response:
    validate_private_feed_token(token)
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="episode not found") from None
    if not store.private_feed_contains(token, job_id):
        raise HTTPException(status_code=404, detail="episode not found")
    return _job_output_response(job_id, head_only=True)


def _job_output_response(job_id: str, head_only: bool = False) -> Response:
    output = store.artifact_path(job_id, "output")
    if not output.exists():
        signed_url = store.signed_media_url(job_id, "output.mp3")
        if signed_url:
            return RedirectResponse(signed_url, headers={"Content-Type": "audio/mpeg"})
        raise HTTPException(status_code=404, detail="output not found")
    if head_only:
        return Response(
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(output.stat().st_size),
                "Content-Type": "audio/mpeg",
                "X-Robots-Tag": "noindex, nofollow",
            }
        )
    return FileResponse(
        output,
        media_type="audio/mpeg",
        filename="output.mp3",
        headers={"X-Robots-Tag": "noindex, nofollow"},
    )


@app.get("/jobs/{job_id}/output")
def job_output(job_id: str, request: Request) -> Response:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="output not found") from None
    authorize_job_access(request, job_id)
    return _job_output_response(job_id)


@app.head("/jobs/{job_id}/output")
def job_output_head(job_id: str, request: Request) -> Response:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="output not found") from None
    authorize_job_access(request, job_id)
    return _job_output_response(job_id, head_only=True)


def validate_private_feed_token(token: str) -> str:
    if not 32 <= len(token) <= 256 or not all(char.isalnum() or char in "_-" for char in token):
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
