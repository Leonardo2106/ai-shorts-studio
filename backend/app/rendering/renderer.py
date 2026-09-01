from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue

from app.core.errors import AppError
from app.core.settings import Settings
from app.projects.storage import ProjectStorage
from app.rendering.captions import build_ass_document
from app.rendering.command import FFmpegCommandBuilder
from app.rendering.filter_graph import FilterGraphBuilder
from app.rendering.schemas import RenderPlan


@dataclass(frozen=True)
class RenderedFile:
    path: Path
    size_bytes: int
    duration_ms: int
    width: int
    height: int
    has_audio: bool


class Renderer:
    def __init__(self, settings: Settings, storage: ProjectStorage) -> None:
        self.settings = settings
        self.storage = storage
        self.graph_builder = FilterGraphBuilder()
        self.command_builder = FFmpegCommandBuilder(
            settings.ffmpeg_binary,
            max_output_bytes=settings.max_render_output_bytes,
        )

    def ensure_available(self) -> None:
        if shutil.which(self.settings.ffmpeg_binary) is None or shutil.which(self.settings.ffprobe_binary) is None:
            raise AppError(
                "FFMPEG_UNAVAILABLE",
                "FFmpeg and ffprobe are required. Install them or configure their backend paths.",
                status_code=503,
            )

    def cleanup_orphans(self) -> int:
        """Remove only renderer-owned incomplete files left by an interrupted process."""
        removed = 0
        partial_pattern = re.compile(r"^\.[^.]+\.[0-9a-f]{32}\.partial\.mp4$")
        ass_pattern = re.compile(r"^captions-[0-9a-f]{32}\.ass$")
        for project_directory in self.storage.projects_root.iterdir():
            if not project_directory.is_dir() or project_directory.is_symlink():
                continue
            try:
                project_id = project_directory.name
                self.storage.project_dir(project_id)
            except AppError:
                continue
            for relative_directory, pattern in (
                ("previews", partial_pattern),
                ("renders", partial_pattern),
                ("temp", ass_pattern),
            ):
                directory = self.storage.project_path(project_id, relative_directory)
                if not directory.is_dir() or directory.is_symlink():
                    continue
                for candidate in directory.iterdir():
                    if pattern.fullmatch(candidate.name):
                        try:
                            safe_candidate = self.storage.project_path(
                                project_id, Path(relative_directory) / candidate.name
                            )
                        except AppError:
                            continue
                        if safe_candidate.is_file():
                            safe_candidate.unlink()
                            removed += 1
        return removed

    def render(
        self,
        plan: RenderPlan,
        destination: Path,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> RenderedFile:
        self.ensure_available()
        if plan.clip.duration_ms > self.settings.max_render_duration_ms:
            raise AppError("RENDER_TOO_LONG", "The selected clip exceeds the render duration limit.", status_code=422)
        if destination.exists():
            raise AppError("RENDER_OUTPUT_EXISTS", "The render output already exists.", status_code=409)
        destination.parent.mkdir(parents=False, exist_ok=True)
        self.storage.make_private(destination.parent, directory=True)
        self._require_space(destination.parent, plan)
        token = uuid.uuid4().hex
        temporary = destination.with_name(f".{destination.stem}.{token}.partial{destination.suffix}")
        ass_path = destination.parent.parent / "temp" / f"captions-{token}.ass"
        ass_document = build_ass_document(plan)
        if ass_document is not None:
            ass_path.parent.mkdir(parents=False, exist_ok=True)
            self.storage.make_private(ass_path.parent, directory=True)
            ass_path.write_text(ass_document, encoding="utf-8")
            self.storage.make_private(ass_path)
        try:
            graph = self.graph_builder.build(plan, ass_path=ass_path if ass_document is not None else None)
            command = self.command_builder.build(plan, graph, temporary)
            progress(0.02)
            stderr = self._execute(command, plan.clip.duration_ms, temporary.parent, progress, cancelled)
            if cancelled():
                raise AppError("JOB_CANCELLED", "Rendering was cancelled.", status_code=409)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise self._render_failure(stderr)
            if temporary.stat().st_size >= self.settings.max_render_output_bytes:
                raise AppError(
                    "RENDER_OUTPUT_TOO_LARGE",
                    "The rendered file reached the configured output size limit.",
                    status_code=422,
                    details={"max_output_bytes": self.settings.max_render_output_bytes},
                )
            validation = self._validate(temporary, plan)
            self.storage.atomic_replace(temporary, destination)
            progress(0.99)
            return RenderedFile(
                path=destination,
                size_bytes=destination.stat().st_size,
                duration_ms=validation.duration_ms,
                width=validation.width,
                height=validation.height,
                has_audio=validation.has_audio,
            )
        finally:
            temporary.unlink(missing_ok=True)
            ass_path.unlink(missing_ok=True)

    def _execute(
        self,
        command: list[str],
        duration_ms: int,
        output_directory: Path,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> str:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError as exc:
            raise AppError("FFMPEG_UNAVAILABLE", "Could not start FFmpeg.", status_code=503) from exc
        stderr_chunks: deque[str] = deque()
        stderr_size = [0]

        def drain_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_chunks.append(line)
                stderr_size[0] += len(line.encode("utf-8", errors="replace"))
                while stderr_chunks and stderr_size[0] > self.settings.render_stderr_max_bytes:
                    stderr_size[0] -= len(stderr_chunks.popleft().encode("utf-8", errors="replace"))

        reader = threading.Thread(target=drain_stderr, name="ffmpeg-stderr", daemon=True)
        reader.start()
        progress_lines: Queue[str | None] = Queue()

        def drain_progress() -> None:
            assert process.stdout is not None
            for progress_line in process.stdout:
                progress_lines.put(progress_line)
            progress_lines.put(None)

        progress_reader = threading.Thread(target=drain_progress, name="ffmpeg-progress", daemon=True)
        progress_reader.start()
        deadline = time.monotonic() + self.settings.ffmpeg_timeout_seconds
        try:
            while True:
                if cancelled():
                    _terminate(process, self.settings.render_cancel_grace_seconds)
                    raise AppError("JOB_CANCELLED", "Rendering was cancelled.", status_code=409)
                if time.monotonic() >= deadline:
                    _terminate(process, self.settings.render_cancel_grace_seconds)
                    raise AppError("FFMPEG_TIMEOUT", "Rendering exceeded the configured timeout.", status_code=500)
                if shutil.disk_usage(output_directory).free < self.settings.min_free_space_bytes:
                    _terminate(process, self.settings.render_cancel_grace_seconds)
                    raise AppError(
                        "INSUFFICIENT_STORAGE",
                        "Rendering stopped to preserve the configured free disk reserve.",
                        status_code=507,
                    )
                try:
                    line = progress_lines.get(timeout=0.1)
                except Empty:
                    if process.poll() is not None:
                        break
                    continue
                if line is None:
                    if process.poll() is not None:
                        break
                    continue
                key, separator, value = line.strip().partition("=")
                if separator and key in {"out_time_us", "out_time_ms"}:
                    try:
                        elapsed_ms = int(value) / 1000
                    except ValueError:
                        continue
                    progress(min(0.97, 0.02 + 0.94 * elapsed_ms / duration_ms))
            reader.join(timeout=1)
            progress_reader.join(timeout=1)
            stderr = "".join(stderr_chunks)
            if process.returncode != 0:
                raise self._render_failure(stderr)
            return stderr
        finally:
            if process.poll() is None:
                _terminate(process, self.settings.render_cancel_grace_seconds)

    def _validate(self, path: Path, plan: RenderPlan) -> RenderedFile:
        try:
            result = subprocess.run(
                [
                    self.settings.ffprobe_binary,
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.ffprobe_timeout_seconds,
                shell=False,
                check=False,
            )
            data = json.loads(result.stdout) if result.returncode == 0 else {}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise AppError(
                "RENDER_VALIDATION_FAILED", "The rendered file could not be validated.", status_code=500
            ) from exc
        streams = data.get("streams", [])
        video = next((item for item in streams if item.get("codec_type") == "video"), None)
        if video is None:
            raise AppError("RENDER_VALIDATION_FAILED", "The rendered file has no video stream.", status_code=500)
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        if (width, height) != (plan.canvas.output_width, plan.canvas.output_height):
            raise AppError("RENDER_VALIDATION_FAILED", "The rendered file has unexpected dimensions.", status_code=500)
        try:
            duration_ms = round(float(data.get("format", {}).get("duration")) * 1000)
        except (TypeError, ValueError):
            duration_ms = 0
        if duration_ms <= 0 or abs(duration_ms - plan.clip.duration_ms) > max(1000, plan.clip.duration_ms // 10):
            raise AppError("RENDER_VALIDATION_FAILED", "The rendered file has an unexpected duration.", status_code=500)
        has_audio = any(item.get("codec_type") == "audio" for item in streams)
        if bool(plan.audio.sources) != has_audio:
            raise AppError(
                "RENDER_VALIDATION_FAILED",
                "The rendered file audio does not match the render plan.",
                status_code=500,
            )
        return RenderedFile(
            path=path,
            size_bytes=path.stat().st_size,
            duration_ms=duration_ms,
            width=width,
            height=height,
            has_audio=has_audio,
        )

    def _render_failure(self, stderr: str) -> AppError:
        sanitized = stderr
        with suppress(OSError):
            sanitized = sanitized.replace(str(self.storage.root), "<storage>")
        lowered = sanitized.lower()
        cause = "FFmpeg could not process the selected media."
        if "invalid data" in lowered or "error while decoding" in lowered:
            cause = "One of the selected media sources could not be decoded."
        elif "no space left" in lowered:
            cause = "There is not enough disk space to finish the render."
        technical = "\n".join(sanitized.strip().splitlines()[-12:])[:4000]
        return AppError("FFMPEG_RENDER_FAILED", cause, status_code=422, details={"technical": technical})

    def _require_space(self, directory: Path, plan: RenderPlan) -> None:
        required_output = self.settings.max_render_output_bytes
        if shutil.disk_usage(directory).free < self.settings.min_free_space_bytes + required_output:
            raise AppError(
                "INSUFFICIENT_STORAGE",
                "Not enough free disk space for rendering.",
                status_code=507,
                details={"required_output_bytes": required_output},
            )


def _terminate(process: subprocess.Popen[str], grace_seconds: float) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=grace_seconds)
