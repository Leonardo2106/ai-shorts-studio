from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.core.settings import Settings
from app.db.models import MediaModel, TranscriptModel
from app.projects.storage import ProjectStorage
from app.transcription.engine import PRESET_MODELS, FasterWhisperEngine, TranscriptionEngine
from app.transcription.schemas import TranscriptDocument, TranscriptionRequest


class TranscriptionService:
    def __init__(
        self,
        settings: Settings,
        storage: ProjectStorage,
        session_factory: sessionmaker[Session],
        engine: TranscriptionEngine | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.session_factory = session_factory
        self.engine = engine or FasterWhisperEngine(settings.whisper_timeout_seconds)
        self._cache_locks: dict[str, threading.Lock] = {}
        self._cache_locks_guard = threading.Lock()
        self._wav_locks: dict[str, threading.Lock] = {}
        self._wav_locks_guard = threading.Lock()

    def _cache_key(self, media: MediaModel, request: TranscriptionRequest) -> str:
        payload = {
            "schema": 1,
            # Caches stay inside a project's trust boundary even for identical source files.
            "project_id": media.project_id,
            "sha256": media.sha256,
            "audio_stream_index": request.audio_stream_index,
            "engine": self.engine.name,
            "model": PRESET_MODELS[request.preset],
            "language": request.language,
            "word_timestamps": request.word_timestamps,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def ensure_available(self) -> None:
        if not self.is_available():
            raise AppError(
                "WHISPER_UNAVAILABLE",
                "faster-whisper is not installed or failed its local import preflight.",
                status_code=503,
            )

    def is_available(self) -> bool:
        checker = getattr(self.engine, "available", None)
        return True if checker is None else bool(checker())

    def run(
        self,
        project_id: str,
        request: TranscriptionRequest,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, str | bool]:
        self.ensure_available()
        with self.session_factory() as session:
            media = session.scalar(
                select(MediaModel).where(MediaModel.id == request.media_id, MediaModel.project_id == project_id)
            )
            if media is None:
                raise AppError("MEDIA_NOT_FOUND", "Media was not found.", status_code=404)
            audio_indexes = {item.get("index") for item in media.probe_data.get("audio_streams", [])}
            if request.audio_stream_index not in audio_indexes:
                raise AppError(
                    "AUDIO_STREAM_NOT_FOUND",
                    "Selected audio stream does not exist in this media.",
                    status_code=422,
                    details={"available_indexes": sorted(i for i in audio_indexes if i is not None)},
                )
            cache_key = self._cache_key(media, request)
            with self._cache_lock(cache_key):
                cached = session.scalar(select(TranscriptModel).where(TranscriptModel.cache_key == cache_key))
                if cached is not None:
                    cached_path = self.storage.project_path(
                        project_id, Path("transcripts") / Path(cached.relative_path).name
                    )
                    if cached_path.is_file():
                        return {"transcript_id": cached.id, "cached": True}
                    session.delete(cached)
                    session.commit()
                audio_path = self._extract_audio(media, request.audio_stream_index, cancelled)
                if cancelled():
                    raise AppError("JOB_CANCELLED", "Transcription was cancelled.", status_code=409)
                result = self.engine.transcribe(
                    audio_path,
                    model_name=PRESET_MODELS[request.preset],
                    language=request.language,
                    word_timestamps=request.word_timestamps,
                    progress=progress,
                    cancelled=cancelled,
                )
                transcript_id = str(uuid.uuid4())
                document = TranscriptDocument(
                    id=transcript_id,
                    project_id=project_id,
                    media_id=media.id,
                    source=media.role.value,
                    audio_stream_index=request.audio_stream_index,
                    language=result.language,
                    duration_ms=result.duration_ms,
                    engine=self.engine.name,
                    model=PRESET_MODELS[request.preset],
                    segments=result.segments,
                )
                output_path = self.storage.project_path(project_id, Path("transcripts") / f"{cache_key}.json")
                output_path.parent.mkdir(parents=False, exist_ok=True)
                self.storage.make_private(output_path.parent, directory=True)
                serialized = document.model_dump_json(indent=2)
                self._require_disk_space(output_path.parent, len(serialized.encode("utf-8")))
                temp = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
                try:
                    temp.write_text(serialized, encoding="utf-8")
                    self.storage.atomic_replace(temp, output_path)
                finally:
                    temp.unlink(missing_ok=True)
                transcript = TranscriptModel(
                    id=transcript_id,
                    project_id=project_id,
                    media_id=media.id,
                    cache_key=cache_key,
                    relative_path=self.storage.relative(output_path),
                    language=result.language,
                    duration_ms=result.duration_ms,
                )
                session.add(transcript)
                session.commit()
                return {"transcript_id": transcript_id, "cached": False}

    def _cache_lock(self, cache_key: str) -> threading.Lock:
        with self._cache_locks_guard:
            return self._cache_locks.setdefault(cache_key, threading.Lock())

    def _extract_audio(self, media: MediaModel, stream_index: int, cancelled: Callable[[], bool]) -> Path:
        if not shutil.which(self.settings.ffmpeg_binary):
            raise AppError(
                "FFMPEG_UNAVAILABLE",
                "FFmpeg was not found. Install it or configure AI_SHORTS_FFMPEG_BINARY.",
                status_code=503,
            )
        source = self.storage.project_path(media.project_id, Path(media.relative_path).name)
        wav_key = hashlib.sha256(
            json.dumps(
                {"schema": 1, "sha256": media.sha256, "audio_stream_index": stream_index},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        destination = self.storage.project_path(media.project_id, Path("cache") / f"audio-{wav_key}.wav")
        destination.parent.mkdir(parents=False, exist_ok=True)
        self.storage.make_private(destination.parent, directory=True)
        with self._wav_lock(wav_key):
            if destination.is_file() and destination.stat().st_size > 0:
                return destination
            duration_ms = int(media.probe_data.get("duration_ms") or 0)
            self._require_disk_space(destination.parent, duration_ms * 32 + 44)
            temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            try:
                try:
                    process = subprocess.Popen(
                        [
                            self.settings.ffmpeg_binary,
                            "-nostdin",
                            "-v",
                            "error",
                            "-i",
                            str(source),
                            "-map",
                            f"0:{stream_index}",
                            "-vn",
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "16000",
                            "-ac",
                            "1",
                            "-f",
                            "wav",
                            "-y",
                            str(temp),
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                    )
                except OSError as exc:
                    raise AppError("FFMPEG_FAILED", "Could not execute FFmpeg.", status_code=503) from exc
                deadline = time.monotonic() + self.settings.ffmpeg_timeout_seconds
                while process.poll() is None:
                    if cancelled():
                        _terminate_subprocess(process)
                        raise AppError("JOB_CANCELLED", "Transcription was cancelled.", status_code=409)
                    if time.monotonic() >= deadline:
                        _terminate_subprocess(process)
                        raise AppError("FFMPEG_TIMEOUT", "Audio extraction timed out.", status_code=500)
                    time.sleep(0.1)
                if process.returncode != 0 or not temp.is_file() or temp.stat().st_size == 0:
                    raise AppError(
                        "FFMPEG_FAILED",
                        "Could not extract the selected audio stream.",
                        status_code=422,
                    )
                self.storage.atomic_replace(temp, destination)
                return destination
            finally:
                temp.unlink(missing_ok=True)

    def _wav_lock(self, wav_key: str) -> threading.Lock:
        with self._wav_locks_guard:
            return self._wav_locks.setdefault(wav_key, threading.Lock())

    def _require_disk_space(self, directory: Path, estimated_bytes: int) -> None:
        required = self.settings.min_free_space_bytes + max(0, estimated_bytes)
        if shutil.disk_usage(directory).free < required:
            raise AppError(
                "INSUFFICIENT_STORAGE",
                "Not enough free disk space for transcription artifacts.",
                status_code=507,
                details={"estimated_bytes": max(0, estimated_bytes)},
            )


def _terminate_subprocess(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
