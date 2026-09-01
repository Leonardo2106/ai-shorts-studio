from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.db.models import (
    CandidateModel,
    EditConfigModel,
    MediaModel,
    MediaRole,
    ProjectModel,
    RenderArtifactModel,
    TranscriptModel,
)
from app.editor.captions import extract_caption_cues
from app.editor.schemas import EditConfig, normalize_legacy_edit_config
from app.media.schemas import MediaProbe
from app.projects.storage import ProjectStorage
from app.rendering.plan import RenderPlanBuilder
from app.rendering.renderer import Renderer
from app.rendering.schemas import (
    RenderInputRole,
    RenderKind,
    RenderPlan,
    RenderQuality,
    ResolvedBannerAsset,
    ResolvedMediaInput,
    ResolvedRenderContext,
    ResolvedTranscriptSource,
)
from app.transcription.schemas import TranscriptDocument

_MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024


class RenderingService:
    def __init__(
        self,
        storage: ProjectStorage,
        session_factory: sessionmaker[Session],
        renderer: Renderer,
    ) -> None:
        self.storage = storage
        self.session_factory = session_factory
        self.renderer = renderer
        self.plan_builder = RenderPlanBuilder()

    def plan(self, project_id: str, candidate_id: str, kind: RenderKind, quality: RenderQuality) -> RenderPlan:
        with self.session_factory() as session:
            context = self._context(session, project_id, candidate_id)
        return self.plan_builder.build(context, kind=kind, quality=quality)

    def run(
        self,
        project_id: str,
        data: dict[str, object],
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, object]:
        try:
            candidate_id = str(data["candidate_id"])
            kind = RenderKind(str(data["kind"]))
            quality = RenderQuality(str(data["quality"]))
            job_id = str(data["_job_id"])
            expected_edit_fingerprint = str(data["expected_edit_config_fingerprint"])
            expected_dependency_fingerprint = str(data["expected_dependency_fingerprint"])
        except (KeyError, ValueError) as exc:
            raise AppError("INVALID_RENDER_REQUEST", "Render job data is invalid.", status_code=422) from exc
        plan = self.plan(project_id, candidate_id, kind, quality)
        if (
            plan.edit_config_fingerprint != expected_edit_fingerprint
            or plan.dependency_fingerprint != expected_dependency_fingerprint
        ):
            raise AppError(
                "RENDER_PLAN_STALE",
                "The clip or editor configuration changed after this render was queued. Start a new render.",
                status_code=409,
            )
        if cancelled():
            raise AppError("JOB_CANCELLED", "Rendering was cancelled.", status_code=409)
        with self.session_factory() as session:
            cached = self._cached_preview(session, plan)
            if cached is not None:
                return {"artifact_id": cached.id, "cached": True}
        destination = self._destination(plan)
        if plan.kind == RenderKind.PREVIEW and destination.exists():
            # A deterministic preview without a valid artifact row is an orphan from an interrupted run.
            destination.unlink()
        rendered = self.renderer.render(plan, destination, progress, cancelled)
        if cancelled():
            destination.unlink(missing_ok=True)
            raise AppError("JOB_CANCELLED", "Rendering was cancelled.", status_code=409)
        relative_path = destination.relative_to(self.storage.project_dir(project_id)).as_posix()
        artifact = RenderArtifactModel(
            project_id=project_id,
            candidate_id=plan.candidate_id,
            edit_config_id=plan.edit_config_id,
            job_id=job_id,
            kind=plan.kind.value,
            quality=plan.quality.value,
            dependency_fingerprint=plan.dependency_fingerprint,
            relative_path=relative_path,
            size_bytes=rendered.size_bytes,
            duration_ms=rendered.duration_ms,
            width=rendered.width,
            height=rendered.height,
            has_audio=rendered.has_audio,
        )
        try:
            with self.session_factory.begin() as session:
                session.add(artifact)
                session.flush()
                artifact_id = artifact.id
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        if cancelled():
            with self.session_factory.begin() as session:
                persisted = session.get(RenderArtifactModel, artifact_id)
                if persisted is not None:
                    session.delete(persisted)
            destination.unlink(missing_ok=True)
            raise AppError("JOB_CANCELLED", "Rendering was cancelled.", status_code=409)
        if kind == RenderKind.PREVIEW:
            self._cleanup_old_previews(project_id)
        return {"artifact_id": artifact_id, "cached": False}

    def _context(self, session: Session, project_id: str, candidate_id: str) -> ResolvedRenderContext:
        project = session.get(ProjectModel, project_id)
        if project is None:
            raise AppError("PROJECT_NOT_FOUND", "Project was not found.", status_code=404)
        candidate = session.get(CandidateModel, candidate_id)
        if candidate is None or candidate.project_id != project.id:
            raise AppError("CANDIDATE_NOT_FOUND", "Candidate was not found.", status_code=404)
        edit = session.scalar(
            select(EditConfigModel).where(
                EditConfigModel.project_id == project.id,
                EditConfigModel.candidate_id == candidate.id,
            )
        )
        if edit is None:
            raise AppError("EDIT_CONFIG_NOT_FOUND", "Save the editor configuration before rendering.", status_code=409)
        stored_version = edit.config.get("schema_version", 1)
        config, geometry_migrated = normalize_legacy_edit_config(EditConfig.model_validate(edit.config))
        migrated = stored_version != config.schema_version or geometry_migrated
        if migrated:
            edit.schema_version = config.schema_version
            edit.config = config.model_dump(mode="json")
            session.commit()
        media_rows = session.scalars(select(MediaModel).where(MediaModel.project_id == project.id)).all()
        media = [self._media_input(project.id, item) for item in media_rows]
        transcript_row = session.get(TranscriptModel, candidate.transcript_id)
        transcript = self._transcript(project, candidate, transcript_row) if transcript_row is not None else None
        banner_asset = self._banner_asset(project.id, config)
        return ResolvedRenderContext(
            project_id=project.id,
            candidate_id=candidate.id,
            edit_config_id=edit.id,
            clip_start_ms=candidate.start_ms,
            clip_end_ms=candidate.end_ms,
            webcam_offset_ms=project.webcam_offset_ms,
            edit_config=config,
            media=media,
            transcript=transcript,
            banner_asset=banner_asset,
        )

    def _media_input(self, project_id: str, media: MediaModel) -> ResolvedMediaInput:
        probe = MediaProbe.model_validate(media.probe_data)
        path = self.storage.project_path(project_id, Path(media.relative_path).name)
        return ResolvedMediaInput(
            media_id=media.id,
            role=RenderInputRole(media.role.value),
            path=path,
            sha256=media.sha256,
            duration_ms=probe.duration_ms,
            video_stream_indexes=[item.index for item in probe.video_streams],
            audio_stream_indexes=[item.index for item in probe.audio_streams],
        )

    def _transcript(
        self,
        project: ProjectModel,
        candidate: CandidateModel,
        transcript: TranscriptModel,
    ) -> ResolvedTranscriptSource:
        if transcript.project_id != project.id:
            raise AppError("TRANSCRIPT_NOT_FOUND", "Candidate transcript was not found.", status_code=404)
        path = self.storage.project_path(project.id, Path("transcripts") / Path(transcript.relative_path).name)
        document = _load_transcript(self.storage, path)
        offset = project.webcam_offset_ms if document.source == MediaRole.WEBCAM.value else 0
        cues, timing_source = extract_caption_cues(document, candidate.start_ms, candidate.end_ms, offset)
        return ResolvedTranscriptSource(
            transcript_id=transcript.id,
            cache_key=transcript.cache_key,
            media_id=transcript.media_id,
            audio_stream_index=document.audio_stream_index,
            caption_cues=cues,
            timing_source=timing_source,
        )

    def _banner_asset(self, project_id: str, config: EditConfig) -> ResolvedBannerAsset | None:
        relative = config.banner.image_relative_path
        if relative is None:
            return None
        path = self.storage.project_path(project_id, relative)
        if not path.is_file():
            raise AppError("RENDER_BANNER_ASSET_MISSING", "Banner asset is missing.", status_code=422)
        try:
            size_bytes = path.stat().st_size
        except OSError as exc:
            raise AppError("RENDER_BANNER_ASSET_MISSING", "Banner asset is unavailable.", status_code=422) from exc
        if size_bytes > self.renderer.settings.max_banner_asset_bytes:
            raise AppError(
                "RENDER_BANNER_ASSET_TOO_LARGE",
                "Banner image exceeds the configured size limit.",
                status_code=413,
                details={"max_bytes": self.renderer.settings.max_banner_asset_bytes},
            )
        self._validate_banner_image(path)
        digest = hashlib.sha256()
        try:
            with self.storage.open_binary(path) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise AppError("RENDER_BANNER_ASSET_MISSING", "Banner asset is unavailable.", status_code=422) from exc
        return ResolvedBannerAsset(relative_path=relative, path=path, sha256=digest.hexdigest())

    def _validate_banner_image(self, path: Path) -> None:
        settings = self.renderer.settings
        try:
            result = subprocess.run(
                [
                    settings.ffprobe_binary,
                    "-v",
                    "error",
                    "-count_frames",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_type,width,height,nb_read_frames",
                    "-of",
                    "json",
                    str(path),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=settings.ffprobe_timeout_seconds,
                shell=False,
                check=False,
            )
            payload = json.loads(result.stdout) if result.returncode == 0 else {}
            stream = next(
                (item for item in payload.get("streams", []) if item.get("codec_type") == "video"),
                None,
            )
            width = int(stream.get("width") or 0) if stream else 0
            height = int(stream.get("height") or 0) if stream else 0
            frames = int(stream.get("nb_read_frames") or 1) if stream else 0
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise AppError(
                "RENDER_BANNER_ASSET_INVALID", "Banner asset is not a valid image.", status_code=422
            ) from exc
        if (
            stream is None
            or width <= 0
            or height <= 0
            or width > settings.max_video_width
            or height > settings.max_video_height
            or frames != 1
        ):
            raise AppError(
                "RENDER_BANNER_ASSET_INVALID",
                "Banner asset must be one supported still image with reasonable dimensions.",
                status_code=422,
            )

    def _cached_preview(self, session: Session, plan: RenderPlan) -> RenderArtifactModel | None:
        if not plan.cacheable:
            return None
        items = session.scalars(
            select(RenderArtifactModel)
            .where(
                RenderArtifactModel.project_id == plan.project_id,
                RenderArtifactModel.candidate_id == plan.candidate_id,
                RenderArtifactModel.kind == RenderKind.PREVIEW.value,
                RenderArtifactModel.dependency_fingerprint == plan.dependency_fingerprint,
            )
            .order_by(RenderArtifactModel.created_at.desc())
        ).all()
        for item in items:
            try:
                path = self.storage.project_path(plan.project_id, item.relative_path)
            except AppError:
                session.delete(item)
                continue
            if path.is_file() and path.stat().st_size == item.size_bytes:
                return item
            session.delete(item)
        if session.deleted:
            session.commit()
        return None

    def _destination(self, plan: RenderPlan) -> Path:
        directory = Path(plan.output.relative_directory)
        if plan.kind == RenderKind.PREVIEW:
            name = f"preview-{plan.dependency_fingerprint}.mp4"
        else:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            name = f"short-{stamp}-{uuid.uuid4().hex[:8]}.mp4"
        return self.storage.project_path(plan.project_id, directory / name)

    def _cleanup_old_previews(self, project_id: str) -> None:
        with self.session_factory.begin() as session:
            stale = session.scalars(
                select(RenderArtifactModel)
                .where(
                    RenderArtifactModel.project_id == project_id,
                    RenderArtifactModel.kind == RenderKind.PREVIEW.value,
                )
                .order_by(RenderArtifactModel.created_at.desc())
                .offset(self.renderer.settings.max_cached_previews_per_project)
            ).all()
            for artifact in stale:
                relative = Path(artifact.relative_path)
                if relative.parts and relative.parts[0] == "previews":
                    self.storage.project_path(project_id, relative).unlink(missing_ok=True)
                session.delete(artifact)


def _load_transcript(storage: ProjectStorage, path: Path) -> TranscriptDocument:
    try:
        if path.stat().st_size > _MAX_TRANSCRIPT_BYTES:
            raise AppError("TRANSCRIPT_TOO_LARGE", "Transcript exceeds the rendering read limit.", status_code=413)
        with storage.open_binary(path) as source:
            data = source.read(_MAX_TRANSCRIPT_BYTES + 1)
            if len(data) > _MAX_TRANSCRIPT_BYTES:
                raise AppError(
                    "TRANSCRIPT_TOO_LARGE",
                    "Transcript exceeds the rendering read limit.",
                    status_code=413,
                )
            return TranscriptDocument.model_validate(json.loads(data.decode("utf-8")))
    except AppError:
        raise
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise AppError("TRANSCRIPT_UNAVAILABLE", "Stored transcript is unavailable.", status_code=500) from exc
