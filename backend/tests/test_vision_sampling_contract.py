from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import AppError
from app.db.models import CandidateModel, MediaModel, MediaRole, ProjectModel, TranscriptModel
from app.db.session import build_engine, build_session_factory, initialize_database
from app.projects.storage import ProjectStorage
from app.vision.schemas import VisionAnalysisRequest, VisionSignals
from app.vision.service import VisionService


class _Frame:
    shape = (1_000, 2_000, 3)


class _Difference:
    @staticmethod
    def mean() -> float:
        return 32.0


class _Capture:
    def __init__(self) -> None:
        self.positions: list[float] = []
        self.released = False

    @staticmethod
    def isOpened() -> bool:
        return True

    @staticmethod
    def get(prop: int) -> float:
        return 100.0 if prop == 1 else 10.0

    def set(self, _prop: int, value: float) -> None:
        self.positions.append(value)

    @staticmethod
    def read() -> tuple[bool, _Frame]:
        return True, _Frame()

    def release(self) -> None:
        self.released = True


class _FakeCv2:
    CAP_PROP_FRAME_COUNT = 1
    CAP_PROP_FPS = 2
    CAP_PROP_POS_MSEC = 3
    COLOR_BGR2GRAY = 4

    def __init__(self) -> None:
        self.capture = _Capture()
        self.resize_calls: list[tuple[int, int]] = []
        self.thread_limit: int | None = None

    def setNumThreads(self, value: int) -> None:
        self.thread_limit = value

    def VideoCapture(self, _path: str) -> _Capture:
        return self.capture

    def resize(self, frame: _Frame, dimensions: tuple[int, int]) -> _Frame:
        self.resize_calls.append(dimensions)
        return frame

    @staticmethod
    def cvtColor(_frame: _Frame, _color: int) -> object:
        return object()

    @staticmethod
    def absdiff(_left: object, _right: object) -> _Difference:
        return _Difference()


def test_opencv_sampling_is_bounded_and_resizes_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cv2 = _FakeCv2()
    monkeypatch.setattr("app.vision.service.importlib.import_module", lambda _name: fake_cv2)
    request = VisionAnalysisRequest(
        media_id="fixture-media",
        candidate_ids=["candidate"],
        sample_interval_ms=1_000,
        max_samples=3,
        max_dimension=480,
    )

    signals = VisionService._opencv(Path("deterministic.mp4"), request, lambda _value: None, lambda: False)

    assert signals.available is True
    assert signals.sample_count == 3
    assert len(fake_cv2.capture.positions) == 3
    assert fake_cv2.capture.positions == [0.0, pytest.approx(3333.333), pytest.approx(6666.667)]
    assert fake_cv2.resize_calls == [(480, 240)] * 3
    assert signals.motion_intensity == pytest.approx(0.5)
    assert fake_cv2.capture.released is True
    assert fake_cv2.thread_limit == 1
    assert "emotion" in (signals.note or "")


def test_opencv_sampling_honors_cancellation_without_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cv2 = _FakeCv2()
    monkeypatch.setattr("app.vision.service.importlib.import_module", lambda _name: fake_cv2)

    with pytest.raises(AppError, match="cancelled") as failure:
        VisionService._opencv(
            Path("deterministic.mp4"),
            VisionAnalysisRequest(media_id="fixture-media", candidate_ids=["candidate"], max_samples=2),
            lambda _value: None,
            lambda: True,
        )

    assert failure.value.code == "JOB_CANCELLED"
    assert fake_cv2.capture.positions == []
    assert fake_cv2.capture.released is True


def test_vision_applies_distinct_windowed_signals_with_budget_and_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, media_id = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f", "media"
    storage = ProjectStorage(tmp_path / "storage")
    storage.project_dir(project_id, create=True)
    engine = build_engine(storage.root / "metadata.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=project_id, name="Vision"))
        session.flush()
        session.add(
            MediaModel(
                id=media_id,
                project_id=project_id,
                role=MediaRole.SCREEN,
                relative_path=f"projects/{project_id}/screen.mp4",
                original_filename="screen.mp4",
                size_bytes=1,
                sha256="a" * 64,
                probe_data={"duration_ms": 20_000, "audio_streams": []},
            )
        )
        session.flush()
        session.add(
            TranscriptModel(
                id="transcript",
                project_id=project_id,
                media_id=media_id,
                cache_key="b" * 64,
                relative_path="transcripts/vision.json",
                language="pt",
                duration_ms=20_000,
            )
        )
        session.flush()
        session.add_all(
            [
                CandidateModel(
                    id="early",
                    project_id=project_id,
                    transcript_id="transcript",
                    start_ms=0,
                    end_ms=5_000,
                    title="Early",
                    reasons=[],
                    context={"text": "early"},
                    signals={},
                ),
                CandidateModel(
                    id="late",
                    project_id=project_id,
                    transcript_id="transcript",
                    start_ms=10_000,
                    end_ms=15_000,
                    title="Late",
                    reasons=[],
                    context={"text": "late"},
                    signals={},
                ),
            ]
        )
    service = VisionService(storage, factory)
    calls: list[dict[str, tuple[int, int]] | None] = []

    def fake_run(
        _path: Path,
        _request: VisionAnalysisRequest,
        _progress: object,
        _cancelled: object,
        *,
        windows: dict[str, tuple[int, int]] | None = None,
    ) -> dict[str, VisionSignals]:
        calls.append(windows)
        return {
            "early": VisionSignals(available=True, sample_count=2, max_dimension=480, motion_intensity=0.1),
            "late": VisionSignals(available=True, sample_count=2, max_dimension=480, motion_intensity=0.9),
        }

    monkeypatch.setattr(service, "is_available", lambda: True)
    monkeypatch.setattr(service, "_run_isolated", fake_run)
    request = VisionAnalysisRequest(media_id=media_id, candidate_ids=["early", "late"], max_samples=4)

    first = service.analyze(project_id, request, lambda _value: None, lambda: False)
    second = service.analyze(project_id, request, lambda _value: None, lambda: False)

    assert calls == [{"early": (0, 5_000), "late": (10_000, 15_000)}]
    assert first["candidate_signals"]["early"]["motion_intensity"] == 0.1
    assert first["candidate_signals"]["late"]["motion_intensity"] == 0.9
    assert second["cache_hit"] is True
    with factory() as session:
        early, late = session.get(CandidateModel, "early"), session.get(CandidateModel, "late")
        assert early is not None and late is not None
        assert (early.signals["motion_intensity"], late.signals["motion_intensity"]) == (0.1, 0.9)


def _blocking_worker(_path: str, _request: dict[str, object], _output: Any) -> None:
    time.sleep(10)


def _successful_worker(_path: str, request: dict[str, object], output: Any) -> None:
    output.put(
        {
            "ok": True,
            "result": {
                "available": True,
                "sample_count": 1,
                "max_dimension": request["max_dimension"],
                "motion_intensity": 0.25,
                "face_present_ratio": None,
            },
        }
    )


def test_isolated_worker_timeout_is_forced_and_following_worker_can_run() -> None:
    request = VisionAnalysisRequest(media_id="fixture-media", candidate_ids=["candidate"], max_samples=1)
    blocked = VisionService(object(), object(), timeout_seconds=0.1, worker_target=_blocking_worker)  # type: ignore[arg-type]

    with pytest.raises(AppError) as failure:
        blocked._run_isolated(Path("fixture.mp4"), request, lambda _value: None, lambda: False)
    assert failure.value.code == "VISION_TIMEOUT"

    following = VisionService(object(), object(), timeout_seconds=2, worker_target=_successful_worker)  # type: ignore[arg-type]
    result = following._run_isolated(Path("fixture.mp4"), request, lambda _value: None, lambda: False)
    assert result.motion_intensity == 0.25
