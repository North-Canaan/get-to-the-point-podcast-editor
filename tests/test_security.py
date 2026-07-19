import socket
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import podcast_editor.main as main_module
from podcast_editor.config import Settings
from podcast_editor.jobs import JobStore, new_job_id
from podcast_editor.main import app
from podcast_editor.schemas import JobStatus
from podcast_editor.security import enforce_same_origin, validate_public_http_url


def test_ssrf_guard_rejects_private_and_credentialed_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(HTTPException, match="Local or reserved"):
        validate_public_http_url("https://internal.example.test/resource")
    with pytest.raises(HTTPException, match="credentials"):
        validate_public_http_url("https://user:password@example.com/feed")
    with pytest.raises(HTTPException, match="Only public"):
        validate_public_http_url("file:///etc/passwd")


def test_ssrf_guard_accepts_public_address(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    assert validate_public_http_url("https://example.com/feed") == "https://example.com/feed"


def test_csrf_guard_rejects_cross_site_origin(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, trusted_origins="https://app.example.test")
    request = type(
        "RequestStub",
        (),
        {
            "method": "POST",
            "headers": {"origin": "https://evil.example", "sec-fetch-site": "cross-site"},
        },
    )()
    with pytest.raises(HTTPException, match="cross-site"):
        enforce_same_origin(request, settings)


def test_security_headers_are_set(monkeypatch, tmp_path: Path) -> None:
    test_store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    monkeypatch.setattr(main_module, "store", test_store)
    response = TestClient(app).get("/faq")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_owned_job_is_hidden_from_another_user(monkeypatch, tmp_path: Path) -> None:
    test_settings = Settings(
        data_dir=tmp_path,
        state_backend="filesystem",
        better_auth_url="https://auth.example.test",
    )
    test_store = JobStore(test_settings)
    job_id = new_job_id()
    test_store.set_status(job_id, JobStatus.needs_review)
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(test_store, "get_job_record", lambda _job_id: {"user_id": "owner-1"})
    monkeypatch.setattr(main_module, "current_user", lambda *_args: {"id": "other-user"})

    response = TestClient(app).get(f"/jobs/{job_id}/state")

    assert response.status_code == 404


def test_rate_limit_rejects_excessive_job_creation(monkeypatch, tmp_path: Path) -> None:
    test_store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(main_module, "validate_public_http_url", lambda url: url)
    monkeypatch.setattr(main_module, "submit_no_worker_job", lambda *_args: {})
    client = TestClient(app)

    responses = [client.post("/jobs", json={"url": "https://example.com/a.mp3"}) for _ in range(7)]

    assert [response.status_code for response in responses[:6]] == [200] * 6
    assert responses[6].status_code == 429
    assert responses[6].headers["retry-after"] == "3600"
