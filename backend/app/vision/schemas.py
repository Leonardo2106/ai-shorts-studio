from __future__ import annotations

from pydantic import BaseModel, Field


class VisionAnalysisRequest(BaseModel):
    model_config = {"extra": "forbid"}
    media_id: str
    sample_interval_ms: int = Field(default=2000, ge=250, le=60_000)
    max_samples: int = Field(default=300, ge=1, le=1000)
    max_dimension: int = Field(default=480, ge=120, le=720)
    candidate_ids: list[str] = Field(min_length=1, max_length=100)


class VisionSignals(BaseModel):
    analyzer_version: int = 1
    available: bool
    sample_count: int
    max_dimension: int
    motion_intensity: float = Field(ge=0, le=1)
    face_present_ratio: float | None = Field(default=None, ge=0, le=1)
    note: str | None = None


class VisionAnalysisResult(BaseModel):
    candidate_signals: dict[str, VisionSignals]
    cache_hit: bool = False
