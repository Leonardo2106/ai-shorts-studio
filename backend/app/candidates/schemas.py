from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class CandidateGenerationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    transcript_id: str
    min_duration_ms: int = Field(default=12_000, ge=3_000, le=60_000)
    ideal_min_ms: int = Field(default=25_000, ge=3_000, le=60_000)
    ideal_max_ms: int = Field(default=45_000, ge=3_000, le=60_000)
    max_duration_ms: int = Field(default=60_000, ge=3_000, le=60_000)
    pre_roll_ms: int = Field(default=500, ge=0, le=5_000)
    post_roll_ms: int = Field(default=750, ge=0, le=5_000)
    top_n: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def valid_durations(self) -> CandidateGenerationRequest:
        if not self.min_duration_ms <= self.ideal_min_ms <= self.ideal_max_ms <= self.max_duration_ms:
            raise ValueError("duration bounds must satisfy min <= ideal_min <= ideal_max <= max")
        return self


class CandidateUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    status: str | None = Field(default=None, pattern=r"^(PENDING|ACCEPTED|REJECTED)$")

    @model_validator(mode="after")
    def valid_range(self) -> CandidateUpdate:
        if self.start_ms is not None and self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        if self.start_ms is None and self.end_ms is None and self.status is None:
            raise ValueError("at least one field must be supplied")
        return self


class CandidateResponse(BaseModel):
    id: str
    project_id: str
    transcript_id: str
    schema_version: int
    start_ms: int
    end_ms: int
    title: str
    status: str
    text: str
    origin: str
    local_features: dict[str, Any]
    reasons: list[str]
    context: dict[str, Any]
    signals: dict[str, Any]
    score: float | None
    score_breakdown: list[dict[str, Any]] | None
    created_at: datetime
    updated_at: datetime


class CandidateListResponse(BaseModel):
    items: list[CandidateResponse]
