import json
import threading
import time
from hashlib import sha256
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
        self._rate_limits: dict[str, tuple[float, int]] = {}
        self._rate_limit_lock = threading.Lock()

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
        # A Vercel instance can retain /tmp between requests while another instance
        # advances the job. Supabase is authoritative whenever cloud state is enabled.
        if self.cloud:
            return self.cloud.download_json_artifact(job_id, ARTIFACT_NAMES[name])
        if not path.exists():
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

    def signed_media_upload_url(self, job_id: str, filename: str) -> str | None:
        if not self.cloud:
            return None
        return self.cloud.create_signed_upload_url(f"{job_id}/{filename}")

    def delete_media(self, job_id: str, filename: str) -> None:
        path = self.job_dir(job_id) / filename
        path.unlink(missing_ok=True)
        if self.cloud:
            self.cloud.delete_artifact(job_id, filename)

    def artifact_size(self, job_id: str, name: str) -> int | None:
        path = self.artifact_path(job_id, name)
        if path.exists():
            return path.stat().st_size
        if self.cloud:
            return self.cloud.artifact_size(job_id, ARTIFACT_NAMES[name])
        return None

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

    def consume_rate_limit(self, key: str, window_seconds: int, maximum: int) -> bool:
        if self.cloud:
            return self.cloud.consume_rate_limit(key, window_seconds, maximum)
        now = time.monotonic()
        with self._rate_limit_lock:
            started_at, count = self._rate_limits.get(key, (now, 0))
            if now - started_at >= window_seconds:
                started_at, count = now, 0
            if count >= maximum:
                return False
            self._rate_limits[key] = (started_at, count + 1)
            return True

    def list_user_jobs(self, user_id: str) -> list[dict]:
        return self.cloud.list_user_jobs(user_id) if self.cloud else []

    def get_status(self, job_id: str) -> StatusRecord:
        payload = self.read_json(job_id, "status")
        if not payload:
            return StatusRecord(job_id=job_id, status=JobStatus.queued)
        return StatusRecord.model_validate(payload)

    def find_cached_transcript(
        self, audio_url: str, highlight_selection: dict | None = None
    ) -> tuple[dict, dict | None, str] | None:
        if not self.cloud:
            return None
        transcript_only = None
        for job in self.cloud.find_jobs_by_audio_url(audio_url):
            source_job_id = str(job["id"])
            transcript = self.cloud.download_json_artifact(source_job_id, "transcript.json")
            if transcript:
                highlights = self.cloud.download_json_artifact(source_job_id, "highlights.json")
                selection = (highlights or {}).get("selection", {})
                if highlights and highlight_selection is not None and selection == highlight_selection:
                    return transcript, highlights, source_job_id
                if transcript_only is None:
                    transcript_only = (transcript, None, source_job_id)
        return transcript_only

    def find_cached_highlights(self, audio_url: str, selection: dict) -> dict | None:
        if not self.cloud:
            return None
        for job in self.cloud.find_jobs_by_audio_url(audio_url):
            highlights = self.cloud.download_json_artifact(str(job["id"]), "highlights.json")
            if highlights and highlights.get("selection") == selection:
                return highlights
        return None

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

    def add_private_feed_item(
        self, token: str, job_id: str, title: str, size_bytes: int, user_id: str | None = None
    ) -> None:
        token_hash = self.private_feed_token_hash(token)
        if self.cloud:
            self.cloud.add_private_feed_item(token_hash, job_id, title, size_bytes, user_id)
            return
        path = self.settings.data_dir / "private_feeds.json"
        feeds = self._read_local_feeds(path)
        feed = feeds.setdefault(token_hash, {"items": []})
        items = [item for item in feed["items"] if item["job_id"] != job_id]
        items.append(
            {
                "job_id": job_id,
                "title": title,
                "size_bytes": size_bytes,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        feed["items"] = items
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(feeds, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_private_feed_items(self, token: str) -> list[dict] | None:
        token_hash = self.private_feed_token_hash(token)
        if self.cloud:
            return self.cloud.list_private_feed_items(token_hash)
        feeds = self._read_local_feeds(self.settings.data_dir / "private_feeds.json")
        feed = feeds.get(token_hash)
        if not feed:
            return None
        return sorted(feed["items"], key=lambda item: item["published_at"], reverse=True)

    def private_feed_contains(self, token: str, job_id: str) -> bool:
        items = self.list_private_feed_items(token)
        return bool(items and any(item["job_id"] == job_id for item in items))

    def claim_private_feed(self, anonymous_token: str, account_token: str, user_id: str) -> int:
        anonymous_hash = self.private_feed_token_hash(anonymous_token)
        account_hash = self.private_feed_token_hash(account_token)
        if anonymous_hash == account_hash:
            return 0
        if self.cloud:
            return self.cloud.claim_private_feed(anonymous_hash, account_hash, user_id)

        path = self.settings.data_dir / "private_feeds.json"
        feeds = self._read_local_feeds(path)
        source = feeds.get(anonymous_hash)
        if not source:
            return 0
        if source.get("user_id") == user_id:
            return 0
        if source.get("user_id") is not None:
            raise PermissionError("anonymous feed is already associated with another account")
        target = feeds.setdefault(account_hash, {"items": [], "user_id": user_id})
        existing = {item["job_id"]: item for item in target.get("items", [])}
        for item in source.get("items", []):
            existing[item["job_id"]] = item
        target["items"] = list(existing.values())
        target["user_id"] = user_id
        claimed = len(source.get("items", []))
        feeds.pop(anonymous_hash, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(feeds, ensure_ascii=False, indent=2), encoding="utf-8")
        return claimed

    @staticmethod
    def private_feed_token_hash(token: str) -> str:
        return sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _read_local_feeds(path: Path) -> dict[str, dict]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
