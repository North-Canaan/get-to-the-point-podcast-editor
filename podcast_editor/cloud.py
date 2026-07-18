import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings
from .schemas import JobStatus


@dataclass
class SupabaseClient:
    url: str
    service_role_key: str
    bucket: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "SupabaseClient | None":
        if (
            settings.state_backend != "supabase"
            or not settings.supabase_url
            or not settings.supabase_service_role_key
        ):
            return None
        return cls(
            url=settings.supabase_url.rstrip("/"),
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_bucket,
        )

    @property
    def headers(self) -> dict[str, str]:
        headers = {"apikey": self.service_role_key}
        if not self.service_role_key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {self.service_role_key}"
        return headers

    def upsert_job(
        self,
        job_id: str,
        status: JobStatus,
        error: str | None = None,
        source_url: str | None = None,
        clear_lock: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = {"id": job_id, "status": status.value, "error": error}
        if source_url is not None:
            payload["source_url"] = source_url
        if extra:
            payload.update(extra)
        if clear_lock:
            payload["worker_id"] = None
            payload["locked_at"] = None
        headers = {
            **self.headers,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{self.url}/rest/v1/jobs?on_conflict=id", headers=headers, json=payload
            )
            response.raise_for_status()

    def update_job_fields(self, job_id: str, fields: dict[str, Any]) -> None:
        headers = {**self.headers, "Content-Type": "application/json"}
        with httpx.Client(timeout=20.0) as client:
            response = client.patch(
                f"{self.url}/rest/v1/jobs?id=eq.{job_id}",
                headers=headers,
                json=fields,
            )
            response.raise_for_status()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{self.url}/rest/v1/jobs?id=eq.{job_id}&select=*",
                headers=self.headers,
            )
            response.raise_for_status()
            rows = response.json()
            return rows[0] if rows else None

    def find_jobs_by_audio_url(self, audio_url: str, limit: int = 5) -> list[dict[str, Any]]:
        encoded_url = quote(audio_url, safe="")
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{self.url}/rest/v1/jobs"
                f"?resolved_audio_url=eq.{encoded_url}"
                f"&select=id,status,updated_at"
                f"&order=updated_at.desc&limit={limit}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    def claim_job(
        self,
        job_id: str,
        expected_status: JobStatus,
        worker_id: str,
        next_status: JobStatus | None = None,
    ) -> bool:
        payload = {
            "worker_id": worker_id,
            "locked_at": datetime.now(timezone.utc).isoformat(),
        }
        if next_status:
            payload["status"] = next_status.value
        headers = {
            **self.headers,
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        with httpx.Client(timeout=20.0) as client:
            response = client.patch(
                f"{self.url}/rest/v1/jobs"
                f"?id=eq.{job_id}&status=eq.{expected_status.value}&worker_id=is.null",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return bool(response.json())

    def list_available_jobs(self, statuses: list[JobStatus], limit: int = 5) -> list[dict[str, Any]]:
        status_values = ",".join(status.value for status in statuses)
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{self.url}/rest/v1/jobs"
                f"?select=id,status,source_url,created_at"
                f"&status=in.({status_values})"
                f"&worker_id=is.null"
                f"&order=created_at.asc"
                f"&limit={limit}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    def upload_artifact(self, job_id: str, name: str, path: Path, content_type: str) -> None:
        object_path = f"{job_id}/{name}"
        headers = {**self.headers, "Content-Type": content_type, "x-upsert": "true"}
        with httpx.Client(timeout=120.0) as client:
            response = client.put(
                f"{self.url}/storage/v1/object/{self.bucket}/{object_path}",
                headers=headers,
                content=path.read_bytes(),
            )
            response.raise_for_status()

    def download_artifact_to_file(self, job_id: str, name: str, target: Path) -> bool:
        object_path = f"{job_id}/{name}"
        with httpx.Client(timeout=120.0) as client:
            response = client.get(
                f"{self.url}/storage/v1/object/{self.bucket}/{object_path}",
                headers=self.headers,
            )
            if storage_object_not_found(response):
                return False
            response.raise_for_status()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
            return True

    def download_json_artifact(self, job_id: str, name: str) -> dict[str, Any] | None:
        object_path = f"{job_id}/{name}"
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{self.url}/storage/v1/object/{self.bucket}/{object_path}",
                headers=self.headers,
            )
            if storage_object_not_found(response):
                return None
            response.raise_for_status()
            return json.loads(response.text)

    def create_signed_url(self, object_path: str, expires_in: int = 3600) -> str:
        headers = {**self.headers, "Content-Type": "application/json"}
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{self.url}/storage/v1/object/sign/{self.bucket}/{object_path}",
                headers=headers,
                json={"expiresIn": expires_in},
            )
            response.raise_for_status()
            signed_url = response.json()["signedURL"]
            if signed_url.startswith("http"):
                return signed_url
            return f"{self.url}/storage/v1{signed_url}"
    def upsert_feed(self, url: str, title: str, episode_count: int) -> None:
        headers = {
            **self.headers,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        }
        payload = {"url": url, "title": title, "episode_count": episode_count}
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{self.url}/rest/v1/feeds?on_conflict=url", headers=headers, json=payload
            )
            response.raise_for_status()

    def list_feeds(self, query: str = "") -> list[dict[str, Any]]:
        endpoint = f"{self.url}/rest/v1/feeds?select=url,title,episode_count,updated_at"
        needle = query.strip()
        if needle:
            pattern = quote(f"*{needle}*", safe="*")
            endpoint += f"&or=(title.ilike.{pattern},url.ilike.{pattern})"
        endpoint += "&order=updated_at.desc&limit=100"
        with httpx.Client(timeout=20.0) as client:
            response = client.get(endpoint, headers=self.headers)
            response.raise_for_status()
            return response.json()


def storage_object_not_found(response: httpx.Response) -> bool:
    if response.status_code == 404:
        return True
    if response.status_code != 400:
        return False
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return False
    status_code = str(payload.get("statusCode", ""))
    error = str(payload.get("error", "")).casefold()
    message = str(payload.get("message", "")).casefold()
    return status_code == "404" or error in {"not_found", "not found"} or "not found" in message
