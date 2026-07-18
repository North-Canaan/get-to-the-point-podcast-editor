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
    url: str = Field(min_length=1)
    title: str | None = None
    language: str = Field(default="en", pattern=r"^[a-z]{2,3}$")


class CreateJobResponse(BaseModel):
    job_id: str


class FeedRequest(BaseModel):
    url: str = Field(min_length=1)


class FeedEpisode(BaseModel):
    title: str
    audio_url: str
    published: str | None = None
    description: str | None = None
    duration: str | None = None
    language: str


class FeedEpisodesResponse(BaseModel):
    title: str
    language: str
    episodes: list[FeedEpisode]


class SavedFeed(BaseModel):
    url: str
    title: str
    episode_count: int = 0
    updated_at: str | None = None


class FeedLibraryResponse(BaseModel):
    feeds: list[SavedFeed]


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


class CompleteOutputRequest(BaseModel):
    size_bytes: int = Field(ge=1)


class StateResponse(BaseModel):
    job_id: str
    status: JobStatus
    error: str | None = None
    episode_title: str | None = None
    transcript: Transcript | None = None
    highlights: Highlights | None = None


class ReviewSegment(BaseModel):
    start: float
    end: float

    @field_validator("end")
    @classmethod
    def end_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("end must be positive")
        return value


class ReviewRequest(BaseModel):
    ordered_segments: list[ReviewSegment]

    @field_validator("ordered_segments")
    @classmethod
    def must_approve_at_least_one(cls, value: list[ReviewSegment]) -> list[ReviewSegment]:
        if not value:
            raise ValueError("at least one segment is required")
        for segment in value:
            if segment.end <= segment.start:
                raise ValueError("segment end must be greater than start")
        return value
