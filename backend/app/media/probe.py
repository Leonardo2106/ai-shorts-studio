from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.core.errors import AppError
from app.media.schemas import AudioStream, MediaProbe, VideoStream


def _integer(value: object) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _milliseconds(value: object) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return max(0, round(float(str(value)) * 1000))
    except (TypeError, ValueError, OverflowError):
        return None


def _fps(value: object) -> float | None:
    if value in (None, "", "0/0", "N/A"):
        return None
    try:
        result = float(Fraction(str(value)))
        return result if math.isfinite(result) and result > 0 else None
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def _tags(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def parse_ffprobe(payload: dict[str, Any], *, probe_version: str | None = None) -> MediaProbe:
    raw_streams = payload.get("streams")
    if not isinstance(raw_streams, list):
        raise AppError("INVALID_MEDIA", "ffprobe returned no stream information.", status_code=422)
    videos: list[VideoStream] = []
    audios: list[AudioStream] = []
    for raw in raw_streams:
        if not isinstance(raw, dict) or _integer(raw.get("index")) is None:
            continue
        index = int(raw["index"])
        tags = _tags(raw.get("tags"))
        if raw.get("codec_type") == "video":
            videos.append(
                VideoStream(
                    index=index,
                    codec_name=raw.get("codec_name"),
                    width=_integer(raw.get("width")),
                    height=_integer(raw.get("height")),
                    fps=_fps(raw.get("avg_frame_rate") or raw.get("r_frame_rate")),
                    bitrate=_integer(raw.get("bit_rate")),
                    duration_ms=_milliseconds(raw.get("duration")),
                    metadata=tags,
                )
            )
        elif raw.get("codec_type") == "audio":
            audios.append(
                AudioStream(
                    index=index,
                    codec_name=raw.get("codec_name"),
                    sample_rate=_integer(raw.get("sample_rate")),
                    channels=_integer(raw.get("channels")),
                    channel_layout=raw.get("channel_layout"),
                    bitrate=_integer(raw.get("bit_rate")),
                    duration_ms=_milliseconds(raw.get("duration")),
                    language=tags.get("language"),
                    metadata=tags,
                )
            )
    if not videos:
        raise AppError("INVALID_MEDIA", "The uploaded file does not contain a video stream.", status_code=422)
    format_value = payload.get("format")
    raw_format: dict[str, Any] = format_value if isinstance(format_value, dict) else {}
    duration_ms = _milliseconds(raw_format.get("duration"))
    if duration_ms is None:
        durations = [stream.duration_ms for stream in videos if stream.duration_ms is not None]
        durations.extend(stream.duration_ms for stream in audios if stream.duration_ms is not None)
        duration_ms = max(durations, default=0)
    return MediaProbe(
        probe_version=probe_version,
        duration_ms=duration_ms,
        format_name=raw_format.get("format_name"),
        bitrate=_integer(raw_format.get("bit_rate")),
        video_streams=videos,
        audio_streams=audios,
        metadata=_tags(raw_format.get("tags")),
    )


class FFprobeService:
    def __init__(
        self,
        binary: str,
        timeout_seconds: float,
        *,
        max_duration_ms: int = 6 * 60 * 60 * 1000,
        max_width: int = 7680,
        max_height: int = 4320,
        allowed_formats: set[str] | None = None,
    ) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.max_duration_ms = max_duration_ms
        self.max_width = max_width
        self.max_height = max_height
        self.allowed_formats = allowed_formats or {"mov", "mp4"}
        self._version_checked = False
        self._cached_version: str | None = None

    def available_path(self) -> str | None:
        return shutil.which(self.binary)

    def version(self) -> str | None:
        if self._version_checked:
            return self._cached_version
        self._version_checked = True
        if not self.available_path():
            return None
        try:
            completed = subprocess.run(
                [self.binary, "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                shell=False,
                timeout=min(self.timeout_seconds, 5.0),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        first_line = completed.stdout.splitlines()
        self._cached_version = first_line[0][:200] if completed.returncode == 0 and first_line else None
        return self._cached_version

    def inspect(self, path: Path) -> MediaProbe:
        if not self.available_path():
            raise AppError(
                "FFPROBE_UNAVAILABLE",
                "ffprobe was not found. Install FFmpeg or configure AI_SHORTS_FFPROBE_BINARY.",
                status_code=503,
            )
        arguments = [
            self.binary,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-show_entries",
            (
                "format=format_name,duration,bit_rate:"
                "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,"
                "bit_rate,duration,sample_rate,channels,channel_layout:stream_tags=language"
            ),
            "-of",
            "json",
            str(path),
        ]
        return_code, stdout = self._run_capped(arguments)
        if return_code != 0:
            raise AppError(
                "INVALID_MEDIA",
                "ffprobe could not read the uploaded media.",
                status_code=422,
            )
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AppError("INVALID_MEDIA", "ffprobe returned invalid JSON.", status_code=422) from exc
        if not isinstance(payload, dict):
            raise AppError("INVALID_MEDIA", "ffprobe returned an invalid result.", status_code=422)
        probe = parse_ffprobe(payload, probe_version=self.version())
        formats = set((probe.format_name or "").split(","))
        if not formats.intersection(self.allowed_formats):
            raise AppError("UNSUPPORTED_CONTAINER", "Media must use a supported MP4 container.", status_code=422)
        if probe.duration_ms <= 0 or probe.duration_ms > self.max_duration_ms:
            raise AppError(
                "INVALID_MEDIA_DURATION",
                "Media duration is missing or exceeds the configured limit.",
                status_code=422,
                details={"max_duration_ms": self.max_duration_ms},
            )
        stream_durations = [stream.duration_ms for stream in probe.video_streams if stream.duration_ms is not None]
        stream_durations.extend(stream.duration_ms for stream in probe.audio_streams if stream.duration_ms is not None)
        if any(duration > self.max_duration_ms for duration in stream_durations):
            raise AppError(
                "INVALID_MEDIA_DURATION",
                "A media stream exceeds the configured duration limit.",
                status_code=422,
                details={"max_duration_ms": self.max_duration_ms},
            )
        if any(
            stream.width is None
            or stream.height is None
            or stream.width <= 0
            or stream.height <= 0
            or stream.width > self.max_width
            or stream.height > self.max_height
            for stream in probe.video_streams
        ):
            raise AppError("INVALID_VIDEO_DIMENSIONS", "Video dimensions are invalid or too large.", status_code=422)
        if any(stream.fps is not None and stream.fps > 240 for stream in probe.video_streams):
            raise AppError("INVALID_VIDEO_RATE", "Video frame rate exceeds the supported limit.", status_code=422)
        return probe

    def _run_capped(self, arguments: list[str]) -> tuple[int, str]:
        max_output_bytes = 4 * 1024**2
        with tempfile.TemporaryFile(mode="w+b") as output:
            try:
                process = subprocess.Popen(
                    arguments,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
            except OSError as exc:
                raise AppError("FFPROBE_FAILED", "Could not execute ffprobe.", status_code=503) from exc
            deadline = time.monotonic() + self.timeout_seconds
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    _terminate_subprocess(process)
                    raise AppError("FFPROBE_TIMEOUT", "Media inspection timed out.", status_code=422)
                if output.seek(0, 2) > max_output_bytes:
                    _terminate_subprocess(process)
                    raise AppError(
                        "FFPROBE_OUTPUT_TOO_LARGE",
                        "Media metadata is unexpectedly large.",
                        status_code=422,
                    )
                time.sleep(0.02)
            size = output.seek(0, 2)
            if size > max_output_bytes:
                raise AppError("FFPROBE_OUTPUT_TOO_LARGE", "Media metadata is unexpectedly large.", status_code=422)
            output.seek(0)
            return process.returncode, output.read(max_output_bytes + 1).decode("utf-8", errors="strict")


def _terminate_subprocess(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
