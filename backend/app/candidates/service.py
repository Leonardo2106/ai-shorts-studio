from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.candidates.schemas import CandidateGenerationRequest, CandidateResponse
from app.core.errors import AppError
from app.db.models import CandidateModel, MediaModel, MediaRole, ProjectModel, TranscriptModel
from app.projects.storage import ProjectStorage
from app.transcription.schemas import TranscriptDocument, TranscriptSegment


def candidate_response(item: CandidateModel) -> CandidateResponse:
    return CandidateResponse(
        id=item.id,
        project_id=item.project_id,
        transcript_id=item.transcript_id,
        schema_version=item.schema_version,
        start_ms=item.start_ms,
        end_ms=item.end_ms,
        title=item.title,
        status=item.status,
        text=str(item.context.get("text", "")),
        origin=str(item.context.get("analysis_origin", "LOCAL")),
        local_features=item.signals,
        reasons=item.reasons,
        context=item.context,
        signals=item.signals,
        score=item.score,
        score_breakdown=item.score_breakdown,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def rebuild_candidate_excerpt(
    storage: ProjectStorage,
    transcript: TranscriptModel,
    project: ProjectModel,
    start_ms: int,
    end_ms: int,
) -> tuple[str, list[str], dict[str, object], dict[str, float]]:
    path = storage.project_path(project.id, Path("transcripts") / Path(transcript.relative_path).name)
    try:
        with storage.open_binary(path) as source:
            document = TranscriptDocument.model_validate(json.loads(source.read().decode("utf-8")))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise AppError("TRANSCRIPT_UNAVAILABLE", "Stored transcript is unavailable.", status_code=500) from exc
    offset = project.webcam_offset_ms if document.source == MediaRole.WEBCAM.value else 0
    matching = [
        segment
        for segment in document.segments
        if segment.end_ms + offset > start_ms and segment.start_ms + offset < end_ms
    ]
    text = " ".join(segment.text.strip() for segment in matching if segment.text.strip())
    title = text[:237] + "..." if len(text) > 240 else text
    if not title:
        title = f"Manual clip {start_ms / 1000:.1f}s–{end_ms / 1000:.1f}s"
    context: dict[str, object] = {
        "source": document.source,
        "analysis_origin": "LOCAL",
        "source_start_ms": max(0, start_ms - offset),
        "source_end_ms": max(0, end_ms - offset),
        "text": text,
    }
    duration = end_ms - start_ms
    signals = {
        "duration_fit": 1.0 if 25_000 <= duration <= 45_000 else 0.6,
        "context_completeness": 1.0 if matching else 0.0,
        "dead_air_penalty": 0.0 if matching else 1.0,
    }
    return title, ["manual_range_adjustment"], context, signals


def project_timeline_bounds(session: Session, project: ProjectModel) -> tuple[int, int]:
    media = session.scalars(select(MediaModel).where(MediaModel.project_id == project.id)).all()
    by_role = {item.role: int(item.probe_data["duration_ms"]) for item in media}
    if MediaRole.SCREEN in by_role and MediaRole.WEBCAM in by_role:
        start = max(0, project.webcam_offset_ms)
        end = min(by_role[MediaRole.SCREEN], project.webcam_offset_ms + by_role[MediaRole.WEBCAM])
    elif MediaRole.SCREEN in by_role:
        start, end = 0, by_role[MediaRole.SCREEN]
    elif MediaRole.WEBCAM in by_role:
        start, end = project.webcam_offset_ms, project.webcam_offset_ms + by_role[MediaRole.WEBCAM]
    else:
        raise AppError("MEDIA_NOT_READY", "Project has no media timeline.", status_code=409)
    if end <= start:
        raise AppError("SYNC_NO_OVERLAP", "The project media has no usable timeline overlap.", status_code=422)
    return max(0, start), end


def validate_candidate_range(session: Session, project: ProjectModel, start_ms: int, end_ms: int) -> None:
    lower, upper = project_timeline_bounds(session, project)
    duration = end_ms - start_ms
    if start_ms < lower or end_ms > upper or duration < 1_000 or duration > 60_000:
        raise AppError(
            "INVALID_CANDIDATE_RANGE",
            "Candidate range must be inside the available project timeline.",
            status_code=422,
            details={
                "timeline_start_ms": lower,
                "timeline_end_ms": upper,
                "min_duration_ms": 1_000,
                "max_duration_ms": 60_000,
            },
        )


class CandidateService:
    def __init__(self, storage: ProjectStorage, session_factory: sessionmaker[Session]) -> None:
        self.storage = storage
        self.session_factory = session_factory

    def _document(self, project_id: str, transcript: TranscriptModel) -> TranscriptDocument:
        path = self.storage.project_path(project_id, Path("transcripts") / Path(transcript.relative_path).name)
        try:
            with self.storage.open_binary(path) as source:
                return TranscriptDocument.model_validate(json.loads(source.read().decode("utf-8")))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise AppError("TRANSCRIPT_UNAVAILABLE", "Stored transcript is unavailable.", status_code=500) from exc

    def generate(
        self,
        project_id: str,
        request: CandidateGenerationRequest,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, object]:
        with self.session_factory() as session:
            project = session.get(ProjectModel, project_id)
            transcript = session.get(TranscriptModel, request.transcript_id)
            if project is None or transcript is None or transcript.project_id != project_id:
                raise AppError("TRANSCRIPT_NOT_FOUND", "Transcript was not found.", status_code=404)
            document = self._document(project_id, transcript)
            source_offset = project.webcam_offset_ms if document.source == MediaRole.WEBCAM.value else 0
            lower, upper = project_timeline_bounds(session, project)
        if cancelled():
            raise AppError("JOB_CANCELLED", "Candidate generation was cancelled.", status_code=409)
        segments = [segment for segment in document.segments if segment.text.strip()]
        proposals: list[dict[str, object]] = []
        for index, _segment in enumerate(segments):
            if cancelled():
                raise AppError("JOB_CANCELLED", "Candidate generation was cancelled.", status_code=409)
            proposal = self._proposal(segments, index, document.source, source_offset, lower, upper, request)
            if proposal is not None:
                proposals.append(proposal)
            progress((index + 1) / max(1, len(segments)) * 0.8)
        ranked = self._rank_and_deduplicate(proposals, request.top_n)
        if cancelled():
            raise AppError("JOB_CANCELLED", "Candidate generation was cancelled.", status_code=409)
        with self.session_factory.begin() as session:
            existing = session.scalars(
                select(CandidateModel).where(
                    CandidateModel.project_id == project_id,
                    CandidateModel.transcript_id == request.transcript_id,
                )
            ).all()
            by_range = {(item.start_ms, item.end_ms): item for item in existing}
            ids: list[str] = []
            for proposal in ranked:
                range_key = (cast(int, proposal["start_ms"]), cast(int, proposal["end_ms"]))
                previous = by_range.get(range_key)
                if previous is not None:
                    ids.append(previous.id)
                    continue
                item = CandidateModel(
                    project_id=project_id,
                    transcript_id=request.transcript_id,
                    **proposal,
                )
                session.add(item)
                session.flush()
                ids.append(item.id)
        progress(1.0)
        return {"candidate_ids": ids, "count": len(ids)}

    @staticmethod
    def _proposal(
        segments: list[TranscriptSegment],
        anchor: int,
        source: str,
        offset: int,
        lower: int,
        upper: int,
        request: CandidateGenerationRequest,
    ) -> dict[str, object] | None:
        first = anchor
        last = anchor
        while first > 0 and segments[last].end_ms - segments[first].start_ms < request.ideal_min_ms:
            first -= 1
        while last + 1 < len(segments) and segments[last].end_ms - segments[first].start_ms < request.ideal_min_ms:
            last += 1
        raw_start = segments[first].start_ms + offset - request.pre_roll_ms
        raw_end = segments[last].end_ms + offset + request.post_roll_ms
        start, end = max(lower, raw_start), min(upper, raw_end)
        duration = end - start
        if duration < request.min_duration_ms or duration > request.max_duration_ms:
            return None
        text = " ".join(segment.text.strip() for segment in segments[first : last + 1])
        reasons = ["complete_transcript_phrase", "continuous_context"]
        if "!" in text:
            reasons.append("exclamation")
        if re.search(r"\b(uau|wow|incr[ií]vel|surpresa|surprising)\b", text, re.IGNORECASE):
            reasons.append("surprise_language")
        if re.search(r"\b(?:ha){2,}|\b(?:he){2,}|\b(risos|laughs?)\b", text, re.IGNORECASE):
            reasons.append("laughter_transcript_marker")
        ideal = request.ideal_min_ms <= duration <= request.ideal_max_ms
        signals = {
            "duration_fit": 1.0 if ideal else 0.6,
            "exclamation": 1.0 if "exclamation" in reasons else 0.0,
            "surprise_language": 1.0 if "surprise_language" in reasons else 0.0,
            "laughter_marker": 1.0 if "laughter_transcript_marker" in reasons else 0.0,
            "context_completeness": 1.0,
            "dead_air_penalty": 0.0,
        }
        score = round(sum(float(value) for value in signals.values()) / len(signals) * 100, 3)
        return {
            "start_ms": start,
            "end_ms": end,
            "title": text[:237] + "..." if len(text) > 240 else text,
            "reasons": reasons,
            "context": {
                "source": source,
                "analysis_origin": "LOCAL",
                "source_start_ms": segments[first].start_ms,
                "source_end_ms": segments[last].end_ms,
                "text": text,
            },
            "signals": signals,
            "score": score,
            "score_breakdown": None,
        }

    @staticmethod
    def _rank_and_deduplicate(items: list[dict[str, object]], top_n: int) -> list[dict[str, object]]:
        ranked = sorted(
            items,
            key=lambda item: (-cast(float, item["score"]), cast(int, item["start_ms"])),
        )
        selected: list[dict[str, object]] = []
        for item in ranked:
            start, end = cast(int, item["start_ms"]), cast(int, item["end_ms"])
            duplicate = False
            for existing in selected:
                other_start = cast(int, existing["start_ms"])
                other_end = cast(int, existing["end_ms"])
                overlap = max(0, min(end, other_end) - max(start, other_start))
                shorter = min(end - start, other_end - other_start)
                if shorter and overlap / shorter > 0.65:
                    duplicate = True
                    break
            if not duplicate:
                selected.append(item)
            if len(selected) >= top_n:
                break
        return selected
