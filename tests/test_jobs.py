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


def test_private_feed_items_are_isolated_by_secret_token(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, state_backend="filesystem")
    store = JobStore(settings)
    first_job = new_job_id()
    second_job = new_job_id()
    first_token = "a" * 43
    second_token = "b" * 43

    store.add_private_feed_item(first_token, first_job, "First edit", 1234)
    store.add_private_feed_item(second_token, second_job, "Second edit", 5678)

    first_items = store.list_private_feed_items(first_token)
    assert first_items is not None
    assert [item["job_id"] for item in first_items] == [first_job]
    assert store.private_feed_contains(first_token, second_job) is False
    assert store.list_private_feed_items("c" * 43) is None


def test_claim_private_feed_merges_anonymous_items_into_account_feed(tmp_path: Path) -> None:
    store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    anonymous_token = "a" * 43
    account_token = "b" * 43
    shared_job = new_job_id()
    anonymous_job = new_job_id()
    store.add_private_feed_item(account_token, shared_job, "Existing account item", 100)
    store.add_private_feed_item(anonymous_token, shared_job, "Updated item", 120)
    store.add_private_feed_item(anonymous_token, anonymous_job, "Anonymous item", 200)

    claimed = store.claim_private_feed(anonymous_token, account_token, "user-1")

    assert claimed == 2
    assert store.list_private_feed_items(anonymous_token) is None
    account_items = store.list_private_feed_items(account_token)
    assert account_items is not None
    assert {item["job_id"] for item in account_items} == {shared_job, anonymous_job}
    assert store.claim_private_feed(anonymous_token, account_token, "user-1") == 0


class FakeCloud:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.downloads = 0

    def download_json_artifact(self, _job_id: str, _name: str) -> dict:
        self.downloads += 1
        return self.payload


def test_cloud_json_is_authoritative_over_warm_local_cache(tmp_path: Path) -> None:
    store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    job_id = new_job_id()
    store.artifact_path(job_id, "status").write_text(
        '{"job_id":"stale","status":"transcribing","error":null}', encoding="utf-8"
    )
    cloud = FakeCloud(
        {"job_id": job_id, "status": "needs_review", "error": None}
    )
    store.cloud = cloud  # type: ignore[assignment]

    payload = store.read_json(job_id, "status")

    assert payload["status"] == "needs_review"
    assert cloud.downloads == 1


def test_cached_transcript_only_reuses_highlights_from_requested_prompt_version(
    tmp_path: Path,
) -> None:
    class CacheCloud:
        def find_jobs_by_audio_url(self, _audio_url: str) -> list[dict]:
            return [{"id": "11111111-1111-4111-8111-111111111111"}]

        def download_json_artifact(self, _job_id: str, name: str) -> dict:
            if name == "transcript.json":
                return {"duration": 60, "segments": []}
            return {
                "selection": {"mode": "library", "prompt_version": 4},
                "highlights": [{"id": "old-fragment"}],
            }

    store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    store.cloud = CacheCloud()  # type: ignore[assignment]

    current = store.find_cached_transcript(
        "https://cdn.example.test/episode.mp3",
        {"mode": "library", "prompt_version": 5},
    )
    old = store.find_cached_transcript(
        "https://cdn.example.test/episode.mp3",
        {"mode": "library", "prompt_version": 4},
    )

    assert current is not None and current[0]["duration"] == 60
    assert current[1] is None
    assert old is not None and old[1] is not None
    assert old[1]["highlights"][0]["id"] == "old-fragment"
