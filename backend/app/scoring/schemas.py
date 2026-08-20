from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class ScoreRule(BaseModel):
    model_config = {"extra": "forbid"}
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    weight: float = Field(ge=-10, le=10)
    enabled: bool = True


class ScoreProfileCreate(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=120)
    rules: list[ScoreRule] = Field(min_length=1, max_length=50)


class ScoreProfileResponse(BaseModel):
    id: str
    project_id: str | None
    name: str
    rules: list[ScoreRule]
    is_default: bool
    created_at: datetime
    updated_at: datetime


class ScoreProfilesResponse(BaseModel):
    items: list[ScoreProfileResponse]


class ScoreCandidatesRequest(BaseModel):
    model_config = {"extra": "forbid"}
    transcript_id: str = Field(min_length=1, max_length=100)
    candidate_ids: list[str] | None = Field(default=None, min_length=1, max_length=500)
    profile_id: str | None = None
    top_n: int = Field(default=10, ge=1, le=100)
    max_overlap_ratio: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def unique_candidates(self) -> ScoreCandidatesRequest:
        if self.candidate_ids is not None and len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("candidate_ids must be unique")
        return self


DEFAULT_RULES = [
    ScoreRule(key="duration_fit", label="Duration fit", weight=1.5),
    ScoreRule(key="context_completeness", label="Context completeness", weight=1.5),
    ScoreRule(key="exclamation", label="Exclamation", weight=0.5),
    ScoreRule(key="surprise_language", label="Surprise language", weight=0.75),
    ScoreRule(key="laughter_marker", label="Laughter marker", weight=0.5),
    ScoreRule(key="motion_intensity", label="Visual motion intensity", weight=0.4),
    ScoreRule(key="face_present_ratio", label="Face present", weight=0.3),
    ScoreRule(key="dead_air_penalty", label="Dead-air penalty", weight=-1.0),
]
