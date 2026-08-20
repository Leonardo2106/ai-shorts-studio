from __future__ import annotations

import hashlib
import importlib
import json
import multiprocessing
import queue
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.db.models import AnalysisCacheModel, CandidateModel, MediaModel, MediaRole, ProjectModel
from app.projects.storage import ProjectStorage
from app.vision.schemas import VisionAnalysisRequest, VisionAnalysisResult, VisionSignals


class VisionService:
    def __init__(
        self,
        storage: ProjectStorage,
        session_factory: sessionmaker[Session],
        *,
        timeout_seconds: float = 300.0,
        worker_target: Callable[[str, dict[str, object], Any], None] | None = None,
    ) -> None:
        self.storage = storage
        self.session_factory = session_factory
        self.timeout_seconds = timeout_seconds
        self.worker_target = worker_target or _opencv_process_entry

    @staticmethod
    def is_available() -> bool:
        return importlib.util.find_spec("cv2") is not None

    def analyze(
        self,
        project_id: str,
        request: VisionAnalysisRequest,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, object]:
        with self.session_factory() as session:
            project = session.get(ProjectModel, project_id)
            media = session.get(MediaModel, request.media_id)
            if project is None:
                raise AppError("PROJECT_NOT_FOUND", "Project was not found.", status_code=404)
            if media is None or media.project_id != project_id:
                raise AppError("MEDIA_NOT_FOUND", "Media was not found.", status_code=404)
            candidates = list(
                session.scalars(
                    select(CandidateModel).where(
                        CandidateModel.project_id == project_id,
                        CandidateModel.id.in_(request.candidate_ids),
                    )
                ).all()
            )
            if len(candidates) != len(set(request.candidate_ids)):
                raise AppError("CANDIDATE_NOT_FOUND", "One or more candidates were not found.", status_code=404)
            if len(candidates) > request.max_samples:
                raise AppError(
                    "VISION_SAMPLE_BUDGET_TOO_SMALL",
                    "max_samples must allow at least one sample per candidate.",
                    status_code=422,
                )
            media_offset = project.webcam_offset_ms if media.role == MediaRole.WEBCAM else 0
            media_duration = int(media.probe_data["duration_ms"])
            windows = {
                candidate.id: (
                    max(0, candidate.start_ms - media_offset),
                    min(media_duration, candidate.end_ms - media_offset),
                )
                for candidate in candidates
            }
            if any(end <= start for start, end in windows.values()):
                raise AppError(
                    "CANDIDATE_OUTSIDE_MEDIA",
                    "One or more candidate windows do not overlap the selected media.",
                    status_code=422,
                )
            cache_material = {
                "v": 2,
                "sha256": media.sha256,
                "sample_interval_ms": request.sample_interval_ms,
                "max_samples": request.max_samples,
                "max_dimension": request.max_dimension,
                "windows": windows,
            }
            cache_key = hashlib.sha256(json.dumps(cache_material, sort_keys=True).encode()).hexdigest()
            cached = session.scalar(select(AnalysisCacheModel).where(AnalysisCacheModel.cache_key == cache_key))
            if cached is not None:
                cached_result = VisionAnalysisResult.model_validate(cached.result_data)
                session.close()
                self._apply_signals(project_id, cached_result.candidate_signals)
                return cached_result.model_copy(update={"cache_hit": True}).model_dump(mode="json")
            path = self.storage.project_path(project_id, Path(media.relative_path).name)
        if not self.is_available():
            unavailable = {
                candidate_id: VisionSignals(
                    available=False,
                    sample_count=0,
                    max_dimension=request.max_dimension,
                    motion_intensity=0,
                    note="OpenCV is not installed; local scoring remains available.",
                )
                for candidate_id in windows
            }
            return VisionAnalysisResult(candidate_signals=unavailable).model_dump(mode="json")
        raw_result = self._run_isolated(path, request, progress, cancelled, windows=windows)
        candidate_signals = cast(dict[str, VisionSignals], raw_result)
        result = VisionAnalysisResult(candidate_signals=candidate_signals)
        dumped = result.model_dump(mode="json")
        with self.session_factory.begin() as session:
            session.add(
                AnalysisCacheModel(project_id=project_id, kind="VISION", cache_key=cache_key, result_data=dumped)
            )
        self._apply_signals(project_id, candidate_signals)
        return dumped

    def _apply_signals(self, project_id: str, candidate_signals: dict[str, VisionSignals]) -> None:
        with self.session_factory.begin() as session:
            candidates = session.scalars(
                select(CandidateModel).where(
                    CandidateModel.project_id == project_id,
                    CandidateModel.id.in_(candidate_signals),
                )
            ).all()
            for candidate in candidates:
                result = candidate_signals[candidate.id]
                signals = dict(candidate.signals)
                signals["motion_intensity"] = result.motion_intensity
                signals["vision_sample_count"] = result.sample_count
                if result.face_present_ratio is not None:
                    signals["face_present_ratio"] = result.face_present_ratio
                candidate.signals = signals
                context = dict(candidate.context)
                context["vision_analyzer"] = "OPENCV_LOCAL"
                if context.get("analysis_origin", "LOCAL") == "LOCAL":
                    context["analysis_origin"] = "VISION_LOCAL"
                candidate.context = context

    def _run_isolated(
        self,
        path: Path,
        request: VisionAnalysisRequest,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
        *,
        windows: dict[str, tuple[int, int]] | None = None,
    ) -> VisionSignals | dict[str, VisionSignals]:
        context = multiprocessing.get_context("spawn")
        output: Any = context.Queue(maxsize=1)
        process = context.Process(
            target=self.worker_target,
            args=(
                str(path),
                {**request.model_dump(mode="json"), "_windows": windows}
                if windows is not None
                else request.model_dump(mode="json"),
                output,
            ),
            name="ai-shorts-vision",
            daemon=True,
        )
        process.start()
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while process.is_alive():
                if cancelled():
                    raise AppError("JOB_CANCELLED", "Vision analysis was cancelled.", status_code=409)
                if time.monotonic() >= deadline:
                    raise AppError(
                        "VISION_TIMEOUT", "Vision analysis exceeded its configured timeout.", status_code=504
                    )
                process.join(timeout=0.05)
            try:
                message = output.get(timeout=0.5)
            except queue.Empty as exc:
                raise AppError(
                    "VISION_PROCESS_FAILED", "Vision analysis process exited unexpectedly.", status_code=500
                ) from exc
            if message["ok"]:
                progress(1.0)
                if windows is not None:
                    return {
                        candidate_id: VisionSignals.model_validate(signals)
                        for candidate_id, signals in message["candidate_signals"].items()
                    }
                return VisionSignals.model_validate(message["result"])
            raise AppError("VISION_ANALYSIS_FAILED", "Vision analysis failed in its isolated process.", status_code=422)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=1.0)
            output.cancel_join_thread()
            output.close()

    @staticmethod
    def _opencv(
        path: Path,
        request: VisionAnalysisRequest,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
        *,
        start_ms: int = 0,
        end_ms: int | None = None,
        sample_limit: int | None = None,
    ) -> VisionSignals:
        cv2: Any = importlib.import_module("cv2")
        if hasattr(cv2, "setNumThreads"):
            cv2.setNumThreads(1)
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise AppError("VISION_MEDIA_UNREADABLE", "OpenCV could not open the selected media.", status_code=422)
        duration_ms = max(
            0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT) / max(capture.get(cv2.CAP_PROP_FPS), 0.001) * 1000)
        )
        window_end = min(duration_ms, end_ms) if end_ms is not None else duration_ms
        window_duration = max(0, window_end - start_ms)
        requested = max(1, (window_duration + request.sample_interval_ms - 1) // request.sample_interval_ms)
        count = min(sample_limit or request.max_samples, requested)
        step = window_duration / count if count else request.sample_interval_ms
        previous: Any = None
        motion: list[float] = []
        face_samples = 0
        face_detector: Any = None
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
            cascade_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
            detector = cv2.CascadeClassifier(cascade_path)
            if not detector.empty():
                face_detector = detector
        try:
            for index in range(count):
                if cancelled():
                    raise AppError("JOB_CANCELLED", "Vision analysis was cancelled.", status_code=409)
                capture.set(cv2.CAP_PROP_POS_MSEC, start_ms + index * step)
                ok, frame = capture.read()
                if not ok:
                    continue
                height, width = frame.shape[:2]
                scale = min(1.0, request.max_dimension / max(height, width))
                if scale < 1:
                    frame = cv2.resize(frame, (max(1, int(width * scale)), max(1, int(height * scale))))
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if face_detector is not None and len(
                    face_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
                ):
                    face_samples += 1
                if previous is not None:
                    motion.append(min(1.0, float(cv2.absdiff(gray, previous).mean()) / 64.0))
                previous = gray
                progress((index + 1) / count)
        finally:
            capture.release()
        return VisionSignals(
            available=True,
            sample_count=count,
            max_dimension=request.max_dimension,
            motion_intensity=sum(motion) / len(motion) if motion else 0,
            face_present_ratio=face_samples / count if face_detector is not None and count else None,
            note="Only bounded face presence and pixel motion were measured; no internal emotion is inferred.",
        )


def _opencv_process_entry(path: str, request_data: dict[str, object], output: Any) -> None:
    try:
        windows = cast(dict[str, list[int]] | None, request_data.pop("_windows", None))
        request = VisionAnalysisRequest.model_validate(request_data)
        if windows is None:
            result = VisionService._opencv(Path(path), request, lambda _value: None, lambda: False)
            output.put({"ok": True, "result": result.model_dump(mode="json")})
            return
        base_budget, remainder = divmod(request.max_samples, len(windows))
        candidate_signals: dict[str, dict[str, object]] = {}
        for index, (candidate_id, interval) in enumerate(windows.items()):
            result = VisionService._opencv(
                Path(path),
                request,
                lambda _value: None,
                lambda: False,
                start_ms=interval[0],
                end_ms=interval[1],
                sample_limit=base_budget + (1 if index < remainder else 0),
            )
            candidate_signals[candidate_id] = result.model_dump(mode="json")
        output.put({"ok": True, "candidate_signals": candidate_signals})
    except Exception:
        output.put({"ok": False})
