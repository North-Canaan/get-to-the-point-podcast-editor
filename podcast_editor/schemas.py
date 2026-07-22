from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class JobStatus(StrEnum):
    queued = "queued"
    ingesting = "ingesting"
    transcribing = "transcribing"
    detecting_highlights = "detecting_highlights"
    needs_review = "needs_review"
    splicing = "splicing"
    done = "done"
    error = "error"


class CreateJobRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    title: str | None = Field(default=None, max_length=300)
    language: str = Field(default="en", pattern=r"^[a-z]{2,3}$")


class CreateJobResponse(BaseModel):
    job_id: str


class FeedRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class FeedEpisode(BaseModel):
    title: str = Field(max_length=300)
    audio_url: str = Field(max_length=2048)
    published: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    duration: str | None = Field(default=None, max_length=100)
    language: str


class FeedEpisodesResponse(BaseModel):
    title: str = Field(max_length=300)
    language: str
    episodes: list[FeedEpisode] = Field(max_length=500)


class SavedFeed(BaseModel):
    url: str = Field(max_length=2048)
    title: str = Field(max_length=300)
    episode_count: int = 0
    updated_at: str | None = None


class FeedLibraryResponse(BaseModel):
    feeds: list[SavedFeed]


class AutomaticRecipe(BaseModel):
    target_minutes: Literal[15, 30, 45, 60] = 30
    topics: list[str] = Field(default_factory=list, max_length=100)
    minimum_score: int = Field(default=7, ge=1, le=10)
    transition_seconds: Literal[0, 0.5, 1] = 0.5
    start_policy: Literal["future_only", "newest_once"] = "future_only"

    @field_validator("topics")
    @classmethod
    def valid_topics(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(topic.strip() for topic in value if topic.strip()))
        if any(len(topic) > 160 for topic in normalized):
            raise ValueError("topics must be at most 160 characters")
        return normalized


class CreateSubscriptionRequest(BaseModel):
    feed_url: str = Field(min_length=8, max_length=2048)
    recipe: AutomaticRecipe = Field(default_factory=AutomaticRecipe)


class UpdateSubscriptionRequest(BaseModel):
    status: Literal["active", "paused", "deleted"] | None = None
    recipe: AutomaticRecipe | None = None


class SubscriptionResponse(BaseModel):
    id: str
    status: str
    feed_url: str
    feed_title: str | None = None
    recipe: AutomaticRecipe
    start_after: str
    created_at: str


class StatusRecord(BaseModel):
    job_id: str
    status: JobStatus
    error: str | None = None


class TranscriptSegment(BaseModel):
    id: int
    start: float
    end: float
    speaker: str
    text: str


class Transcript(BaseModel):
    duration: float
    segments: list[TranscriptSegment]


Role = Literal["host", "guest", "other"]


class HighlightCandidate(BaseModel):
    id: str
    start: float
    end: float
    speaker: str
    topic: str = "Other"
    reason: str
    score: int = Field(ge=1, le=10)
    text: str = ""


class Highlights(BaseModel):
    roles: dict[str, Role]
    topics: list[str] = Field(default_factory=list)
    selection: dict = Field(default_factory=dict)
    highlights: list[HighlightCandidate]


class HighlightSelectionRequest(BaseModel):
    topic: str | None = Field(default=None, max_length=160)
    target_minutes: int = Field(default=15, ge=1, le=120)


class PrivateFeedRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class ClaimAnonymousFeedRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class CompleteOutputRequest(BaseModel):
    size_bytes: int = Field(ge=1, le=1_000_000_000)


class StateResponse(BaseModel):
    job_id: str
    status: JobStatus
    error: str | None = None
    created_at: str | None = None
    status_updated_at: str | None = None
    email_delivery_available: bool = False
    episode_title: str | None = None
    transcript: Transcript | None = None
    highlights: Highlights | None = None


class ReviewSegment(BaseModel):
    start: float = Field(ge=0, le=86_400)
    end: float = Field(gt=0, le=86_400)

    @field_validator("end")
    @classmethod
    def end_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("end must be positive")
        return value


class ReviewRequest(BaseModel):
    ordered_segments: list[ReviewSegment] = Field(min_length=1, max_length=500)
    transition_seconds: float = Field(default=0.5, ge=0, le=2)

    @field_validator("ordered_segments")
    @classmethod
    def must_approve_at_least_one(cls, value: list[ReviewSegment]) -> list[ReviewSegment]:
        if not value:
            raise ValueError("at least one segment is required")
        for segment in value:
            if segment.end <= segment.start:
                raise ValueError("segment end must be greater than start")
        return value
