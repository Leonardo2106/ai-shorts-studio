from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from app.api.routes import candidate_captions
from app.db.models import CandidateModel, MediaModel, MediaRole, ProjectModel, TranscriptModel
from app.db.session import build_engine, build_session_factory, initialize_database
from app.editor.captions import extract_caption_cues
from app.projects.storage import ProjectStorage
from app.transcription.schemas import TranscriptDocument


def test_caption_extraction_clips_words_and_segment_fallback_to_candidate_timeline() -> None:
    document = TranscriptDocument.model_validate(
        {
            "id": "transcript",
            "project_id": "project",
            "media_id": "media",
            "source": "WEBCAM",
            "audio_stream_index": 1,
            "language": "pt",
            "duration_ms": 5_000,
            "engine": "fixture",
            "model": "fixture",
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 2_000,
                    "text": "first second",
                    "words": [
                        {"start_ms": 0, "end_ms": 1_200, "text": "first"},
                        {"start_ms": 1_200, "end_ms": 2_000, "text": "second"},
                    ],
                },
                {"start_ms": 2_000, "end_ms": 4_000, "text": "fallback"},
            ],
        }
    )

    cues, timing_source = extract_caption_cues(document, 1_000, 3_000, 200)

    assert timing_source == "WORDS_AND_SEGMENTS"
    assert [cue.model_dump() for cue in cues] == [
        {
            "start_ms": 0,
            "end_ms": 400,
            "text": "first",
            "words": [{"start_ms": 0, "end_ms": 400, "text": "first"}],
        },
        {
            "start_ms": 400,
            "end_ms": 1_200,
            "text": "second",
            "words": [{"start_ms": 400, "end_ms": 1_200, "text": "second"}],
        },
        {"start_ms": 1_200, "end_ms": 2_000, "text": "fallback", "words": None},
    ]


def test_captions_fall_back_per_segment_when_only_part_of_transcript_has_words(tmp_path: Path) -> None:
    project_id = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"
    storage = ProjectStorage(tmp_path / "storage")
    storage.project_dir(project_id, create=True)
    path = storage.project_path(project_id, "transcripts/mixed.json")
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "id": "transcript",
                "project_id": project_id,
                "media_id": "media",
                "source": "SCREEN",
                "audio_stream_index": 1,
                "language": "pt",
                "duration_ms": 10_000,
                "engine": "fixture",
                "model": "fixture",
                "segments": [
                    {
                        "start_ms": 0,
                        "end_ms": 2_000,
                        "text": "word segment",
                        "words": [{"start_ms": 0, "end_ms": 1_000, "text": "word"}],
                    },
                    {"start_ms": 2_000, "end_ms": 4_000, "text": "segment fallback"},
                ],
            }
        ),
        encoding="utf-8",
    )
    engine = build_engine(storage.root / "metadata.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=project_id, name="Captions"))
        session.flush()
        session.add(
            MediaModel(
                id="media",
                project_id=project_id,
                role=MediaRole.SCREEN,
                relative_path=f"projects/{project_id}/screen.mp4",
                original_filename="screen.mp4",
                size_bytes=1,
                sha256="a" * 64,
                probe_data={"duration_ms": 10_000, "audio_streams": []},
            )
        )
        session.flush()
        session.add(
            TranscriptModel(
                id="transcript",
                project_id=project_id,
                media_id="media",
                cache_key="b" * 64,
                relative_path="transcripts/mixed.json",
                language="pt",
                duration_ms=10_000,
            )
        )
        session.flush()
        session.add(
            CandidateModel(
                id="candidate",
                project_id=project_id,
                transcript_id="transcript",
                start_ms=0,
                end_ms=4_000,
                title="Candidate",
                reasons=[],
                context={},
                signals={},
            )
        )

    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(storage=storage))})
    with factory() as session:
        result = candidate_captions(project_id, "candidate", request, session)

    assert result.timing_source == "WORDS_AND_SEGMENTS"
    assert [cue.text for cue in result.items] == ["word", "segment fallback"]
    assert result.items[0].words is not None
    assert result.items[1].words is None
