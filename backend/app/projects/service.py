from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import MediaRole, ProjectModel, TranscriptModel
from app.media.schemas import media_response
from app.projects.schemas import ProjectResponse, ProjectStage


def get_project(session: Session, project_id: str) -> ProjectModel:
    try:
        normalized = str(uuid.UUID(project_id))
    except ValueError as exc:
        raise AppError("INVALID_PROJECT_ID", "Project id must be a UUID.", status_code=422) from exc
    project = session.get(ProjectModel, normalized)
    if project is None:
        raise AppError("PROJECT_NOT_FOUND", "Project was not found.", status_code=404)
    return project


def project_response(session: Session, project: ProjectModel, api_prefix: str) -> ProjectResponse:
    roles = {item.role for item in project.media}
    transcript_count = session.scalar(
        select(func.count(TranscriptModel.id)).where(TranscriptModel.project_id == project.id)
    )
    if transcript_count:
        stage = ProjectStage.TRANSCRIBED
    elif roles == {MediaRole.SCREEN, MediaRole.WEBCAM}:
        stage = ProjectStage.MEDIA_READY
    elif roles:
        stage = ProjectStage.MEDIA_PARTIAL
    else:
        stage = ProjectStage.EMPTY
    return ProjectResponse(
        id=project.id,
        name=project.name,
        stage=stage,
        webcam_offset_ms=project.webcam_offset_ms,
        created_at=project.created_at,
        updated_at=project.updated_at,
        media=[media_response(item, api_prefix) for item in sorted(project.media, key=lambda x: x.role.value)],
    )


def validate_sync_overlap(project: ProjectModel, offset_ms: int) -> None:
    by_role = {item.role: item for item in project.media}
    if set(by_role) != {MediaRole.SCREEN, MediaRole.WEBCAM}:
        raise AppError(
            "MEDIA_NOT_READY",
            "Both screen and webcam media are required before setting synchronization.",
            status_code=409,
        )
    screen_duration = int(by_role[MediaRole.SCREEN].probe_data["duration_ms"])
    webcam_duration = int(by_role[MediaRole.WEBCAM].probe_data["duration_ms"])
    overlap = min(screen_duration, offset_ms + webcam_duration) - max(0, offset_ms)
    if overlap <= 0:
        raise AppError(
            "SYNC_NO_OVERLAP",
            "The synchronization offset leaves no overlap between the media sources.",
            status_code=422,
            details={
                "screen_duration_ms": screen_duration,
                "webcam_duration_ms": webcam_duration,
            },
        )
