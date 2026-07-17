import mimetypes
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx
import yt_dlp

from ..jobs import JobStore
from .media import ffprobe_duration, transcode_to_16k_wav


class IngestError(RuntimeError):
    pass


def ingest(job_id: str, source_url: str, store: JobStore) -> dict:
    resolved_url = resolve_audio_url(source_url)
    original_path = download_audio(job_id, resolved_url, store)
    duration = ffprobe_duration(original_path)
    audio16k = store.artifact_path(job_id, "audio16k")
    transcode_to_16k_wav(original_path, audio16k)
    store.upload_media(job_id, original_path, content_type="audio/mpeg")
    store.upload_media(job_id, audio16k, content_type="audio/wav")

    payload = {
        "source_url": source_url,
        "resolved_audio_url": resolved_url,
        "duration": duration,
        "original_filename": original_path.name,
    }
    store.write_json(job_id, "input", payload)
    return payload


def resolve_audio_url(source_url: str) -> str:
    feed_url = maybe_resolve_feed(source_url)
    if feed_url:
        return feed_url
    return ytdlp_extract_audio_url(source_url)


def maybe_resolve_feed(source_url: str) -> str | None:
    headers: dict[str, str] = {}
    try:
        with httpx.Client(follow_redirects=True, timeout=20.0) as client:
            response = client.get(source_url)
            headers = dict(response.headers)
            sample = response.text[:2000]
    except httpx.HTTPError:
        response = None
        sample = ""

    content_type = headers.get("content-type", "")
    parsed = feedparser.parse(sample if "xml" in content_type else source_url)
    if not parsed.entries:
        return None

    entry = parsed.entries[0]
    enclosures = getattr(entry, "enclosures", []) or []
    for enclosure in enclosures:
        href = enclosure.get("href")
        if href:
            return href
    links = getattr(entry, "links", []) or []
    for link in links:
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]
    return None


def ytdlp_extract_audio_url(source_url: str) -> str:
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source_url, download=False)
    except Exception as exc:  # yt-dlp raises a broad family of exceptions.
        raise IngestError(f"yt-dlp failed to resolve audio: {exc}") from exc

    if not isinstance(info, dict):
        raise IngestError("yt-dlp returned no media info")
    direct_url = info.get("url")
    if not direct_url:
        raise IngestError("yt-dlp did not resolve an audio URL")
    return str(direct_url)


def download_audio(job_id: str, audio_url: str, store: JobStore) -> Path:
    extension = guess_extension(audio_url)
    output = store.job_dir(job_id) / f"original{extension}"

    if is_local_path(audio_url):
        source = Path(urlparse(audio_url).path if audio_url.startswith("file://") else audio_url)
        if not source.exists():
            raise IngestError(f"local audio path does not exist: {source}")
        shutil.copyfile(source, output)
        return output

    args = [
        "yt-dlp",
        "--no-playlist",
        "-f",
        "bestaudio/best",
        "-o",
        str(output),
        audio_url,
    ]
    try:
        subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        direct_download(audio_url, output)
    return output


def direct_download(audio_url: str, output: Path) -> None:
    with httpx.stream("GET", audio_url, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        with output.open("wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)


def guess_extension(audio_url: str) -> str:
    parsed = urlparse(audio_url)
    suffix = Path(parsed.path).suffix
    if suffix and len(suffix) <= 8:
        return suffix
    guessed = mimetypes.guess_extension(parsed.path)
    return guessed or ".mp3"


def is_local_path(value: str) -> bool:
    return value.startswith("file://") or Path(value).exists()
