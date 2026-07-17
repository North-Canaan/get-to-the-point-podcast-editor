from ..config import get_settings
from ..jobs import JobStore
from ..schemas import JobStatus
from .highlights import detect_highlights
from .ingest import ingest
from .splice import splice
from .transcribe import transcribe_and_diarize


def run_initial_pipeline(job_id: str, source_url: str) -> None:
    settings = get_settings()
    store = JobStore(settings)
    try:
        store.set_status(job_id, JobStatus.ingesting)
        ingest(job_id, source_url, store)
        store.set_status(job_id, JobStatus.transcribing)
        transcribe_and_diarize(job_id, store, settings)
        store.set_status(job_id, JobStatus.detecting_highlights)
        detect_highlights(job_id, store, settings)
        store.set_status(job_id, JobStatus.needs_review, clear_lock=True)
    except Exception as exc:
        store.set_status(job_id, JobStatus.error, error=str(exc), clear_lock=True)


def run_splice_pipeline(job_id: str) -> None:
    settings = get_settings()
    store = JobStore(settings)
    try:
        store.set_status(job_id, JobStatus.splicing)
        splice(job_id, store)
        store.set_status(job_id, JobStatus.done, clear_lock=True)
    except Exception as exc:
        store.set_status(job_id, JobStatus.error, error=str(exc), clear_lock=True)
