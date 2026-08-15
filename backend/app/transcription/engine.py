from __future__ import annotations

import importlib.util
import multiprocessing
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import AppError
from app.db.models import TranscriptionPreset
from app.transcription.schemas import TranscriptSegment


@dataclass(frozen=True)
class EngineResult:
    language: str | None
    duration_ms: int
    segments: list[TranscriptSegment]


class TranscriptionEngine(Protocol):
    @property
    def name(self) -> str: ...

    def transcribe(
        self,
        audio_path: Path,
        *,
        model_name: str,
        language: str | None,
        word_timestamps: bool,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> EngineResult: ...


PRESET_MODELS: dict[TranscriptionPreset, str] = {
    TranscriptionPreset.ECONOMY: "tiny",
    TranscriptionPreset.BALANCED: "base",
    TranscriptionPreset.QUALITY: "small",
    TranscriptionPreset.MAXIMUM_QUALITY: "large-v3",
}


class FasterWhisperEngine:
    def __init__(self, timeout_seconds: float = 2 * 60 * 60) -> None:
        self.timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "faster-whisper"

    def available(self) -> bool:
        if importlib.util.find_spec("faster_whisper") is None:
            return False
        try:
            importlib.import_module("faster_whisper")
        except Exception:
            return False
        return True

    def transcribe(
        self,
        audio_path: Path,
        *,
        model_name: str,
        language: str | None,
        word_timestamps: bool,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> EngineResult:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_whisper_worker,
            args=(sender, str(audio_path), model_name, language, word_timestamps),
            daemon=True,
            name="ai-shorts-whisper",
        )
        try:
            process.start()
        except (OSError, RuntimeError) as exc:
            receiver.close()
            sender.close()
            raise AppError(
                "TRANSCRIPTION_PROCESS_FAILED",
                "Could not start the local transcription process.",
                status_code=500,
            ) from exc
        sender.close()
        deadline = time.monotonic() + self.timeout_seconds
        detected_language: str | None = None
        duration_ms = 0
        segments: list[TranscriptSegment] = []
        try:
            while True:
                if cancelled():
                    _terminate_process(process)
                    raise AppError("JOB_CANCELLED", "Transcription was cancelled.", status_code=409)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_process(process)
                    raise AppError("WHISPER_TIMEOUT", "Local transcription exceeded its deadline.", status_code=500)
                if receiver.poll(min(0.2, remaining)):
                    kind, payload = receiver.recv()
                    if kind == "info":
                        detected_language = payload["language"]
                        duration_ms = int(payload["duration_ms"])
                    elif kind == "segment":
                        segment = TranscriptSegment.model_validate(payload)
                        segments.append(segment)
                        progress(min(0.99, segment.end_ms / duration_ms) if duration_ms else 0.5)
                    elif kind == "error":
                        code = str(payload.get("code", "TRANSCRIPTION_FAILED"))
                        status = 503 if code == "WHISPER_UNAVAILABLE" else 500
                        raise AppError(code, str(payload["message"]), status_code=status)
                    elif kind == "done":
                        progress(1.0)
                        process.join(timeout=2)
                        return EngineResult(
                            language=detected_language,
                            duration_ms=duration_ms,
                            segments=segments,
                        )
                elif not process.is_alive():
                    raise AppError(
                        "TRANSCRIPTION_FAILED", "Local transcription process exited unexpectedly.", status_code=500
                    )
        except (EOFError, OSError) as exc:
            raise AppError("TRANSCRIPTION_FAILED", "Local transcription process failed.", status_code=500) from exc
        finally:
            receiver.close()
            if process.is_alive():
                _terminate_process(process)


def _terminate_process(process: BaseProcess) -> None:
    if not process.is_alive():
        process.join(timeout=0.1)
        return
    process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)


def _whisper_worker(
    connection: Connection,
    audio_path: str,
    model_name: str,
    language: str | None,
    word_timestamps: bool,
) -> None:
    try:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError:
            connection.send(
                (
                    "error",
                    {
                        "code": "WHISPER_UNAVAILABLE",
                        "message": "faster-whisper is not installed. Install the backend 'whisper' extra.",
                    },
                )
            )
            return
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        raw_segments, info = model.transcribe(audio_path, language=language, word_timestamps=word_timestamps)
        duration_ms = max(0, round(float(info.duration) * 1000))
        connection.send(("info", {"language": info.language, "duration_ms": duration_ms}))
        for raw in raw_segments:
            words: list[dict[str, Any]] | None = None
            if word_timestamps and raw.words is not None:
                words = [
                    {
                        "start_ms": max(0, round(float(word.start) * 1000)),
                        "end_ms": max(0, round(float(word.end) * 1000)),
                        "text": word.word,
                    }
                    for word in raw.words
                ]
            connection.send(
                (
                    "segment",
                    {
                        "start_ms": max(0, round(float(raw.start) * 1000)),
                        "end_ms": max(0, round(float(raw.end) * 1000)),
                        "text": raw.text.strip(),
                        "words": words,
                    },
                )
            )
        connection.send(("done", {}))
    except Exception:
        connection.send(("error", {"code": "TRANSCRIPTION_FAILED", "message": "Local transcription failed."}))
    finally:
        connection.close()
