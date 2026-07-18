import json
from pathlib import Path

from podcast_editor.main import app


def test_review_page_rewrite_does_not_intercept_review_submission() -> None:
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))

    assert all(rewrite["source"] != "/jobs/:job_id/review" for rewrite in config["rewrites"])
    review_routes = [route for route in app.routes if route.path == "/jobs/{job_id}/review"]
    assert any("GET" in route.methods for route in review_routes)
    assert any("POST" in route.methods for route in review_routes)
