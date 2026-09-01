# ruff: noqa: SIM905 -- the stable code table is easier to audit in canonical Whisper order.
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db.models import TranscriptionPreset

WHISPER_LANGUAGE_CODES = frozenset(
    filter(
        None,
        (
            "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu "
            "ha haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn "
            "mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg "
            "th tk tl tr tt uk ur uz vi yi yo yue zh"
        ).split(" "),
    )
)


class TranscriptionRequest(BaseModel):
    media_id: str
    audio_stream_index: int = Field(ge=0)
    preset: TranscriptionPreset = TranscriptionPreset.BALANCED
    language: str | None = Field(default=None, min_length=2, max_length=16, pattern=r"^[A-Za-z-]+$")
    word_timestamps: bool = False

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: object) -> object:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if normalized not in WHISPER_LANGUAGE_CODES:
            raise ValueError("unsupported faster-whisper language code")
        return normalized


class TranscriptWord(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(max_length=1_000)

    @model_validator(mode="after")
    def valid_range(self) -> TranscriptWord:
        if self.end_ms < self.start_ms:
            raise ValueError("word end must not precede start")
        return self


class TranscriptSegment(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(max_length=20_000)
    words: list[TranscriptWord] | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def valid_range(self) -> TranscriptSegment:
        if self.end_ms < self.start_ms:
            raise ValueError("segment end must not precede start")
        return self


class TranscriptDocument(BaseModel):
    schema_version: int = 1
    id: str
    project_id: str
    media_id: str
    source: str
    audio_stream_index: int
    language: str | None
    duration_ms: int
    engine: str
    model: str
    segments: list[TranscriptSegment] = Field(max_length=50_000)


class TranscriptSummary(BaseModel):
    id: str
    project_id: str
    media_id: str
    language: str | None
    duration_ms: int
    created_at: datetime


class TranscriptListResponse(BaseModel):
    items: list[TranscriptSummary]
