from __future__ import annotations

import enum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator


class AIProvider(enum.StrEnum):
    OPENAI = "OPENAI"
    GEMINI = "GEMINI"
    GROQ = "GROQ"


class SemanticMetrics(BaseModel):
    model_config = {"extra": "forbid"}
    hook: float = Field(ge=0, le=1)
    humor: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    context_completeness: float = Field(ge=0, le=1)
    standalone_quality: float = Field(ge=0, le=1)
    information_value: float = Field(ge=0, le=1)
    narrative_progression: float = Field(ge=0, le=1)
    dead_air_penalty: float = Field(ge=0, le=1)
    recommended_start_ms: int = Field(ge=0)
    recommended_end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_range(self) -> SemanticMetrics:
        if self.recommended_end_ms <= self.recommended_start_ms:
            raise ValueError("recommended timestamps must form a positive range")
        return self


class CandidateSemanticResult(BaseModel):
    model_config = {"extra": "forbid"}

    candidate_id: str = Field(min_length=1, max_length=100)
    metrics: SemanticMetrics
    reasons: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(default_factory=list, max_length=20)
    effective_provider: AIProvider | None = None
    effective_model: str | None = Field(default=None, max_length=120)


class SemanticAnalysisRequest(BaseModel):
    model_config = {"extra": "forbid"}
    provider: AIProvider
    model: str = Field(min_length=1, max_length=120)
    candidate_ids: list[str] = Field(min_length=1, max_length=100)
    opt_in_external_processing: bool
    max_output_tokens: int = Field(default=2048, ge=128, le=32768)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    reasoning_effort: str | None = Field(default=None, pattern=r"^(low|medium|high)$")
    timeout_seconds: float = Field(default=30, ge=1, le=120)
    retries: int = Field(default=1, ge=0, le=3)
    fallback_provider: AIProvider | None = None
    chunk_char_limit: int = Field(default=6000, ge=500, le=20000)

    @model_validator(mode="after")
    def unique_candidates(self) -> SemanticAnalysisRequest:
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("candidate_ids must be unique")
        return self


class ProviderCapability(BaseModel):
    provider: AIProvider
    configured: bool
    models: list[str]
    parameters: list[str]


class AnalysisEstimate(BaseModel):
    chunks: int
    estimated_input_tokens: int
    candidates: int
