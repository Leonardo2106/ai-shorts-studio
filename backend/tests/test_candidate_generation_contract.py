from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.candidates.schemas import CandidateGenerationRequest, CandidateUpdate
from app.candidates.service import CandidateService
from app.core.errors import AppError
from app.db.models import CandidateModel, MediaModel, MediaRole, ProjectModel, TranscriptModel
from app.db.session import build_engine, build_session_factory, initialize_database
from app.projects.storage import ProjectStorage

PROJECT_ID = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"
MEDIA_ID = "2c4d3443-a8f1-4d75-befb-0dab0a3b37e9"
TRANSCRIPT_ID = "c8effc6b-676b-45fd-8e8e-34eb97c3641c"


def test_candidate_generation_never_accepts_more_than_sixty_seconds() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 60000"):
        CandidateGenerationRequest(transcript_id=TRANSCRIPT_ID, max_duration_ms=60_001)


def _service_with_transcript(
    tmp_path: Path,
    *,
    source: MediaRole = MediaRole.WEBCAM,
    webcam_offset_ms: int = 0,
    duration_ms: int = 60_000,
    segments: list[dict[str, object]] | None = None,
) -> CandidateService:
    storage = ProjectStorage(tmp_path / "storage")
    project_dir = storage.project_dir(PROJECT_ID, create=True)
    transcript_path = project_dir / "transcripts" / "candidate-source.json"
    transcript_path.parent.mkdir()
    transcript_path.write_text(
        json.dumps(
            {
                "id": TRANSCRIPT_ID,
                "project_id": PROJECT_ID,
                "media_id": MEDIA_ID,
                "source": source.value,
                "audio_stream_index": 1,
                "language": "pt",
                "duration_ms": duration_ms,
                "engine": "fixture",
                "model": "fixture",
                "segments": segments
                or [
                    {
                        "start_ms": 5_000,
                        "end_ms": 21_000,
                        "text": "Uau! hahaha, isto é incrível.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    engine = build_engine(storage.root / "metadata.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=PROJECT_ID, name="Candidate contract", webcam_offset_ms=webcam_offset_ms))
        session.flush()
        media = MediaModel(
            id=MEDIA_ID,
            project_id=PROJECT_ID,
            role=source,
            relative_path=f"projects/{PROJECT_ID}/{source.value.lower()}.mp4",
            original_filename=f"{source.value.lower()}.mp4",
            size_bytes=1,
            sha256="a" * 64,
            probe_data={"duration_ms": duration_ms, "audio_streams": []},
        )
        session.add(media)
        session.flush()
        session.add(
            TranscriptModel(
                id=TRANSCRIPT_ID,
                project_id=PROJECT_ID,
                media_id=MEDIA_ID,
                cache_key="b" * 64,
                relative_path="transcripts/candidate-source.json",
                language="pt",
                duration_ms=duration_ms,
            )
        )
    return CandidateService(storage, factory)


def test_candidate_generation_preserves_webcam_source_at_zero_offset_and_explains_result(tmp_path: Path) -> None:
    service = _service_with_transcript(tmp_path)

    result = service.generate(
        PROJECT_ID,
        CandidateGenerationRequest(transcript_id=TRANSCRIPT_ID, pre_roll_ms=500, post_roll_ms=750),
        lambda _progress: None,
        lambda: False,
    )

    assert result["count"] == 1
    with service.session_factory() as session:
        candidate = session.get(CandidateModel, result["candidate_ids"][0])
        assert candidate is not None
        assert (candidate.start_ms, candidate.end_ms) == (4_500, 21_750)
        assert candidate.context["source"] == "WEBCAM"
        assert (candidate.context["source_start_ms"], candidate.context["source_end_ms"]) == (5_000, 21_000)
        assert {
            "complete_transcript_phrase",
            "continuous_context",
            "exclamation",
            "surprise_language",
            "laughter_transcript_marker",
        } <= set(candidate.reasons)


def test_candidate_generation_drops_short_transcript_and_honors_cancellation(tmp_path: Path) -> None:
    service = _service_with_transcript(
        tmp_path,
        segments=[{"start_ms": 0, "end_ms": 2_000, "text": "curto"}],
    )

    empty = service.generate(
        PROJECT_ID,
        CandidateGenerationRequest(transcript_id=TRANSCRIPT_ID),
        lambda _progress: None,
        lambda: False,
    )
    assert empty == {"candidate_ids": [], "count": 0}

    with pytest.raises(AppError, match="cancelled") as failure:
        service.generate(
            PROJECT_ID,
            CandidateGenerationRequest(transcript_id=TRANSCRIPT_ID),
            lambda _progress: None,
            lambda: True,
        )
    assert failure.value.code == "JOB_CANCELLED"


def test_candidate_request_and_manual_range_reject_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="duration bounds"):
        CandidateGenerationRequest(
            transcript_id=TRANSCRIPT_ID,
            min_duration_ms=20_000,
            ideal_min_ms=12_000,
        )
    with pytest.raises(ValueError, match="end_ms"):
        CandidateUpdate(start_ms=20_000, end_ms=20_000)


def test_candidate_generation_cancels_between_segments_before_persisting_results(tmp_path: Path) -> None:
    service = _service_with_transcript(
        tmp_path,
        segments=[
            {"start_ms": 0, "end_ms": 15_000, "text": "primeiro trecho completo"},
            {"start_ms": 15_000, "end_ms": 30_000, "text": "segundo trecho completo"},
        ],
    )
    progress: list[float] = []

    with pytest.raises(AppError, match="cancelled") as failure:
        service.generate(
            PROJECT_ID,
            CandidateGenerationRequest(transcript_id=TRANSCRIPT_ID),
            progress.append,
            lambda: bool(progress),
        )

    assert failure.value.code == "JOB_CANCELLED"
    with service.session_factory() as session:
        assert list(session.scalars(select(CandidateModel))) == []
