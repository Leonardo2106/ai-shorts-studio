from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.db.models import JobStatus


class JobError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class JobResponse(BaseModel):
    id: str
    project_id: str
    kind: str
    status: JobStatus
    progress: float
    result: dict[str, Any] | None
    error: JobError | None
    cancellation_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobListResponse(BaseModel):
    items: list[JobResponse]
