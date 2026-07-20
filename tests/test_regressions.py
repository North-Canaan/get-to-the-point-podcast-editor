from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

import podcast_editor.main as main_module
from podcast_editor.auth import personal_feed_token
from podcast_editor.config import Settings
from podcast_editor.jobs import JobStore, new_job_id
from podcast_editor.main import app
from podcast_editor.schemas import JobStatus


def completed_job(store: JobStore, title: str = "Regression episode") -> str:
    job_id = new_job_id()
    store.write_json(job_id, "input", {"episode_title": title})
    store.artifact_path(job_id, "output").write_bytes(b"edited-audio")
    store.set_status(job_id, JobStatus.done)
    return job_id


def test_output_confirmation_deletes_oversized_upload(monkeypatch, tmp_path: Path) -> None:
    test_settings = Settings(data_dir=tmp_path, state_backend="filesystem", max_output_bytes=10)
    test_store = JobStore(test_settings)
    job_id = new_job_id()
    test_store.set_status(job_id, JobStatus.splicing)
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(test_store, "artifact_size", lambda *_args: 11)
    monkeypatch.setattr(test_store, "delete_media", lambda *args: deleted.append(args))

    response = TestClient(app).post(
        f"/jobs/{job_id}/output-complete", json={"size_bytes": 11}
    )

    assert response.status_code == 413
    assert deleted == [(job_id, "output.mp3")]
    assert test_store.get_status(job_id).status == JobStatus.splicing


def test_output_confirmation_deletes_size_mismatch(monkeypatch, tmp_path: Path) -> None:
    test_settings = Settings(data_dir=tmp_path, state_backend="filesystem")
    test_store = JobStore(test_settings)
    job_id = new_job_id()
    test_store.set_status(job_id, JobStatus.splicing)
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(test_store, "artifact_size", lambda *_args: 200)
    monkeypatch.setattr(test_store, "delete_media", lambda *args: deleted.append(args))

    response = TestClient(app).post(
        f"/jobs/{job_id}/output-complete", json={"size_bytes": 199}
    )

    assert response.status_code == 409
    assert deleted == [(job_id, "output.mp3")]
    assert test_store.get_status(job_id).status == JobStatus.splicing


def test_output_confirmation_marks_matching_upload_done(monkeypatch, tmp_path: Path) -> None:
    test_settings = Settings(data_dir=tmp_path, state_backend="filesystem")
    test_store = JobStore(test_settings)
    job_id = new_job_id()
    test_store.set_status(job_id, JobStatus.splicing)
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(test_store, "artifact_size", lambda *_args: 200)

    response = TestClient(app).post(
        f"/jobs/{job_id}/output-complete", json={"size_bytes": 200}
    )

    assert response.status_code == 200
    assert test_store.get_status(job_id).status == JobStatus.done


def test_review_rejects_zero_and_reversed_ranges(monkeypatch, tmp_path: Path) -> None:
    test_store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    job_id = new_job_id()
    test_store.set_status(job_id, JobStatus.needs_review)
    monkeypatch.setattr(main_module, "store", test_store)
    client = TestClient(app)

    zero = client.post(
        f"/jobs/{job_id}/review", json={"ordered_segments": [{"start": 5, "end": 5}]}
    )
    reversed_range = client.post(
        f"/jobs/{job_id}/review", json={"ordered_segments": [{"start": 8, "end": 2}]}
    )

    assert zero.status_code == 422
    assert reversed_range.status_code == 422
    assert test_store.get_status(job_id).status == JobStatus.needs_review


def test_review_persists_transition_duration(monkeypatch, tmp_path: Path) -> None:
    test_store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    job_id = new_job_id()
    test_store.set_status(job_id, JobStatus.needs_review)
    monkeypatch.setattr(main_module, "store", test_store)

    response = TestClient(app).post(
        f"/jobs/{job_id}/review",
        json={
            "ordered_segments": [{"start": 5, "end": 15}],
            "transition_seconds": 1,
        },
    )

    assert response.status_code == 200
    assert test_store.read_json(job_id, "review")["transition_seconds"] == 1


def test_review_rejects_transition_longer_than_ui_limit(monkeypatch, tmp_path: Path) -> None:
    test_store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    job_id = new_job_id()
    test_store.set_status(job_id, JobStatus.needs_review)
    monkeypatch.setattr(main_module, "store", test_store)

    response = TestClient(app).post(
        f"/jobs/{job_id}/review",
        json={
            "ordered_segments": [{"start": 5, "end": 15}],
            "transition_seconds": 3,
        },
    )

    assert response.status_code == 422
    assert test_store.get_status(job_id).status == JobStatus.needs_review


def test_auth_outage_does_not_attach_episode_to_anonymous_feed(monkeypatch, tmp_path: Path) -> None:
    test_store = JobStore(Settings(data_dir=tmp_path, state_backend="filesystem"))
    job_id = completed_job(test_store)
    token = "anonymous_feed_token_abcdefghijklmnopqrstuvwxyz"
    attached: list[tuple] = []
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(
        main_module,
        "optional_current_user",
        lambda *_args: (_ for _ in ()).throw(HTTPException(status_code=503, detail="auth down")),
    )
    monkeypatch.setattr(test_store, "add_private_feed_item", lambda *args: attached.append(args))

    response = TestClient(app).post(
        f"/jobs/{job_id}/private-feed", json={"token": token}
    )

    assert response.status_code == 503
    assert attached == []


def test_signed_in_feed_ignores_browser_token(monkeypatch, tmp_path: Path) -> None:
    test_settings = Settings(
        data_dir=tmp_path,
        state_backend="filesystem",
        better_auth_secret="regression-secret",
    )
    test_store = JobStore(test_settings)
    job_id = completed_job(test_store)
    browser_token = "browser_supplied_token_abcdefghijklmnopqrstuvwxyz"
    expected_token = personal_feed_token("user-1", test_settings)
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(
        main_module,
        "optional_current_user",
        lambda *_args: {"id": "user-1", "email": "listener@example.com"},
    )

    response = TestClient(app).post(
        f"/jobs/{job_id}/private-feed", json={"token": browser_token}
    )

    assert response.status_code == 200
    assert response.json()["feed_url"] == f"/private-feed/{expected_token}.xml"
    assert test_store.list_private_feed_items(expected_token)
    assert test_store.list_private_feed_items(browser_token) is None


def test_signed_in_user_can_claim_anonymous_feed(monkeypatch, tmp_path: Path) -> None:
    test_settings = Settings(
        data_dir=tmp_path,
        state_backend="filesystem",
        better_auth_secret="claim-test-secret",
    )
    test_store = JobStore(test_settings)
    anonymous_token = "anonymous_feed_token_abcdefghijklmnopqrstuvwxyz"
    job_id = new_job_id()
    test_store.add_private_feed_item(anonymous_token, job_id, "Earlier edit", 123)
    account_token = personal_feed_token("user-1", test_settings)
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(
        main_module,
        "current_user",
        lambda *_args: {"id": "user-1", "email": "listener@example.com"},
    )

    response = TestClient(app).post(
        "/me/claim-anonymous-feed", json={"token": anonymous_token}
    )

    assert response.status_code == 200
    assert response.json()["claimed_episodes"] == 1
    assert response.json()["feed_url"] == f"/private-feed/{account_token}.xml"
    assert test_store.private_feed_contains(account_token, job_id)
    assert test_store.list_private_feed_items(anonymous_token) is None


def test_account_page_migrates_derived_feed_to_email_override(
    monkeypatch, tmp_path: Path
) -> None:
    test_settings = Settings(
        data_dir=tmp_path,
        state_backend="filesystem",
        better_auth_secret="account-migration-secret",
    )
    test_store = JobStore(test_settings)
    user = {"id": "replacement-user-id", "email": "osamet67@gmail.com"}
    derived_token = personal_feed_token(user["id"], test_settings)
    preferred_token = "XZcZIbk48mC7uNs55thzlygcbSN6VnL7KvxK0DrNzuI"
    job_id = new_job_id()
    test_store.add_private_feed_item(derived_token, job_id, "Existing edit", 123, user["id"])
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(main_module, "current_user", lambda *_args: user)

    response = TestClient(app).get("/me")

    assert response.status_code == 200
    assert response.json()["feed_url"] == f"/private-feed/{preferred_token}.xml"
    assert test_store.private_feed_contains(preferred_token, job_id)
    assert test_store.list_private_feed_items(derived_token) is None


def test_private_feed_revision_changes_guid_and_uses_canonical_origin(
    monkeypatch, tmp_path: Path
) -> None:
    test_settings = Settings(
        data_dir=tmp_path,
        state_backend="filesystem",
        app_base_url="https://podcasts.example.test",
    )
    test_store = JobStore(test_settings)
    first_id = new_job_id()
    revised_id = new_job_id()
    token = "private_feed_token_abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setattr(main_module, "settings", test_settings)
    monkeypatch.setattr(main_module, "store", test_store)
    monkeypatch.setattr(
        test_store,
        "list_private_feed_items",
        lambda _token: [
            {"job_id": first_id, "title": "Original", "size_bytes": 10},
            {
                "job_id": revised_id,
                "title": "Revised",
                "size_bytes": 20,
                "updated_at": "2026-07-19T12:34:56+00:00",
            },
        ],
    )

    response = TestClient(app).get(f"/private-feed/{token}.xml")

    assert response.status_code == 200
    assert f'<guid isPermaLink="false">{first_id}</guid>' in response.text
    assert f'<guid isPermaLink="false">{revised_id}-202607191234560000</guid>' in response.text
    assert "localhost" not in response.text
    assert response.text.count("https://podcasts.example.test/private-feed/") == 3


def test_auth_rate_limit_migration_supplies_generated_ids() -> None:
    migration = Path("supabase/migrations/0010_fix_auth_rate_limit_ids.sql").read_text(
        encoding="utf-8"
    )
    assert 'alter table public."rateLimit"' in migration
    assert "alter column id set default gen_random_uuid()::text" in migration


def test_anonymous_feed_claim_migration_is_atomic_and_reassigns_jobs() -> None:
    migration = Path("supabase/migrations/0011_claim_anonymous_feeds.sql").read_text(
        encoding="utf-8"
    )
    assert "security definer" in migration
    assert "for update" in migration
    assert "on conflict (feed_id, job_id) do update" in migration
    assert "set user_id = account_user_id" in migration
    assert "where job.user_id is null" in migration
    assert "delete from public.private_feeds where id = source_feed.id" in migration
    assert "grant execute" in migration
