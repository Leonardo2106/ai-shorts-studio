from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field

from app.media.schemas import MediaResponse


class ProjectStage(enum.StrEnum):
    EMPTY = "EMPTY"
    MEDIA_PARTIAL = "MEDIA_PARTIAL"
    MEDIA_READY = "MEDIA_READY"
    TRANSCRIBED = "TRANSCRIBED"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectSyncUpdate(BaseModel):
    webcam_offset_ms: int = Field(ge=-86_400_000, le=86_400_000)


class ProjectResponse(BaseModel):
    id: str
    name: str
    stage: ProjectStage
    webcam_offset_ms: int
    created_at: datetime
    updated_at: datetime
    media: list[MediaResponse]


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
