from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .jobs import JobStore, new_job_id, validate_job_id
from .pipeline.runner import run_initial_pipeline, run_splice_pipeline
from .schemas import (
    CreateJobRequest,
    CreateJobResponse,
    JobStatus,
    ReviewRequest,
    StateResponse,
)

settings = get_settings()
store = JobStore(settings)
app = FastAPI(title="Get To The Point Podcast Editor")

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
if (PUBLIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=PUBLIC_DIR / "assets"), name="assets")


def public_file(name: str) -> FileResponse:
    path = PUBLIC_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return public_file("index.html")


@app.get("/how-it-works", include_in_schema=False)
def how_it_works() -> FileResponse:
    return public_file("how-it-works.html")


@app.get("/faq", include_in_schema=False)
def faq() -> FileResponse:
    return public_file("faq.html")


@app.get("/review.html", include_in_schema=False)
def review_html_direct() -> FileResponse:
    return public_file("review.html")


@app.get("/robots.txt", include_in_schema=False)
def robots() -> FileResponse:
    return public_file("robots.txt")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> FileResponse:
    return public_file("sitemap.xml")


@app.post("/jobs", response_model=CreateJobResponse)
def create_job(request: CreateJobRequest, background_tasks: BackgroundTasks) -> CreateJobResponse:
    job_id = new_job_id()
    store.set_status(job_id, JobStatus.queued)
    store.write_json(job_id, "input", {"source_url": request.url, "resolved_audio_url": None})
    background_tasks.add_task(run_initial_pipeline, job_id, request.url)
    return CreateJobResponse(job_id=job_id)


@app.get("/jobs/{job_id}/state", response_model=StateResponse)
def job_state(job_id: str) -> StateResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None

    status = store.get_status(job_id)
    transcript = store.read_json(job_id, "transcript")
    highlights = store.read_json(job_id, "highlights")
    return StateResponse(
        job_id=job_id,
        status=status.status,
        error=status.error,
        transcript=transcript,
        highlights=highlights,
    )


@app.get("/jobs/{job_id}/review", include_in_schema=False)
def review_html(job_id: str) -> FileResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    response = public_file("review.html")
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
        raise HTTPException(status_code=404, detail="audio not found")
    return ranged_file_response(original, request)


@app.post("/jobs/{job_id}/review")
def submit_review(
    job_id: str, review: ReviewRequest, background_tasks: BackgroundTasks
) -> JSONResponse:
    try:
        validate_job_id(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="job not found") from None
    store.write_json(job_id, "review", review.model_dump())
    store.set_status(job_id, JobStatus.splicing)
    background_tasks.add_task(run_splice_pipeline, job_id)
    return JSONResponse({"ok": True, "job_id": job_id})


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
