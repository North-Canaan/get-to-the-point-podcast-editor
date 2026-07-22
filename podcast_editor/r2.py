"""Small, isolated Cloudflare R2 client used for verified enclosures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings


@dataclass
class R2Client:
    client: Any
    bucket: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "R2Client | None":
        if not all(
            (settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key)
        ):
            return None
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )
        return cls(client, settings.r2_bucket)

    def upload(self, key: str, path: Path) -> None:
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": "audio/mpeg"}
        )

    def verify(self, key: str, expected_size: int) -> None:
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        if int(head.get("ContentLength") or 0) != expected_size:
            raise RuntimeError("R2 object size verification failed")
        if str(head.get("ContentType") or "").split(";", 1)[0] != "audio/mpeg":
            raise RuntimeError("R2 object MIME verification failed")
        ranged = self.client.get_object(Bucket=self.bucket, Key=key, Range="bytes=0-1023")
        body = ranged["Body"]
        try:
            if not body.read(1024):
                raise RuntimeError("R2 ranged-read verification failed")
        finally:
            body.close()

    def promote(self, temporary_key: str, final_key: str) -> None:
        self.client.copy_object(
            Bucket=self.bucket,
            Key=final_key,
            CopySource={"Bucket": self.bucket, "Key": temporary_key},
            ContentType="audio/mpeg",
            MetadataDirective="REPLACE",
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str, method: str = "get_object", expires_in: int = 900) -> str:
        return str(
            self.client.generate_presigned_url(
                method,
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        )
