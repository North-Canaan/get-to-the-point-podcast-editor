import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
        }

    def upsert_job(self, job_id: str, status: JobStatus, error: str | None = None) -> None:
        payload = {"id": job_id, "status": status.value, "error": error}
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

    def download_json_artifact(self, job_id: str, name: str) -> dict[str, Any] | None:
        object_path = f"{job_id}/{name}"
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{self.url}/storage/v1/object/{self.bucket}/{object_path}",
                headers=self.headers,
            )
            if response.status_code == 404:
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
