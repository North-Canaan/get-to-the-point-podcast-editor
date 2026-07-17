from pathlib import Path

from podcast_editor.config import Settings
from podcast_editor.jobs import JobStore, new_job_id
from podcast_editor.schemas import JobStatus


def test_job_store_writes_and_reads_json(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, state_backend="filesystem")
    store = JobStore(settings)
    job_id = new_job_id()

    store.write_json(job_id, "input", {"source_url": "https://example.com/audio.mp3"})

    assert store.read_json(job_id, "input") == {"source_url": "https://example.com/audio.mp3"}


def test_job_store_status_round_trip(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, state_backend="filesystem")
    store = JobStore(settings)
    job_id = new_job_id()

    store.set_status(job_id, JobStatus.transcribing)

    assert store.get_status(job_id).status == JobStatus.transcribing
