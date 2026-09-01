from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_session
from app.api.routes import _file_chunk, _job_response, _parse_range
from app.core.errors import AppError
from app.db.models import CandidateModel, JobModel, JobStatus, RenderArtifactModel
from app.jobs.schemas import JobListResponse, JobResponse
from app.projects.service import get_project
from app.rendering.schemas import (
    RenderArtifactList,
    RenderArtifactResponse,
    RenderKind,
    RenderPlan,
    RenderQuality,
    RenderRequest,
)

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


def _artifact_response(item: RenderArtifactModel, api_prefix: str) -> RenderArtifactResponse:
    return RenderArtifactResponse(
        id=item.id,
        project_id=item.project_id,
        candidate_id=item.candidate_id,
        edit_config_id=item.edit_config_id,
        job_id=item.job_id,
        kind=RenderKind(item.kind),
        quality=RenderQuality(item.quality),
        dependency_fingerprint=item.dependency_fingerprint,
        size_bytes=item.size_bytes,
        duration_ms=item.duration_ms,
        width=item.width,
        height=item.height,
        has_audio=item.has_audio,
        content_url=f"{api_prefix}/projects/{item.project_id}/artifacts/{item.id}/content",
        created_at=item.created_at,
    )


@router.get(
    "/projects/{project_id}/candidates/{candidate_id}/render-plan",
    response_model=RenderPlan,
)
async def inspect_render_plan(
    project_id: str,
    candidate_id: str,
    request: Request,
    session: SessionDep,
    kind: RenderKind = RenderKind.PREVIEW,
    quality: RenderQuality = RenderQuality.BALANCED,
) -> RenderPlan:
    project = get_project(session, project_id)
    plan = cast(
        RenderPlan,
        await _build_plan(request, project.id, candidate_id, kind, quality),
    )
    # The inspect endpoint exposes plan semantics, never host filesystem paths.
    inputs = [item.model_copy(update={"path": Path("media") / item.media_id}) for item in plan.inputs]
    banner = plan.banner
    if banner.asset_path is not None and banner.asset_relative_path is not None:
        banner = banner.model_copy(update={"asset_path": Path(banner.asset_relative_path)})
    return plan.model_copy(update={"inputs": inputs, "banner": banner})


@router.post(
    "/projects/{project_id}/candidates/{candidate_id}/preview-jobs",
    response_model=JobResponse,
    status_code=202,
)
async def start_preview(
    project_id: str,
    candidate_id: str,
    body: RenderRequest,
    request: Request,
    session: SessionDep,
) -> JobResponse:
    return await _start_render(project_id, candidate_id, RenderKind.PREVIEW, body, request, session)


@router.post(
    "/projects/{project_id}/candidates/{candidate_id}/render-jobs",
    response_model=JobResponse,
    status_code=202,
)
async def start_final_render(
    project_id: str,
    candidate_id: str,
    body: RenderRequest,
    request: Request,
    session: SessionDep,
) -> JobResponse:
    return await _start_render(project_id, candidate_id, RenderKind.FINAL, body, request, session)


async def _start_render(
    project_id: str,
    candidate_id: str,
    kind: RenderKind,
    body: RenderRequest,
    request: Request,
    session: Session,
) -> JobResponse:
    project = get_project(session, project_id)
    candidate = session.get(CandidateModel, candidate_id)
    if candidate is None or candidate.project_id != project.id:
        raise AppError("CANDIDATE_NOT_FOUND", "Candidate was not found.", status_code=404)
    # Resolve every dependency before occupying the constrained local job queue.
    plan = cast(
        RenderPlan,
        await _build_plan(request, project.id, candidate.id, kind, body.quality),
    )
    request.app.state.renderer.ensure_available()
    data: dict[str, object] = {
        "candidate_id": candidate.id,
        "kind": kind.value,
        "quality": body.quality.value,
        "expected_edit_config_fingerprint": plan.edit_config_fingerprint,
        "expected_dependency_fingerprint": plan.dependency_fingerprint,
    }
    job_kind = "RENDER_PREVIEW" if kind == RenderKind.PREVIEW else "RENDER_FINAL"
    job, created = request.app.state.job_runner.create_job(project.id, job_kind, data)
    if created:
        request.app.state.job_runner.submit(job.id)
    return _job_response(job)


async def _build_plan(
    request: Request,
    project_id: str,
    candidate_id: str,
    kind: RenderKind,
    quality: RenderQuality,
) -> object:
    async with request.app.state.render_plan_slots:
        future = request.app.state.render_plan_executor.submit(
            request.app.state.rendering.plan, project_id, candidate_id, kind, quality
        )
        # Polling avoids platform/event-loop-specific wakeup issues observed when a
        # concurrent Future completes after spawning ffprobe from its worker thread.
        try:
            while not future.done():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            if not future.cancel():
                while not future.done():
                    await asyncio.sleep(0.01)
            raise
        return future.result()


@router.get("/projects/{project_id}/render-jobs", response_model=JobListResponse)
async def list_render_jobs(
    project_id: str,
    session: SessionDep,
    candidate_id: str | None = None,
    kind: Literal["RENDER_PREVIEW", "RENDER_FINAL"] | None = None,
    status: JobStatus | None = None,
) -> JobListResponse:
    project = get_project(session, project_id)
    statement = select(JobModel).where(
        JobModel.project_id == project.id,
        JobModel.kind.in_(["RENDER_PREVIEW", "RENDER_FINAL"]),
    )
    if kind is not None:
        statement = statement.where(JobModel.kind == kind)
    if status is not None:
        statement = statement.where(JobModel.status == status)
    if candidate_id is not None:
        statement = statement.where(JobModel.request_data["candidate_id"].as_string() == candidate_id)
    jobs = session.scalars(statement.order_by(JobModel.created_at.desc()).limit(200)).all()
    return JobListResponse(items=[_job_response(job) for job in jobs])


@router.get("/projects/{project_id}/artifacts", response_model=RenderArtifactList)
async def list_artifacts(
    project_id: str,
    request: Request,
    session: SessionDep,
    candidate_id: str | None = None,
    kind: RenderKind | None = None,
) -> RenderArtifactList:
    project = get_project(session, project_id)
    statement = select(RenderArtifactModel).where(RenderArtifactModel.project_id == project.id)
    if candidate_id is not None:
        statement = statement.where(RenderArtifactModel.candidate_id == candidate_id)
    if kind is not None:
        statement = statement.where(RenderArtifactModel.kind == kind.value)
    items = session.scalars(statement.order_by(RenderArtifactModel.created_at.desc())).all()
    return RenderArtifactList(
        items=[_artifact_response(item, request.app.state.settings.api_prefix) for item in items]
    )


@router.get("/projects/{project_id}/artifacts/{artifact_id}", response_model=RenderArtifactResponse)
async def get_artifact(
    project_id: str,
    artifact_id: str,
    request: Request,
    session: SessionDep,
) -> RenderArtifactResponse:
    item = _artifact(session, project_id, artifact_id)
    return _artifact_response(item, request.app.state.settings.api_prefix)


@router.get("/projects/{project_id}/artifacts/{artifact_id}/content")
async def artifact_content(
    project_id: str,
    artifact_id: str,
    request: Request,
    session: SessionDep,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> StreamingResponse:
    item = _artifact(session, project_id, artifact_id)
    path = request.app.state.storage.project_path(project_id, item.relative_path)
    if not path.is_file() or path.stat().st_size != item.size_bytes:
        raise AppError("RENDER_ARTIFACT_MISSING", "Rendered file is missing from project storage.", status_code=404)
    size = item.size_bytes
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": f'inline; filename="{path.name}"'}
    if range_header is None:
        return StreamingResponse(
            _file_chunk(request.app.state.storage, path, 0, size),
            media_type="video/mp4",
            headers={**headers, "Content-Length": str(size)},
        )
    start, end = _parse_range(range_header, size)
    return StreamingResponse(
        _file_chunk(request.app.state.storage, path, start, end - start + 1),
        status_code=206,
        media_type="video/mp4",
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )


def _artifact(session: Session, project_id: str, artifact_id: str) -> RenderArtifactModel:
    project = get_project(session, project_id)
    item = session.get(RenderArtifactModel, artifact_id)
    if item is None or item.project_id != project.id:
        raise AppError("RENDER_ARTIFACT_NOT_FOUND", "Render artifact was not found.", status_code=404)
    return item
