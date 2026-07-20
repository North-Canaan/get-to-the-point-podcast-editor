from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import Response

from podcast_editor.auth import (
    current_user,
    optional_current_user,
    personal_feed_token,
    personal_feed_token_for_user,
)
from podcast_editor.config import Settings


class FakeClient:
    response = Response(200, json=None)
    headers = {}

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, _url, headers):
        type(self).headers = headers
        return type(self).response


def test_current_user_forwards_session_cookie(tmp_path, monkeypatch) -> None:
    app = FastAPI()
    settings = Settings(
        data_dir=tmp_path,
        better_auth_url="https://example.test",
        better_auth_secret="x" * 32,
    )

    @app.get("/protected")
    def protected(request: Request):
        return current_user(request, settings)

    FakeClient.response = Response(
        200, json={"user": {"id": "user-1", "email": "a@example.com"}}
    )
    monkeypatch.setattr("podcast_editor.auth.httpx.Client", FakeClient)
    response = TestClient(app).get("/protected", cookies={"better-auth.session_token": "token"})

    assert response.status_code == 200
    assert response.json()["id"] == "user-1"
    assert "better-auth.session_token=token" in FakeClient.headers["cookie"]


def test_current_user_rejects_missing_session(tmp_path, monkeypatch) -> None:
    settings = Settings(data_dir=tmp_path, better_auth_url="https://example.test")
    app = FastAPI()

    @app.get("/protected")
    def protected(request: Request):
        return current_user(request, settings)

    FakeClient.response = Response(200, json=None)
    monkeypatch.setattr("podcast_editor.auth.httpx.Client", FakeClient)
    response = TestClient(app).get("/protected")
    assert response.status_code == 401


def test_optional_current_user_allows_missing_session(tmp_path, monkeypatch) -> None:
    settings = Settings(data_dir=tmp_path, better_auth_url="https://example.test")
    app = FastAPI()

    @app.get("/optional")
    def optional(request: Request):
        return {"user": optional_current_user(request, settings)}

    FakeClient.response = Response(200, json=None)
    monkeypatch.setattr("podcast_editor.auth.httpx.Client", FakeClient)
    response = TestClient(app).get("/optional")

    assert response.status_code == 200
    assert response.json() == {"user": None}


def test_optional_current_user_does_not_silently_ignore_auth_outage(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_dir=tmp_path, better_auth_url="https://example.test")
    app = FastAPI()

    @app.get("/optional")
    def optional(request: Request):
        return {"user": optional_current_user(request, settings)}

    FakeClient.response = Response(500, json={"error": "database unavailable"})
    monkeypatch.setattr("podcast_editor.auth.httpx.Client", FakeClient)

    response = TestClient(app).get("/optional")

    assert response.status_code == 503


def test_personal_feed_token_is_stable_and_user_specific(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, better_auth_secret="secret-value-that-is-long-enough")
    assert personal_feed_token("user-1", settings) == personal_feed_token("user-1", settings)
    assert personal_feed_token("user-1", settings) != personal_feed_token("user-2", settings)


def test_personal_feed_token_override_is_case_insensitive(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, better_auth_secret="test-secret")

    assert personal_feed_token_for_user(
        {"id": "current-user-id", "email": "OSAMET67@GMAIL.COM"}, settings
    ) == "XZcZIbk48mC7uNs55thzlygcbSN6VnL7KvxK0DrNzuI"


def test_personal_feed_token_for_other_user_uses_user_id(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path, better_auth_secret="test-secret")
    user = {"id": "user-1", "email": "listener@example.com"}

    assert personal_feed_token_for_user(user, settings) == personal_feed_token(
        "user-1", settings
    )
