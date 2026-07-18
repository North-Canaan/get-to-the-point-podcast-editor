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


def test_feed_library_upserts_and_searches(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, state_backend="filesystem")
    store = JobStore(settings)

    store.save_feed("https://example.com/feed.xml", "A Great Podcast", 12)
    store.save_feed("https://example.com/feed.xml", "A Great Podcast", 13)
    store.save_feed("https://other.example/rss", "Another Show", 4)

    assert len(store.list_feeds()) == 2
    result = store.list_feeds("great")
    assert len(result) == 1
    assert result[0]["episode_count"] == 13
