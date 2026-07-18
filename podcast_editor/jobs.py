import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .cloud import SupabaseClient
from .config import Settings, get_settings
from .schemas import JobStatus, StatusRecord


ARTIFACT_NAMES = {
    "input": "input.json",
    "audio16k": "audio16k.wav",
    "transcript": "transcript.json",
    "highlights": "highlights.json",
    "review": "review.json",
    "output": "output.mp3",
    "status": "status.json",
}


def new_job_id() -> str:
    return str(uuid4())


def validate_job_id(job_id: str) -> str:
    try:
        return str(UUID(job_id))
    except ValueError as exc:
        raise ValueError("invalid job id") from exc


class JobStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.cloud = SupabaseClient.from_settings(self.settings)

    def job_dir(self, job_id: str) -> Path:
        valid_id = validate_job_id(job_id)
        path = self.settings.data_dir / valid_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def artifact_path(self, job_id: str, name: str) -> Path:
        return self.job_dir(job_id) / ARTIFACT_NAMES[name]

    def original_path(self, job_id: str) -> Path | None:
        job_dir = self.job_dir(job_id)
        matches = sorted(job_dir.glob("original.*"))
        return matches[0] if matches else None

    def temp_dir(self, job_id: str) -> Path:
        path = self.job_dir(job_id) / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, job_id: str, name: str, payload: dict) -> Path:
        path = self.artifact_path(job_id, name)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.cloud:
            self.cloud.upload_artifact(
                job_id,
                ARTIFACT_NAMES[name],
                path,
                content_type="application/json; charset=utf-8",
            )
        return path

    def read_json(self, job_id: str, name: str) -> dict | None:
        path = self.artifact_path(job_id, name)
        if not path.exists():
            if self.cloud:
                return self.cloud.download_json_artifact(job_id, ARTIFACT_NAMES[name])
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def set_status(
        self,
        job_id: str,
        status: JobStatus,
        error: str | None = None,
        source_url: str | None = None,
        clear_lock: bool = False,
        extra: dict | None = None,
    ) -> StatusRecord:
        record = StatusRecord(job_id=job_id, status=status, error=error)
        self.write_json(job_id, "status", record.model_dump())
        if self.cloud:
            self.cloud.upsert_job(
                job_id,
                status,
                error,
                source_url=source_url,
                clear_lock=clear_lock,
                extra=extra,
            )
        return record

    def upload_media(self, job_id: str, path: Path, content_type: str) -> None:
        if self.cloud:
            self.cloud.upload_artifact(job_id, path.name, path, content_type)

    def signed_media_url(self, job_id: str, filename: str) -> str | None:
        if not self.cloud:
            return None
        return self.cloud.create_signed_url(f"{job_id}/{filename}")

    def download_media(self, job_id: str, filename: str, target: Path) -> bool:
        if not self.cloud:
            return False
        return self.cloud.download_artifact_to_file(job_id, filename, target)

    def update_job_fields(self, job_id: str, fields: dict) -> None:
        if self.cloud:
            self.cloud.update_job_fields(job_id, fields)

    def get_job_record(self, job_id: str) -> dict | None:
        if not self.cloud:
            return None
        return self.cloud.get_job(job_id)

    def get_status(self, job_id: str) -> StatusRecord:
        payload = self.read_json(job_id, "status")
        if not payload:
            return StatusRecord(job_id=job_id, status=JobStatus.queued)
        return StatusRecord.model_validate(payload)

    def save_feed(self, url: str, title: str, episode_count: int) -> None:
        if self.cloud:
            self.cloud.upsert_feed(url, title, episode_count)
            return
        path = self.settings.data_dir / "feeds.json"
        feeds = self._read_local_feeds(path)
        feeds[url] = {
            "url": url,
            "title": title,
            "episode_count": episode_count,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(feeds, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_feeds(self, query: str = "") -> list[dict]:
        if self.cloud:
            return self.cloud.list_feeds(query)
        feeds = list(self._read_local_feeds(self.settings.data_dir / "feeds.json").values())
        needle = query.casefold().strip()
        if needle:
            feeds = [
                feed
                for feed in feeds
                if needle in str(feed.get("title", "")).casefold()
                or needle in str(feed.get("url", "")).casefold()
            ]
        return sorted(feeds, key=lambda feed: str(feed.get("updated_at", "")), reverse=True)

    @staticmethod
    def _read_local_feeds(path: Path) -> dict[str, dict]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
