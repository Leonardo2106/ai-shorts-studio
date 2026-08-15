from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.core.settings import Settings
from app.db.models import MediaModel, MediaRole, ProjectModel, TranscriptionPreset, TranscriptModel
from app.db.session import build_engine, build_session_factory, initialize_database
from app.projects.storage import ProjectStorage
from app.transcription.engine import EngineResult
from app.transcription.schemas import TranscriptionRequest, TranscriptSegment
from app.transcription.service import TranscriptionService


class FakeEngine:
    name = "fake-whisper"
    calls = 0

    def transcribe(self, *_: object, **kwargs: object) -> EngineResult:
        self.calls += 1
        progress = kwargs["progress"]
        assert callable(progress)
        progress(1.0)
        return EngineResult(
            language="pt",
            duration_ms=1200,
            segments=[TranscriptSegment(start_ms=0, end_ms=1200, text="olá")],
        )


def test_project_and_media_metadata_persist_without_video_blob(tmp_path: Path) -> None:
    engine = build_engine(tmp_path / "metadata.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory() as session:
        project = ProjectModel(id="2ef83f41-3d17-4cf1-a09b-8ca04882dd0f", name="Demo")
        media = MediaModel(
            project=project,
            role=MediaRole.SCREEN,
            relative_path="projects/2ef83f41-3d17-4cf1-a09b-8ca04882dd0f/screen.mp4",
            original_filename="screen.mp4",
            size_bytes=3,
            sha256="a" * 64,
            probe_data={"duration_ms": 1000, "audio_streams": []},
        )
        session.add(media)
        session.commit()

    with factory() as session:
        saved = session.scalar(select(ProjectModel).where(ProjectModel.name == "Demo"))
        assert saved is not None
        assert saved.webcam_offset_ms == 0
        assert saved.media[0].relative_path.startswith("projects/")
        assert not hasattr(saved.media[0], "video_blob")


def test_transcript_cache_avoids_second_engine_run(tmp_path: Path, monkeypatch: object) -> None:
    storage = ProjectStorage(tmp_path / "storage")
    project_id = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"
    project_dir = storage.project_dir(project_id, create=True)
    media_path = project_dir / "screen.mp4"
    media_path.write_bytes(b"not used by fake extractor")
    db_engine = build_engine(storage.root / "metadata.sqlite3")
    initialize_database(db_engine)
    factory = build_session_factory(db_engine)
    with factory() as session:
        session.add(ProjectModel(id=project_id, name="Cache"))
        session.add(
            MediaModel(
                id="2c4d3443-a8f1-4d75-befb-0dab0a3b37e9",
                project_id=project_id,
                role=MediaRole.SCREEN,
                relative_path=storage.relative(media_path),
                original_filename="screen.mp4",
                size_bytes=1,
                sha256="b" * 64,
                probe_data={"audio_streams": [{"index": 1}]},
            )
        )
        session.commit()

    fake = FakeEngine()
    service = TranscriptionService(Settings(storage_root=storage.root), storage, factory, engine=fake)
    audio_path = project_dir / "fake.wav"
    audio_path.write_bytes(b"audio")
    monkeypatch.setattr(service, "_extract_audio", lambda *_: audio_path)
    request = TranscriptionRequest(
        media_id="2c4d3443-a8f1-4d75-befb-0dab0a3b37e9",
        audio_stream_index=1,
        preset=TranscriptionPreset.BALANCED,
    )

    first = service.run(project_id, request, lambda _: None, lambda: False)
    second = service.run(project_id, request, lambda _: None, lambda: False)

    assert first["cached"] is False
    assert second["cached"] is True
    assert fake.calls == 1
    with factory() as session:
        assert len(list(session.scalars(select(TranscriptModel)))) == 1
