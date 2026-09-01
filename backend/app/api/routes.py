from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.ai.schemas import AnalysisEstimate, SemanticAnalysisRequest
from app.api.dependencies import get_session
from app.candidates.schemas import CandidateGenerationRequest, CandidateListResponse, CandidateResponse, CandidateUpdate
from app.candidates.service import candidate_response, rebuild_candidate_excerpt, validate_candidate_range
from app.core.errors import AppError
from app.db.models import (
    CandidateModel,
    EditConfigModel,
    JobModel,
    JobStatus,
    MediaModel,
    MediaRole,
    ProjectModel,
    ScoreProfileModel,
    TranscriptModel,
)
from app.editor.captions import extract_caption_cues
from app.editor.schemas import (
    CaptionCueList,
    EditConfig,
    EditConfigResponse,
    LayoutPreset,
    normalize_legacy_edit_config,
    preset_config,
)
from app.jobs.schemas import JobError, JobListResponse, JobResponse
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
from app.scoring.schemas import (
    DEFAULT_RULES,
    ScoreCandidatesRequest,
    ScoreProfileCreate,
    ScoreProfileResponse,
    ScoreProfilesResponse,
)
from app.scoring.service import score_and_rank
from app.transcription.schemas import (
    TranscriptDocument,
    TranscriptionRequest,
    TranscriptListResponse,
    TranscriptSummary,
)
from app.vision.schemas import VisionAnalysisRequest

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
JobKindFilter = Literal[
    "TRANSCRIPTION",
    "CANDIDATE_GENERATION",
    "SEMANTIC_ANALYSIS",
    "VISION_ANALYSIS",
    "RENDER_PREVIEW",
    "RENDER_FINAL",
]


def _job_response(job: JobModel) -> JobResponse:
    error = None
    if job.error_code and job.error_message:
        details = None
        if job.result_data and isinstance(job.result_data.get("error_details"), dict):
            details = job.result_data["error_details"]
        error = JobError(code=job.error_code, message=job.error_message, details=details)
    return JobResponse(
        id=job.id,
        project_id=job.project_id,
        kind=job.kind,
        status=job.status,
        progress=job.progress,
        result=None if error is not None else job.result_data,
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
    session.add(
        ScoreProfileModel(
            project_id=project.id,
            name="Default",
            is_default=True,
            rules=[rule.model_dump(mode="json") for rule in DEFAULT_RULES],
        )
    )
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


@router.get("/projects/{project_id}/jobs", response_model=JobListResponse)
async def list_project_jobs(
    project_id: str,
    session: SessionDep,
    kind: JobKindFilter | None = None,
    status: JobStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobListResponse:
    project = get_project(session, project_id)
    statement = select(JobModel).where(JobModel.project_id == project.id)
    if kind is not None:
        statement = statement.where(JobModel.kind == kind)
    if status is not None:
        statement = statement.where(JobModel.status == status)
    jobs = session.scalars(
        statement.order_by(JobModel.created_at.desc(), JobModel.id.desc()).limit(limit)
    ).all()
    return JobListResponse(items=[_job_response(job) for job in jobs])


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(job_id: str, request: Request) -> JobResponse:
    return _job_response(request.app.state.job_runner.cancel(job_id))


@router.get("/projects/{project_id}/transcripts", response_model=TranscriptListResponse)
async def list_transcripts(project_id: str, session: SessionDep) -> TranscriptListResponse:
    project = get_project(session, project_id)
    items = session.scalars(
        select(TranscriptModel)
        .where(TranscriptModel.project_id == project.id)
        .order_by(TranscriptModel.created_at.desc())
    ).all()
    return TranscriptListResponse(
        items=[TranscriptSummary.model_validate(item, from_attributes=True) for item in items]
    )


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


@router.post("/projects/{project_id}/candidate-jobs", response_model=JobResponse, status_code=202)
async def start_candidate_generation(
    project_id: str, body: CandidateGenerationRequest, request: Request, session: SessionDep
) -> JobResponse:
    project = get_project(session, project_id)
    transcript = session.get(TranscriptModel, body.transcript_id)
    if transcript is None or transcript.project_id != project.id:
        raise AppError("TRANSCRIPT_NOT_FOUND", "Transcript was not found.", status_code=404)
    job, created = request.app.state.job_runner.create_job(
        project.id, "CANDIDATE_GENERATION", body.model_dump(mode="json")
    )
    if created:
        request.app.state.job_runner.submit(job.id)
    return _job_response(job)


@router.get("/projects/{project_id}/candidates", response_model=CandidateListResponse)
async def list_candidates(project_id: str, session: SessionDep) -> CandidateListResponse:
    project = get_project(session, project_id)
    items = session.scalars(
        select(CandidateModel)
        .where(CandidateModel.project_id == project.id)
        .order_by(CandidateModel.score.desc(), CandidateModel.start_ms)
    ).all()
    return CandidateListResponse(items=[candidate_response(item) for item in items])


@router.patch("/projects/{project_id}/candidates/{candidate_id}", response_model=CandidateResponse)
async def update_candidate(
    project_id: str, candidate_id: str, body: CandidateUpdate, request: Request, session: SessionDep
) -> CandidateResponse:
    project = get_project(session, project_id)
    candidate = session.get(CandidateModel, candidate_id)
    if candidate is None or candidate.project_id != project.id:
        raise AppError("CANDIDATE_NOT_FOUND", "Candidate was not found.", status_code=404)
    start = body.start_ms if body.start_ms is not None else candidate.start_ms
    end = body.end_ms if body.end_ms is not None else candidate.end_ms
    validate_candidate_range(session, project, start, end)
    range_changed = start != candidate.start_ms or end != candidate.end_ms
    candidate.start_ms, candidate.end_ms = start, end
    if range_changed:
        transcript = session.get(TranscriptModel, candidate.transcript_id)
        if transcript is None:
            raise AppError("TRANSCRIPT_NOT_FOUND", "Transcript was not found.", status_code=404)
        title, reasons, context, signals = rebuild_candidate_excerpt(
            request.app.state.storage, transcript, project, start, end
        )
        candidate.title = title
        candidate.reasons = reasons
        candidate.context = context
        candidate.signals = signals
        candidate.score = None
        candidate.score_breakdown = None
    if body.status is not None:
        candidate.status = body.status
    session.commit()
    session.refresh(candidate)
    return candidate_response(candidate)


@router.post("/projects/{project_id}/semantic-analysis/estimate", response_model=AnalysisEstimate)
async def estimate_semantic_analysis(
    project_id: str, body: SemanticAnalysisRequest, request: Request, session: SessionDep
) -> AnalysisEstimate:
    project = get_project(session, project_id)
    candidates = session.scalars(
        select(CandidateModel).where(CandidateModel.project_id == project.id, CandidateModel.id.in_(body.candidate_ids))
    ).all()
    if len(candidates) != len(set(body.candidate_ids)):
        raise AppError("CANDIDATE_NOT_FOUND", "One or more candidates were not found.", status_code=404)
    return cast(AnalysisEstimate, request.app.state.semantic_analysis.estimate(list(candidates), body))


@router.post("/projects/{project_id}/semantic-analysis-jobs", response_model=JobResponse, status_code=202)
async def start_semantic_analysis(
    project_id: str, body: SemanticAnalysisRequest, request: Request, session: SessionDep
) -> JobResponse:
    project = get_project(session, project_id)
    if not body.opt_in_external_processing:
        raise AppError(
            "EXTERNAL_AI_OPT_IN_REQUIRED", "Explicit opt-in is required for external processing.", status_code=422
        )
    request.app.state.semantic_analysis.validate_request(body)
    job, created = request.app.state.job_runner.create_job(
        project.id, "SEMANTIC_ANALYSIS", body.model_dump(mode="json")
    )
    if created:
        request.app.state.job_runner.submit(job.id)
    return _job_response(job)


@router.post("/projects/{project_id}/vision-jobs", response_model=JobResponse, status_code=202)
async def start_vision_analysis(
    project_id: str, body: VisionAnalysisRequest, request: Request, session: SessionDep
) -> JobResponse:
    project = get_project(session, project_id)
    media = session.get(MediaModel, body.media_id)
    if media is None or media.project_id != project.id:
        raise AppError("MEDIA_NOT_FOUND", "Media was not found.", status_code=404)
    job, created = request.app.state.job_runner.create_job(project.id, "VISION_ANALYSIS", body.model_dump(mode="json"))
    if created:
        request.app.state.job_runner.submit(job.id)
    return _job_response(job)


def _profile_response(profile: ScoreProfileModel) -> ScoreProfileResponse:
    return ScoreProfileResponse.model_validate(profile, from_attributes=True)


@router.get("/projects/{project_id}/score-profiles", response_model=ScoreProfilesResponse)
async def list_score_profiles(project_id: str, session: SessionDep) -> ScoreProfilesResponse:
    project = get_project(session, project_id)
    items = session.scalars(select(ScoreProfileModel).where(ScoreProfileModel.project_id == project.id)).all()
    return ScoreProfilesResponse(items=[_profile_response(item) for item in items])


@router.post("/projects/{project_id}/score-profiles", response_model=ScoreProfileResponse, status_code=201)
async def create_score_profile(project_id: str, body: ScoreProfileCreate, session: SessionDep) -> ScoreProfileResponse:
    project = get_project(session, project_id)
    profile = ScoreProfileModel(
        project_id=project.id, name=body.name.strip(), rules=[rule.model_dump(mode="json") for rule in body.rules]
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _profile_response(profile)


@router.put("/projects/{project_id}/score-profiles/{profile_id}", response_model=ScoreProfileResponse)
async def update_score_profile(
    project_id: str, profile_id: str, body: ScoreProfileCreate, session: SessionDep
) -> ScoreProfileResponse:
    project = get_project(session, project_id)
    profile = session.get(ScoreProfileModel, profile_id)
    if profile is None or profile.project_id != project.id:
        raise AppError("SCORE_PROFILE_NOT_FOUND", "Score profile was not found.", status_code=404)
    profile.name = body.name.strip()
    profile.rules = [rule.model_dump(mode="json") for rule in body.rules]
    session.commit()
    session.refresh(profile)
    return _profile_response(profile)


@router.post("/projects/{project_id}/score-profiles/default", response_model=ScoreProfileResponse)
async def restore_default_score_profile(project_id: str, session: SessionDep) -> ScoreProfileResponse:
    project = get_project(session, project_id)
    session.execute(
        delete(ScoreProfileModel).where(
            ScoreProfileModel.project_id == project.id, ScoreProfileModel.is_default.is_(True)
        )
    )
    profile = ScoreProfileModel(
        project_id=project.id,
        name="Default",
        is_default=True,
        rules=[rule.model_dump(mode="json") for rule in DEFAULT_RULES],
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _profile_response(profile)


@router.post("/projects/{project_id}/candidates/rank", response_model=CandidateListResponse)
async def rank_candidates(project_id: str, body: ScoreCandidatesRequest, session: SessionDep) -> CandidateListResponse:
    project = get_project(session, project_id)
    transcript = session.get(TranscriptModel, body.transcript_id)
    if transcript is None or transcript.project_id != project.id:
        raise AppError("TRANSCRIPT_NOT_FOUND", "Transcript was not found.", status_code=404)
    items = score_and_rank(
        session,
        project.id,
        body.transcript_id,
        body.candidate_ids,
        body.profile_id,
        body.top_n,
        body.max_overlap_ratio,
    )
    session.commit()
    return CandidateListResponse(items=[candidate_response(item) for item in items])


@router.get("/editor/presets", response_model=dict[LayoutPreset, EditConfig])
async def editor_presets() -> dict[LayoutPreset, EditConfig]:
    return {preset: preset_config(preset) for preset in LayoutPreset}


def _edit_response(item: EditConfigModel) -> EditConfigResponse:
    config = EditConfig.model_validate(item.config)
    return EditConfigResponse(
        id=item.id,
        project_id=item.project_id,
        candidate_id=item.candidate_id,
        schema_version=config.schema_version,
        config=config,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.get("/projects/{project_id}/candidates/{candidate_id}/edit-config", response_model=EditConfigResponse)
async def get_edit_config(project_id: str, candidate_id: str, session: SessionDep) -> EditConfigResponse:
    project = get_project(session, project_id)
    candidate = session.get(CandidateModel, candidate_id)
    if candidate is None or candidate.project_id != project.id:
        raise AppError("CANDIDATE_NOT_FOUND", "Candidate was not found.", status_code=404)
    item = session.scalar(
        select(EditConfigModel).where(
            EditConfigModel.project_id == project.id, EditConfigModel.candidate_id == candidate.id
        )
    )
    if item is None:
        raise AppError("EDIT_CONFIG_NOT_FOUND", "Edit config was not found.", status_code=404)
    stored_version = item.config.get("schema_version", 1)
    normalized, geometry_migrated = normalize_legacy_edit_config(EditConfig.model_validate(item.config))
    migrated = stored_version != normalized.schema_version or geometry_migrated
    if migrated:
        item.schema_version = normalized.schema_version
        item.config = normalized.model_dump(mode="json")
        session.commit()
        session.refresh(item)
    return _edit_response(item)


@router.get("/projects/{project_id}/candidates/{candidate_id}/captions", response_model=CaptionCueList)
def candidate_captions(project_id: str, candidate_id: str, request: Request, session: SessionDep) -> CaptionCueList:
    project = get_project(session, project_id)
    candidate = session.get(CandidateModel, candidate_id)
    if candidate is None or candidate.project_id != project.id:
        raise AppError("CANDIDATE_NOT_FOUND", "Candidate was not found.", status_code=404)
    transcript = session.get(TranscriptModel, candidate.transcript_id)
    if transcript is None:
        raise AppError("TRANSCRIPT_NOT_FOUND", "Transcript was not found.", status_code=404)
    path = request.app.state.storage.project_path(project.id, Path("transcripts") / Path(transcript.relative_path).name)
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise AppError("TRANSCRIPT_TOO_LARGE", "Transcript exceeds the caption read limit.", status_code=413)
        with request.app.state.storage.open_binary(path) as source:
            data = source.read(16 * 1024 * 1024 + 1)
            if len(data) > 16 * 1024 * 1024:
                raise AppError("TRANSCRIPT_TOO_LARGE", "Transcript exceeds the caption read limit.", status_code=413)
            document = TranscriptDocument.model_validate(json.loads(data.decode("utf-8")))
    except AppError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise AppError("TRANSCRIPT_UNAVAILABLE", "Stored transcript is unavailable.", status_code=500) from exc
    offset = project.webcam_offset_ms if document.source == MediaRole.WEBCAM.value else 0
    cues, timing_source = extract_caption_cues(document, candidate.start_ms, candidate.end_ms, offset)
    return CaptionCueList(items=cues, timing_source=timing_source)


@router.put("/projects/{project_id}/candidates/{candidate_id}/edit-config", response_model=EditConfigResponse)
async def save_edit_config(
    project_id: str, candidate_id: str, body: EditConfig, request: Request, session: SessionDep
) -> EditConfigResponse:
    body, _migrated = normalize_legacy_edit_config(body)
    project = get_project(session, project_id)
    candidate = session.get(CandidateModel, candidate_id)
    if candidate is None or candidate.project_id != project.id:
        raise AppError("CANDIDATE_NOT_FOUND", "Candidate was not found.", status_code=404)
    clip_duration = candidate.end_ms - candidate.start_ms
    if body.banner.start_ms >= clip_duration or (body.banner.end_ms is not None and body.banner.end_ms > clip_duration):
        raise AppError("INVALID_BANNER_RANGE", "Banner interval exceeds candidate duration.", status_code=422)
    if body.banner.image_relative_path is not None:
        asset_path = request.app.state.storage.project_path(project.id, body.banner.image_relative_path)
        if not asset_path.is_file():
            raise AppError("EDITOR_ASSET_NOT_FOUND", "Banner asset was not found in project storage.", status_code=422)
        try:
            with request.app.state.storage.open_binary(asset_path):
                pass
        except OSError as exc:
            raise AppError("EDITOR_ASSET_UNAVAILABLE", "Banner asset is unavailable.", status_code=422) from exc
    item = session.scalar(
        select(EditConfigModel).where(
            EditConfigModel.project_id == project.id, EditConfigModel.candidate_id == candidate.id
        )
    )
    if item is None:
        item = EditConfigModel(project_id=project.id, candidate_id=candidate.id, config={})
        session.add(item)
    item.schema_version = body.schema_version
    item.config = body.model_dump(mode="json")
    session.commit()
    session.refresh(item)
    return _edit_response(item)
