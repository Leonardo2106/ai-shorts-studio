from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.api.dependencies import get_session
from app.core.errors import AppError
from app.db.models import JobModel, MediaModel, MediaRole, ProjectModel, TranscriptModel
from app.jobs.schemas import JobError, JobResponse
from app.media.importer import safe_original_filename
from app.media.schemas import MediaResponse, media_response
from app.projects.schemas import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectSyncUpdate,
)
from app.projects.service import get_project, project_response, validate_sync_overlap
from app.projects.storage import ProjectStorage
from app.transcription.schemas import TranscriptDocument, TranscriptionRequest

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


def _job_response(job: JobModel) -> JobResponse:
    error = None
    if job.error_code and job.error_message:
        error = JobError(code=job.error_code, message=job.error_message)
    return JobResponse(
        id=job.id,
        project_id=job.project_id,
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        result=job.result_data,
        error=error,
        cancellation_requested=job.cancellation_requested,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    result: dict[str, Any] = request.app.state.capabilities
    return result


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreate, request: Request, session: SessionDep) -> ProjectResponse:
    project = ProjectModel(id=str(uuid.uuid4()), name=body.name.strip())
    if not project.name:
        raise AppError("INVALID_PROJECT_NAME", "Project name cannot be blank.", status_code=422)
    session.add(project)
    try:
        request.app.state.storage.project_dir(project.id, create=True)
        session.commit()
        session.refresh(project)
    except Exception:
        session.rollback()
        raise
    return project_response(session, project, request.app.state.settings.api_prefix)


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(request: Request, session: SessionDep) -> ProjectListResponse:
    projects = session.scalars(select(ProjectModel).order_by(ProjectModel.created_at.desc())).all()
    return ProjectListResponse(
        items=[project_response(session, project, request.app.state.settings.api_prefix) for project in projects]
    )


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def open_project(project_id: str, request: Request, session: SessionDep) -> ProjectResponse:
    return project_response(session, get_project(session, project_id), request.app.state.settings.api_prefix)


@router.patch("/projects/{project_id}/sync", response_model=ProjectResponse)
async def update_sync(
    project_id: str,
    body: ProjectSyncUpdate,
    request: Request,
    session: SessionDep,
) -> ProjectResponse:
    project = get_project(session, project_id)
    validate_sync_overlap(project, body.webcam_offset_ms)
    project.webcam_offset_ms = body.webcam_offset_ms
    session.commit()
    session.refresh(project)
    return project_response(session, project, request.app.state.settings.api_prefix)


@router.post("/projects/{project_id}/media", response_model=MediaResponse, status_code=201)
async def upload_media(
    project_id: str,
    request: Request,
    role: Annotated[MediaRole, Form()],
    file: Annotated[UploadFile, File()],
    session: SessionDep,
) -> MediaResponse:
    project = get_project(session, project_id)
    async with request.app.state.importer.reserve(project.id, role):
        existing = session.scalar(
            select(MediaModel).where(MediaModel.project_id == project.id, MediaModel.role == role)
        )
        if existing is not None:
            raise AppError(
                "MEDIA_ROLE_EXISTS",
                "This media role is already associated with the project.",
                status_code=409,
            )
        imported = await request.app.state.importer.import_upload(project.id, role, file, reserved=True)
        existing = MediaModel(project_id=project.id, role=role)
        session.add(existing)
        existing.relative_path = request.app.state.storage.relative(imported.path)
        existing.original_filename = safe_original_filename(file.filename, f"{role.value.lower()}.mp4")
        existing.size_bytes = imported.size_bytes
        existing.sha256 = imported.sha256
        existing.probe_data = imported.probe.model_dump(mode="json")
        session.commit()
        session.refresh(existing)
    return media_response(existing, request.app.state.settings.api_prefix)


def _parse_range(value: str, size: int) -> tuple[int, int]:
    if not value.startswith("bytes=") or "," in value:
        raise AppError("INVALID_RANGE", "Only a single byte range is supported.", status_code=416)
    start_text, separator, end_text = value[6:].partition("-")
    if not separator:
        raise AppError("INVALID_RANGE", "Invalid byte range.", status_code=416)
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                raise ValueError
            start, end = max(0, size - suffix), size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else size - 1
    except ValueError as exc:
        raise AppError("INVALID_RANGE", "Invalid byte range.", status_code=416) from exc
    if start < 0 or start >= size or end < start:
        raise AppError("RANGE_NOT_SATISFIABLE", "Byte range is not satisfiable.", status_code=416)
    return start, min(end, size - 1)


async def _file_chunk(storage: ProjectStorage, path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    with storage.open_binary(path) as source:
        source.seek(start)
        remaining = length
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/projects/{project_id}/media/{media_id}/content")
async def media_content(
    project_id: str,
    media_id: str,
    request: Request,
    session: SessionDep,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    project = get_project(session, project_id)
    media = session.scalar(select(MediaModel).where(MediaModel.id == media_id, MediaModel.project_id == project.id))
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "Media was not found.", status_code=404)
    path = request.app.state.storage.project_path(project.id, Path(media.relative_path).name)
    if not path.is_file():
        raise AppError("MEDIA_FILE_MISSING", "Media file is missing from storage.", status_code=404)
    size = path.stat().st_size
    common_headers = {"Accept-Ranges": "bytes", "Content-Disposition": "inline"}
    if range_header is None:
        return StreamingResponse(
            _file_chunk(request.app.state.storage, path, 0, size),
            media_type="video/mp4",
            headers={**common_headers, "Content-Length": str(size)},
        )
    start, end = _parse_range(range_header, size)
    headers = {
        **common_headers,
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Content-Length": str(end - start + 1),
    }
    return StreamingResponse(
        _file_chunk(request.app.state.storage, path, start, end - start + 1),
        status_code=206,
        media_type="video/mp4",
        headers=headers,
    )


@router.post("/projects/{project_id}/transcription-jobs", response_model=JobResponse, status_code=202)
async def start_transcription(
    project_id: str,
    body: TranscriptionRequest,
    request: Request,
    session: SessionDep,
) -> JobResponse:
    project = get_project(session, project_id)
    media = session.scalar(
        select(MediaModel).where(MediaModel.id == body.media_id, MediaModel.project_id == project.id)
    )
    if media is None:
        raise AppError("MEDIA_NOT_FOUND", "Media was not found.", status_code=404)
    available = {item.get("index") for item in media.probe_data.get("audio_streams", [])}
    if body.audio_stream_index not in available:
        raise AppError(
            "AUDIO_STREAM_NOT_FOUND",
            "Selected audio stream does not exist in this media.",
            status_code=422,
            details={"available_indexes": sorted(i for i in available if i is not None)},
        )
    request.app.state.transcription.ensure_available()
    job, created = request.app.state.job_runner.create_transcription_job(project.id, body.model_dump(mode="json"))
    response = _job_response(job)
    if created:
        request.app.state.job_runner.submit_transcription(job.id)
    return response


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, session: SessionDep) -> JobResponse:
    job = session.get(JobModel, job_id)
    if job is None:
        raise AppError("JOB_NOT_FOUND", "Job was not found.", status_code=404)
    return _job_response(job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: str, request: Request) -> JobResponse:
    return _job_response(request.app.state.job_runner.cancel(job_id))


@router.get("/projects/{project_id}/transcripts/{transcript_id}", response_model=TranscriptDocument)
async def get_transcript(
    project_id: str,
    transcript_id: str,
    request: Request,
    session: SessionDep,
) -> TranscriptDocument:
    project = get_project(session, project_id)
    transcript = session.scalar(
        select(TranscriptModel).where(TranscriptModel.id == transcript_id, TranscriptModel.project_id == project.id)
    )
    if transcript is None:
        raise AppError("TRANSCRIPT_NOT_FOUND", "Transcript was not found.", status_code=404)
    path = request.app.state.storage.project_path(project.id, Path("transcripts") / Path(transcript.relative_path).name)
    try:
        with request.app.state.storage.open_binary(path) as source:
            return TranscriptDocument.model_validate(json.loads(source.read().decode("utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise AppError("TRANSCRIPT_UNAVAILABLE", "Stored transcript is unavailable.", status_code=500) from exc
