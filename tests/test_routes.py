import json
from pathlib import Path

from fastapi.testclient import TestClient
from fastapi import HTTPException

from podcast_editor.config import Settings
from podcast_editor.jobs import JobStore, new_job_id
from podcast_editor.schemas import JobStatus
import podcast_editor.main as main_module
from podcast_editor.main import app


def test_review_page_rewrite_does_not_intercept_review_submission() -> None:
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))

    assert all(rewrite["source"] != "/jobs/:job_id/review" for rewrite in config["rewrites"])
    review_routes = [route for route in app.routes if route.path == "/jobs/{job_id}/review"]
    assert any("GET" in route.methods for route in review_routes)
    assert any("POST" in route.methods for route in review_routes)
    route_paths = {route.path for route in app.routes}
    assert "/jobs/{job_id}/output-upload-url" in route_paths
    assert "/jobs/{job_id}/output-complete" in route_paths
    assert "/jobs/{job_id}/private-feed/email" in route_paths


def test_anonymous_user_can_start_episode_when_auth_is_configured(
    monkeypatch, tmp_path: Path
) -> None:
    test_store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(
            data_dir=tmp_path,
            state_backend="filesystem",
            better_auth_url="https://auth.example.test",
        ),
    )
    monkeypatch.setattr(
        "podcast_editor.auth.current_user",
        lambda *_args: (_ for _ in ()).throw(HTTPException(status_code=401)),
    )
    monkeypatch.setattr(main_module, "submit_no_worker_job", lambda *_args: {})
    monkeypatch.setattr(main_module, "validate_public_http_url", lambda url: url)

    response = TestClient(app).post(
        "/jobs",
        json={"url": "https://cdn.example.test/episode.mp3", "title": "Anonymous edit"},
    )

    assert response.status_code == 200
    job = test_store.get_job_record(response.json()["job_id"])
    assert job is None
    assert test_store.get_status(response.json()["job_id"]).status == JobStatus.queued


def test_job_state_includes_server_timing_when_available(monkeypatch, tmp_path: Path) -> None:
    test_store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    job_id = new_job_id()
    test_store.write_json(job_id, "input", {"episode_title": "Timed episode"})
    test_store.set_status(job_id, JobStatus.needs_review)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(
        test_store,
        "get_job_record",
        lambda _job_id: {
            "created_at": "2026-07-18T10:00:00+00:00",
            "updated_at": "2026-07-18T10:05:00+00:00",
        },
    )

    payload = TestClient(app).get(f"/jobs/{job_id}/state").json()

    assert payload["created_at"] == "2026-07-18T10:00:00+00:00"
    assert payload["status_updated_at"] == "2026-07-18T10:05:00+00:00"
    assert payload["email_delivery_available"] is False


def test_private_feed_serves_only_attached_edited_episode(monkeypatch, tmp_path: Path) -> None:
    test_store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(
            data_dir=tmp_path,
            state_backend="filesystem",
            better_auth_url="https://auth.example.test",
        ),
    )
    monkeypatch.setattr(
        "podcast_editor.auth.current_user",
        lambda *_args: (_ for _ in ()).throw(HTTPException(status_code=401)),
    )
    job_id = new_job_id()
    token = "private_feed_token_abcdefghijklmnopqrstuvwxyz"
    test_store.write_json(job_id, "input", {"episode_title": "A useful conversation"})
    test_store.artifact_path(job_id, "output").write_bytes(b"edited-audio")
    test_store.set_status(job_id, JobStatus.done)
    client = TestClient(app)

    attached = client.post(f"/jobs/{job_id}/private-feed", json={"token": token})
    assert attached.status_code == 200

    feed = client.get(f"/private-feed/{token}.xml")
    assert feed.status_code == 200
    assert "A useful conversation" in feed.text
    assert f"/private-feed/{token}/episodes/{job_id}.mp3" in feed.text

    episode = client.get(f"/private-feed/{token}/episodes/{job_id}.mp3")
    assert episode.status_code == 200
    assert episode.content == b"edited-audio"
    denied = client.get(f"/private-feed/{'x' * 43}/episodes/{job_id}.mp3")
    assert denied.status_code == 404


def test_signed_in_user_can_email_personal_feed(monkeypatch, tmp_path: Path) -> None:
    test_settings = Settings(
        data_dir=tmp_path,
        state_backend="filesystem",
        better_auth_url="https://auth.example.test",
        better_auth_secret="test-secret",
        resend_api_key="test-resend-key",
    )
    test_store = JobStore(test_settings)
    job_id = new_job_id()
    test_store.write_json(job_id, "input", {"episode_title": "A useful conversation"})
    test_store.artifact_path(job_id, "output").write_bytes(b"edited-audio")
    test_store.set_status(job_id, JobStatus.done)
    sent = []
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(
        main_module,
        "current_user",
        lambda *_args: {"id": "user-1", "email": "listener@example.com"},
    )
    monkeypatch.setattr(
        main_module,
        "send_private_feed_email",
        lambda email, feed_url, _settings: sent.append((email, feed_url)),
    )

    response = TestClient(app).post(f"/jobs/{job_id}/private-feed/email")

    assert response.status_code == 200
    assert response.json()["email"] == "l•••••••@example.com"
    assert sent[0][0] == "listener@example.com"
    assert sent[0][1].startswith("http://testserver/private-feed/")
    assert test_store.list_private_feed_items(sent[0][1].split("/")[-1][:-4])
