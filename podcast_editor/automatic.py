"""Pure rules shared by the automatic-feed API and Modal workers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SELECTION_POLICY_VERSION = 1
ANALYSIS_VERSION = 1


def normalize_url(value: str) -> str:
    """Return a stable public HTTP URL identity without changing resource meaning."""
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("a public HTTP(S) URL is required")
    hostname = parsed.hostname.casefold().rstrip(".")
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        hostname = f"{hostname}:{port}"
    path = parsed.path or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((scheme, hostname, path, query, ""))


def source_episode_identity(feed_url: str, guid: str | None, enclosure_url: str) -> str:
    normalized_feed = normalize_url(feed_url)
    clean_guid = (guid or "").strip()
    identity = f"guid:{clean_guid}" if clean_guid else f"enclosure:{normalize_url(enclosure_url)}"
    return sha256(f"{normalized_feed}\n{identity}".encode()).hexdigest()


@dataclass(frozen=True)
class Recipe:
    target_minutes: int = 30
    topics: tuple[str, ...] = ()
    minimum_score: int = 7
    transition_seconds: float = 0.5
    start_policy: str = "future_only"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Recipe":
        target = int(payload.get("target_minutes", 30))
        minimum_score = int(payload.get("minimum_score", 7))
        transition = float(payload.get("transition_seconds", 0.5))
        start_policy = str(payload.get("start_policy", "future_only"))
        topics = tuple(dict.fromkeys(str(item).strip() for item in payload.get("topics", []) if str(item).strip()))
        if target not in {15, 30, 45, 60}:
            raise ValueError("target_minutes must be 15, 30, 45, or 60")
        if not 1 <= minimum_score <= 10:
            raise ValueError("minimum_score must be between 1 and 10")
        if transition not in {0.0, 0.5, 1.0}:
            raise ValueError("transition_seconds must be 0, 0.5, or 1")
        if start_policy not in {"future_only", "newest_once"}:
            raise ValueError("start_policy must be future_only or newest_once")
        if len(topics) > 100 or any(len(topic) > 160 for topic in topics):
            raise ValueError("topics exceed the recipe limits")
        return cls(target, topics, minimum_score, transition, start_policy)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_minutes": self.target_minutes,
            "topics": list(self.topics),
            "minimum_score": self.minimum_score,
            "transition_seconds": self.transition_seconds,
            "start_policy": self.start_policy,
        }


def select_highlights(highlights: Iterable[dict[str, Any]], recipe: Recipe) -> list[dict[str, Any]]:
    """Apply the V1 score-first policy and return complete clips chronologically."""
    candidates: list[dict[str, Any]] = []
    allowed_topics = set(recipe.topics)
    for position, raw in enumerate(highlights):
        try:
            item = dict(raw)
            item["start"] = float(item["start"])
            item["end"] = float(item["end"])
            item["score"] = int(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if item["start"] < 0 or item["end"] <= item["start"] or item["score"] < recipe.minimum_score:
            continue
        if allowed_topics and str(item.get("topic", "")) not in allowed_topics:
            continue
        item["_editorial_position"] = position
        candidates.append(item)

    candidates.sort(key=lambda item: (-item["score"], item["_editorial_position"]))
    selected: list[dict[str, Any]] = []
    selected_seconds = 0.0
    target_seconds = recipe.target_minutes * 60
    for candidate in candidates:
        if any(_substantial_overlap(candidate, existing) for existing in selected):
            continue
        selected.append(candidate)
        selected_seconds += candidate["end"] - candidate["start"]
        if selected_seconds >= target_seconds:
            break
    selected.sort(key=lambda item: (item["start"], item["end"]))
    for item in selected:
        item.pop("_editorial_position", None)
    return selected


def expected_output_seconds(selected: list[dict[str, Any]], transition_seconds: float) -> float:
    clip_seconds = sum(float(item["end"]) - float(item["start"]) for item in selected)
    return clip_seconds + max(0, len(selected) - 1) * transition_seconds


def _substantial_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    overlap = min(left["end"], right["end"]) - max(left["start"], right["start"])
    if overlap <= 0:
        return False
    shorter = min(left["end"] - left["start"], right["end"] - right["start"])
    return overlap >= min(10.0, shorter * 0.2)
