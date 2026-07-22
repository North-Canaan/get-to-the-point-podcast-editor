"""Modal deployment for daily automatic podcast discovery, analysis, and rendering.

Deploy with: ``modal deploy modal_app.py``. Durable truth remains in Supabase;
every function checks it before doing provider or media work.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import feedparser
import httpx
import modal
from anthropic import Anthropic

from podcast_editor.auth import personal_feed_token_for_user
from podcast_editor.automatic import (
    ANALYSIS_VERSION,
    SELECTION_POLICY_VERSION,
    Recipe,
    expected_output_seconds,
    normalize_url,
    select_highlights,
    source_episode_identity,
)
from podcast_editor.cloud import SupabaseClient
from podcast_editor.config import Settings
from podcast_editor.jobs import JobStore
from podcast_editor.pipeline.highlights import (
    MAX_HIGHLIGHT_RESPONSE_TOKENS,
    SYSTEM_PROMPT,
    enrich_highlights,
    parse_json_response,
)
from podcast_editor.pipeline.ingest import MAX_RSS_RESPONSE_BYTES, normalize_feed_language
from podcast_editor.pipeline.no_worker import (
    collapse_assemblyai_utterances,
    get_assemblyai_transcript,
    submit_assemblyai_transcript,
)
from podcast_editor.r2 import R2Client
from podcast_editor.security import (
    download_public_http_file,
    public_http_request,
    validate_public_http_url,
)

app = modal.App("get-to-the-point-automatic-feed")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install_from_pyproject("pyproject.toml", optional_dependencies=["automatic"])
    .add_local_python_source("podcast_editor")
)
secrets = [
    modal.Secret.from_name("get-to-the-point-automatic"),
    modal.Secret.from_name("get-to-the-point-supabase"),
]


def settings_and_db() -> tuple[Settings, SupabaseClient]:
    settings = Settings(state_backend="supabase", data_dir=Path("/tmp/podcast-editor-data"))
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Supabase automatic-worker secrets are missing")
    return settings, SupabaseClient(
        settings.supabase_url.rstrip("/"),
        settings.supabase_service_role_key,
        settings.supabase_bucket,
    )


@app.function(image=image, secrets=secrets, timeout=120)
def smoke_test_resources() -> dict[str, bool]:
    """Verify worker credentials and private storage without invoking paid AI providers."""
    settings, db = settings_and_db()
    if not settings.assemblyai_api_key or not settings.anthropic_api_key:
        raise RuntimeError("analysis provider credentials are missing")
    rest(db, "GET", "source_feeds?select=id&limit=1")
    r2 = R2Client.from_settings(settings)
    if not r2:
        raise RuntimeError("R2 credentials are missing")
    path = Path("/tmp/automatic-feed-smoke.mp3")
    key = f"temporary/smoke-{uuid4()}.mp3"
    path.write_bytes(b"ID3")
    try:
        r2.upload(key, path)
        r2.verify(key, 3)
    finally:
        try:
            r2.delete(key)
        finally:
            path.unlink(missing_ok=True)
    return {
        "supabase": True,
        "r2": True,
        "provider_credentials_present": True,
        "processing_enabled": settings.automatic_processing_enabled,
    }


def rest(
    db: SupabaseClient,
    method: str,
    path: str,
    *,
    payload: Any = None,
    prefer: str | None = None,
) -> Any:
    headers = {**db.headers, "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    with httpx.Client(timeout=30.0) as client:
        response = client.request(method, f"{db.url}/rest/v1/{path}", headers=headers, json=payload)
        response.raise_for_status()
        return response.json() if response.content else None


def upload_json(db: SupabaseClient, object_path: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode()
    with httpx.Client(timeout=60.0) as client:
        response = client.put(
            f"{db.url}/storage/v1/object/{db.bucket}/{object_path}",
            headers={**db.headers, "Content-Type": "application/json", "x-upsert": "true"},
            content=body,
        )
        response.raise_for_status()


def download_json(db: SupabaseClient, object_path: str) -> dict[str, Any] | None:
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            f"{db.url}/storage/v1/object/{db.bucket}/{object_path}", headers=db.headers
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()


def one(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if not rows:
        raise RuntimeError(f"{label} was not found")
    return rows[0]


def duration_seconds(entry: Any) -> int | None:
    raw = str(entry.get("itunes_duration") or "").strip()
    if not raw:
        return None
    try:
        parts = [int(part) for part in raw.split(":")]
    except ValueError:
        return None
    if len(parts) == 3:
        value = parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        value = parts[0] * 60 + parts[1]
    else:
        value = parts[0]
    return value if value > 0 else None


def entry_enclosure(entry: Any) -> str | None:
    for enclosure in getattr(entry, "enclosures", []) or []:
        if enclosure.get("href"):
            return str(enclosure["href"])
    for link in getattr(entry, "links", []) or []:
        if link.get("rel") == "enclosure" and link.get("href"):
            return str(link["href"])
    return None


def entry_published_at(entry: Any) -> str | None:
    value = str(entry.get("published") or entry.get("updated") or "").strip()
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def record_failure(kind: str, maximum_attempts: int):
    def decorate(function):
        @wraps(function)
        def wrapped(identifier: str):
            try:
                return function(identifier)
            except Exception as exc:
                _, db = settings_and_db()
                error_code = type(exc).__name__[:120]
                if kind == "analysis":
                    rows = rest(
                        db,
                        "GET",
                        f"source_episodes?id=eq.{identifier}&select=analysis_attempts",
                    )
                    attempts = int(rows[0]["analysis_attempts"]) if rows else maximum_attempts
                    changed = rest(
                        db,
                        "PATCH",
                        f"source_episodes?id=eq.{identifier}&analysis_status=neq.ready",
                        payload={
                            "analysis_status": "failed" if attempts >= maximum_attempts else "queued",
                            "analysis_error_code": error_code,
                        },
                        prefer="return=representation",
                    )
                    if attempts >= maximum_attempts and changed:
                        deliveries = rest(
                            db,
                            "GET",
                            f"subscription_deliveries?source_episode_id=eq.{identifier}"
                            "&status=in.(waiting,processing)&select=job_id",
                        )
                        rest(
                            db,
                            "PATCH",
                            f"subscription_deliveries?source_episode_id=eq.{identifier}"
                            "&status=in.(waiting,processing)",
                            payload={"status": "failed", "last_error_code": error_code},
                        )
                        for delivery in deliveries:
                            rest(
                                db,
                                "PATCH",
                                f"jobs?id=eq.{delivery['job_id']}",
                                payload={"status": "error", "error": error_code},
                            )
                else:
                    rows = rest(
                        db,
                        "GET",
                        f"subscription_deliveries?id=eq.{identifier}&select=attempts,job_id",
                    )
                    attempts = int(rows[0]["attempts"]) if rows else maximum_attempts
                    changed = rest(
                        db,
                        "PATCH",
                        f"subscription_deliveries?id=eq.{identifier}&status=eq.processing",
                        payload={
                            "status": "failed" if attempts >= maximum_attempts else "waiting",
                            "last_error_code": error_code,
                        },
                        prefer="return=representation",
                    )
                    if attempts >= maximum_attempts and rows and changed:
                        rest(
                            db,
                            "PATCH",
                            f"jobs?id=eq.{rows[0]['job_id']}",
                            payload={"status": "error", "error": error_code},
                        )
                raise

        return wrapped

    return decorate


@app.function(
    image=image,
    secrets=secrets,
    schedule=modal.Cron("15 3 * * *"),
    timeout=1800,
    retries=modal.Retries(max_retries=2, backoff_coefficient=2.0),
)
def poll_subscribed_feeds() -> None:
    settings = Settings(state_backend="supabase", data_dir=Path("/tmp/podcast-editor-data"))
    if not settings.automatic_processing_enabled:
        return
    _, db = settings_and_db()
    expire_deliveries(settings, db)
    subscriptions = rest(
        db,
        "GET",
        "feed_subscriptions?status=eq.active"
        "&select=id,recipe_json,start_after,baseline_completed_at,"
        "source_feeds(id,normalized_url,etag,last_modified,consecutive_failures)",
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    feeds: dict[str, dict[str, Any]] = {}
    for subscription in subscriptions:
        feed = subscription["source_feeds"]
        feeds[str(feed["id"])] = feed
        grouped.setdefault(str(feed["id"]), []).append(subscription)

    newest_once_claimed: set[str] = set()
    for feed_id, feed in feeds.items():
        headers = {}
        if feed.get("etag"):
            headers["If-None-Match"] = str(feed["etag"])
        if feed.get("last_modified"):
            headers["If-Modified-Since"] = str(feed["last_modified"])
        try:
            response = public_http_request(
                "GET", str(feed["normalized_url"]), max_bytes=MAX_RSS_RESPONSE_BYTES, headers=headers
            )
            if response.status_code == 304:
                continue
            parsed = feedparser.parse(response.content)
            if parsed.bozo and not parsed.entries:
                raise RuntimeError("feed parsing failed")
            rest(
                db,
                "PATCH",
                f"source_feeds?id=eq.{feed_id}",
                payload={
                    "etag": response.headers.get("etag"),
                    "last_modified": response.headers.get("last-modified"),
                    "last_polled_at": datetime.now(timezone.utc).isoformat(),
                    "last_poll_error": None,
                    "consecutive_failures": 0,
                },
            )
        except Exception as exc:
            failures = int(feed.get("consecutive_failures") or 0) + 1
            rest(
                db,
                "PATCH",
                f"source_feeds?id=eq.{feed_id}",
                payload={
                    "last_poll_error": type(exc).__name__,
                    "last_polled_at": datetime.now(timezone.utc).isoformat(),
                    "consecutive_failures": failures,
                },
            )
            status_code = (
                exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            )
            if failures >= 3 and status_code in {404, 410}:
                rest(
                    db,
                    "PATCH",
                    f"feed_subscriptions?source_feed_id=eq.{feed_id}&status=eq.active",
                    payload={"status": "paused"},
                )
            continue

        for entry in list(parsed.entries)[:100]:
            enclosure = entry_enclosure(entry)
            if not enclosure:
                continue
            episode_duration = duration_seconds(entry)
            # Do not admit paid processing when the six-hour maximum and cost
            # reservation cannot be established from feed metadata.
            if episode_duration is None or episode_duration > 21_600:
                continue
            episode_minutes = max(1, (episode_duration + 59) // 60)
            identity = source_episode_identity(
                str(feed["normalized_url"]), str(entry.get("id") or ""), enclosure
            )
            payload = {
                "source_feed_id": feed_id,
                "rss_guid": str(entry.get("id") or "") or None,
                "identity_hash": identity,
                "enclosure_url": normalize_url(enclosure),
                "enclosure_url_hash": hashlib.sha256(normalize_url(enclosure).encode()).hexdigest(),
                "title": str(entry.get("title") or "Edited episode")[:300],
                "published_at": entry_published_at(entry),
                "language": normalize_feed_language(parsed.feed.get("language"), fallback="he"),
                "duration_seconds": episode_duration,
                "analysis_version": ANALYSIS_VERSION,
            }
            rows = rest(
                db, "GET", f"source_episodes?identity_hash=eq.{identity}&select=*"
            )
            if not rows:
                rows = rest(
                    db,
                    "POST",
                    "source_episodes?on_conflict=identity_hash",
                    payload=payload,
                    prefer="resolution=ignore-duplicates,return=representation",
                )
                if not rows:
                    rows = rest(
                        db, "GET", f"source_episodes?identity_hash=eq.{identity}&select=*"
                    )
            episode = one(rows, "source episode")
            for subscription in grouped[feed_id]:
                subscription_id = str(subscription["id"])
                published_at = payload["published_at"]
                baseline = subscription.get("baseline_completed_at")
                is_historical = (
                    published_at < str(subscription["start_after"])
                    if published_at
                    else baseline is None or str(episode["created_at"]) <= str(baseline)
                )
                start_policy = subscription["recipe_json"].get("start_policy", "future_only")
                if is_historical:
                    if start_policy == "future_only" or subscription_id in newest_once_claimed:
                        continue
                    newest_once_claimed.add(subscription_id)
                existing_delivery = rest(
                    db,
                    "GET",
                    f"subscription_deliveries?subscription_id=eq.{subscription_id}"
                    f"&source_episode_id=eq.{episode['id']}&select=id",
                )
                if existing_delivery:
                    continue
                result = rest(
                    db,
                    "POST",
                    "rpc/admit_automatic_delivery",
                    payload={
                        "target_subscription_id": subscription["id"],
                        "target_source_episode_id": episode["id"],
                        "recipe_snapshot": subscription["recipe_json"],
                        "policy_version": SELECTION_POLICY_VERSION,
                        "source_minutes": episode_minutes,
                        "daily_source_minutes_limit": settings.automatic_global_source_minutes_per_day,
                        "daily_delivery_limit": 3,
                    },
                )
                if result and result.get("created"):
                    analyze_episode.spawn(str(episode["id"]))

        rest(
            db,
            "PATCH",
            f"feed_subscriptions?source_feed_id=eq.{feed_id}&status=eq.active"
            "&baseline_completed_at=is.null",
            payload={"baseline_completed_at": datetime.now(timezone.utc).isoformat()},
        )

    reconcile(db)


def expire_deliveries(settings: Settings, db: SupabaseClient) -> None:
    expired = rest(
        db,
        "GET",
        "subscription_deliveries?status=eq.published&expires_at=lt.now()"
        "&r2_object_key=not.is.null&select=id,job_id,r2_object_key&limit=500",
    )
    r2 = R2Client.from_settings(settings)
    if expired and not r2:
        raise RuntimeError("R2 credentials are missing for retention cleanup")
    for delivery in expired:
        r2.delete(str(delivery["r2_object_key"]))
        rest(db, "DELETE", f"private_feed_items?job_id=eq.{delivery['job_id']}")
        rest(
            db,
            "PATCH",
            f"subscription_deliveries?id=eq.{delivery['id']}",
            payload={"r2_object_key": None},
        )


def reconcile(db: SupabaseClient) -> None:
    stale = quote(
        (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat(),
        safe="",
    )
    rest(
        db,
        "PATCH",
        "source_episodes?analysis_status=eq.analyzing"
        f"&updated_at=lt.{stale}",
        payload={"analysis_status": "queued"},
    )
    episodes = rest(
        db,
        "GET",
        "source_episodes?analysis_status=in.(queued,analyzing)&analysis_attempts=lt.5"
        f"&updated_at=lt.{stale}&select=id&limit=100",
    )
    for episode in episodes:
        analyze_episode.spawn(str(episode["id"]))
    deliveries = rest(
        db,
        "GET",
        "subscription_deliveries?status=eq.waiting&attempts=lt.3"
        "&select=id,source_episodes(analysis_status)&limit=100",
    )
    for delivery in deliveries:
        if delivery["source_episodes"]["analysis_status"] == "ready":
            render_delivery.spawn(str(delivery["id"]))
    rest(
        db,
        "PATCH",
        "subscription_deliveries?status=eq.processing"
        f"&updated_at=lt.{stale}",
        payload={"status": "waiting"},
    )
    processing = rest(
        db,
        "GET",
        "subscription_deliveries?status=eq.processing&attempts=lt.3"
        f"&updated_at=lt.{stale}&select=id,source_episodes(analysis_status)&limit=100",
    )
    for delivery in processing:
        if delivery["source_episodes"]["analysis_status"] == "ready":
            render_delivery.spawn(str(delivery["id"]))


@app.function(
    image=image,
    secrets=secrets,
    timeout=21_600,
    retries=modal.Retries(max_retries=4, backoff_coefficient=2.0),
    max_containers=5,
)
@record_failure("analysis", 5)
def analyze_episode(source_episode_id: str) -> None:
    settings, db = settings_and_db()
    claimed = rest(
        db,
        "PATCH",
        f"source_episodes?id=eq.{source_episode_id}&analysis_status=in.(queued,failed)",
        payload={"analysis_status": "analyzing"},
        prefer="return=representation",
    )
    if not claimed:
        return
    episode = claimed[0]
    transcript_path = f"automatic/{source_episode_id}/v{ANALYSIS_VERSION}/transcript.json"
    highlights_path = f"automatic/{source_episode_id}/v{ANALYSIS_VERSION}/highlights.json"
    if episode["analysis_status"] == "ready" and download_json(db, highlights_path):
        spawn_waiting_deliveries(db, source_episode_id)
        return
    rest(
        db,
        "PATCH",
        f"source_episodes?id=eq.{source_episode_id}&analysis_status=eq.analyzing",
        payload={"analysis_attempts": int(episode["analysis_attempts"]) + 1},
    )
    if not settings.assemblyai_api_key or not settings.anthropic_api_key:
        raise RuntimeError("analysis provider credentials are missing")
    transcript = download_json(db, transcript_path)
    if not transcript:
        transcript_id = episode.get("assemblyai_transcript_id")
        if not transcript_id:
            validate_public_http_url(str(episode["enclosure_url"]))
            transcript_id = submit_assemblyai_transcript(
                settings.assemblyai_api_key,
                str(episode["enclosure_url"]),
                str(episode.get("language") or "he"),
            )
            rest(
                db,
                "PATCH",
                f"source_episodes?id=eq.{source_episode_id}&assemblyai_transcript_id=is.null",
                payload={"assemblyai_transcript_id": transcript_id},
            )
        while True:
            result = get_assemblyai_transcript(settings.assemblyai_api_key, str(transcript_id))
            if result.get("status") == "completed":
                break
            if result.get("status") == "error":
                raise RuntimeError("AssemblyAI transcription failed")
            time.sleep(10)
        transcript = collapse_assemblyai_utterances(result)
        upload_json(db, transcript_path, transcript)

    highlights = download_json(db, highlights_path)
    if not highlights:
        content = json.dumps(
            {"language": episode.get("language") or "he", "editorial_preferences": {"mode": "library"}, "segments": transcript["segments"]},
            ensure_ascii=False,
        )
        message = Anthropic(api_key=settings.anthropic_api_key).messages.create(
            model=settings.anthropic_model,
            max_tokens=MAX_HIGHLIGHT_RESPONSE_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        response_text = "\n".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        )
        highlights = enrich_highlights(parse_json_response(response_text), transcript["segments"])
        upload_json(db, highlights_path, highlights)
    rest(
        db,
        "PATCH",
        f"source_episodes?id=eq.{source_episode_id}",
        payload={
            "analysis_status": "ready",
            "transcript_storage_path": transcript_path,
            "highlights_storage_path": highlights_path,
            "analysis_error_code": None,
        },
    )
    spawn_waiting_deliveries(db, source_episode_id)


def spawn_waiting_deliveries(db: SupabaseClient, source_episode_id: str) -> None:
    rows = rest(
        db,
        "GET",
        f"subscription_deliveries?source_episode_id=eq.{source_episode_id}&status=eq.waiting&select=id",
    )
    for row in rows:
        render_delivery.spawn(str(row["id"]))


@app.function(
    image=image,
    secrets=secrets,
    timeout=7200,
    retries=modal.Retries(max_retries=2, backoff_coefficient=2.0),
    max_containers=5,
    cpu=2,
    memory=2048,
)
@record_failure("render", 3)
def render_delivery(delivery_id: str) -> None:
    settings, db = settings_and_db()
    delivery_rows = rest(
        db,
        "GET",
        f"subscription_deliveries?id=eq.{delivery_id}&status=eq.waiting"
        "&select=*,source_episodes(*),feed_subscriptions(user_id)",
    )
    if not delivery_rows:
        return
    delivery = delivery_rows[0]
    episode = delivery["source_episodes"]
    if episode["analysis_status"] != "ready":
        return
    claimed = rest(
        db,
        "PATCH",
        f"subscription_deliveries?id=eq.{delivery_id}&status=eq.waiting",
        payload={
            "status": "processing",
            "attempts": int(delivery["attempts"]) + 1,
        },
        prefer="return=representation",
    )
    if not claimed:
        return
    highlights = download_json(db, str(episode["highlights_storage_path"]))
    if not highlights:
        raise RuntimeError("highlight artifact is unavailable")
    recipe = Recipe.from_dict(delivery["recipe_snapshot_json"])
    selected = select_highlights(highlights.get("highlights", []), recipe)
    if not selected:
        rest(
            db,
            "PATCH",
            f"subscription_deliveries?id=eq.{delivery_id}&status=in.(waiting,processing)",
            payload={"status": "no_matching_highlights"},
        )
        return
    expected_seconds = expected_output_seconds(selected, recipe.transition_seconds)
    rest(
        db,
        "PATCH",
        f"subscription_deliveries?id=eq.{delivery_id}&status=in.(waiting,processing)",
        payload={
            "status": "processing",
            "selected_highlight_ids_json": [item.get("id") for item in selected],
            "expected_duration_seconds": expected_seconds,
        },
    )
    with tempfile.TemporaryDirectory() as temporary_directory:
        work = Path(temporary_directory)
        source = work / "source"
        download_public_http_file(str(episode["enclosure_url"]), source, max_bytes=1_000_000_000)
        output = render_mp3(source, selected, recipe.transition_seconds, work, settings.max_output_bytes)
        probe = ffprobe(output)
        size = output.stat().st_size
        digest_builder = hashlib.sha256()
        with output.open("rb") as rendered:
            for chunk in iter(lambda: rendered.read(1024 * 1024), b""):
                digest_builder.update(chunk)
        digest = digest_builder.hexdigest()
        duration_tolerance = max(2.0, expected_seconds * 0.02)
        if abs(probe - expected_seconds) > duration_tolerance:
            raise RuntimeError("rendered duration failed verification")
        r2 = R2Client.from_settings(settings)
        if not r2:
            raise RuntimeError("R2 credentials are missing")
        temporary_key = f"temporary/{delivery_id}/{uuid4()}.mp3"
        final_key = f"deliveries/{delivery_id}/recipe-v{SELECTION_POLICY_VERSION}.mp3"
        try:
            r2.upload(temporary_key, output)
            r2.verify(temporary_key, size)
            r2.promote(temporary_key, final_key)
            r2.verify(final_key, size)
        finally:
            try:
                r2.delete(temporary_key)
            except Exception:
                # The bucket lifecycle rule is the fallback for abandoned temporary objects.
                pass
    user_id = str(delivery["feed_subscriptions"]["user_id"])
    users = rest(db, "GET", f"user?id=eq.{user_id}&select=id,email")
    token = personal_feed_token_for_user(one(users, "delivery owner"), settings)
    token_hash = JobStore.private_feed_token_hash(token)
    rest(
        db,
        "POST",
        "rpc/publish_automatic_delivery",
        payload={
            "target_delivery_id": delivery_id,
            "target_r2_key": final_key,
            "target_size_bytes": size,
            "target_duration_seconds": probe,
            "target_sha256": digest,
            "personal_feed_token_hash": token_hash,
        },
    )


def render_mp3(
    source: Path,
    selected: list[dict[str, Any]],
    transition_seconds: float,
    work: Path,
    max_bytes: int,
) -> Path:
    concat_paths: list[Path] = []
    for index, item in enumerate(selected):
        clip = work / f"clip-{index:04d}.wav"
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-v", "error", "-i", str(source),
                "-ss", str(item["start"]), "-to", str(item["end"]),
                "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(clip),
            ],
            check=True,
        )
        concat_paths.append(clip)
        if transition_seconds and index < len(selected) - 1:
            silence = work / f"silence-{index:04d}.wav"
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=44100:cl=stereo", "-t", str(transition_seconds), str(silence),
                ],
                check=True,
            )
            concat_paths.append(silence)
    concat_file = work / "concat.txt"
    concat_file.write_text("".join(f"file '{path.name}'\n" for path in concat_paths))
    output = work / "output.mp3"
    seconds = expected_output_seconds(selected, transition_seconds)
    bitrates = [128, 96, 64, 48, 32]
    bitrate = next((rate for rate in bitrates if seconds * rate * 1000 / 8 <= max_bytes * 0.95), None)
    if bitrate is None:
        raise RuntimeError("estimated output exceeds the configured size limit")
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(concat_file), "-c:a", "libmp3lame", "-b:a", f"{bitrate}k", str(output),
        ],
        check=True,
        cwd=work,
    )
    return output


def ffprobe(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise RuntimeError("rendered output has invalid duration")
    return duration
