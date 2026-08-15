from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.errors import AppError
from app.core.middleware import LocalRequestGuardMiddleware
from app.core.settings import Settings
from app.db.models import JobModel, MediaModel, MediaRole
from app.main import create_app
from app.media.importer import safe_original_filename
from app.media.probe import FFprobeService
from app.projects.storage import ProjectStorage
from app.transcription.engine import FasterWhisperEngine
from app.transcription.schemas import TranscriptionRequest
from app.transcription.service import TranscriptionService


def test_roadmap_zero_forces_single_job_worker() -> None:
    with pytest.raises(ValidationError):
        Settings(job_workers=2)


def test_job_worker_value_is_parsed_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SHORTS_JOB_WORKERS", "1")

    assert Settings(_env_file=None).job_workers == 1


def test_whisper_language_is_normalized_and_rejected_locally() -> None:
    request = TranscriptionRequest(media_id="media", audio_stream_index=0, language=" PT ")
    assert request.language == "pt"
    with pytest.raises(ValidationError):
        TranscriptionRequest(media_id="media", audio_stream_index=0, language="pt-BR")


@pytest.mark.asyncio
async def test_unavailable_whisper_is_rejected_before_job_creation(tmp_path: Path) -> None:
    app = create_app(Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"]))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            project_id = (await client.post("/api/v1/projects", json={"name": "preflight"})).json()["id"]
            media_id = "2c4d3443-a8f1-4d75-befb-0dab0a3b37e9"
            with app.state.session_factory.begin() as session:
                session.add(
                    MediaModel(
                        id=media_id,
                        project_id=project_id,
                        role=MediaRole.SCREEN,
                        relative_path=f"projects/{project_id}/screen.mp4",
                        original_filename="screen.mp4",
                        size_bytes=1,
                        sha256="a" * 64,
                        probe_data={"duration_ms": 1000, "audio_streams": [{"index": 1}]},
                    )
                )

            class UnavailableEngine:
                name = "faster-whisper"

                def available(self) -> bool:
                    return False

            app.state.transcription.engine = UnavailableEngine()
            response = await client.post(
                f"/api/v1/projects/{project_id}/transcription-jobs",
                json={"media_id": media_id, "audio_stream_index": 1},
            )
            with app.state.session_factory() as session:
                job_count = session.scalar(select(func.count(JobModel.id)))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WHISPER_UNAVAILABLE"
    assert job_count == 0


@pytest.mark.asyncio
async def test_request_limit_rejects_declared_oversize_body(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            storage_root=tmp_path / "storage",
            max_upload_bytes=10,
            max_request_bytes=20,
            allowed_hosts=["test"],
        )
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request("GET", "/health", content=b"x", headers={"content-length": "21"})

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


@pytest.mark.asyncio
async def test_cross_origin_mutation_is_rejected(tmp_path: Path) -> None:
    app = create_app(Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"]))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/projects", json={"name": "blocked"}, headers={"origin": "https://evil.test"}
            )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_REJECTED"


def test_windows_and_control_filename_is_sanitized() -> None:
    assert safe_original_filename("C:\\temp\\bad\x00:name.mp4", "screen.mp4") == "badname.mp4"
    assert safe_original_filename("CON.mp4", "screen.mp4") == "screen.mp4"


def test_probe_enforces_duration_limit(tmp_path: Path) -> None:
    media = tmp_path / "screen.mp4"
    media.write_bytes(b"fixture")
    payload = {
        "format": {"format_name": "mov,mp4", "duration": "20"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "duration": "20",
            }
        ],
    }
    service = FFprobeService("ffprobe", 1, max_duration_ms=10_000)
    service._version_checked = True

    with (
        patch.object(service, "available_path", return_value="ffprobe"),
        patch.object(service, "_run_capped", return_value=(0, json.dumps(payload))),
        pytest.raises(AppError) as error,
    ):
        service.inspect(media)

    assert error.value.code == "INVALID_MEDIA_DURATION"


def test_ffprobe_output_is_capped_while_process_runs() -> None:
    service = FFprobeService("ffprobe", 1)

    class CompleteProcess:
        returncode = 0

        def poll(self) -> int:
            return 0

    def fake_popen(*_: object, stdout: Any, **kwargs: object) -> CompleteProcess:
        assert kwargs["shell"] is False
        stdout.write(b"x" * (4 * 1024**2 + 1))
        return CompleteProcess()

    with patch("app.media.probe.subprocess.Popen", side_effect=fake_popen), pytest.raises(AppError) as error:
        service._run_capped(["ffprobe", "media.mp4"])

    assert error.value.code == "FFPROBE_OUTPUT_TOO_LARGE"


def test_whisper_cancellation_terminates_spawned_process(tmp_path: Path) -> None:
    class Receiver:
        def poll(self, _: float) -> bool:
            return False

        def close(self) -> None:
            return None

    class Sender:
        def close(self) -> None:
            return None

    class Process:
        terminated = False
        alive = False

        def start(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.terminated = True
            self.alive = False

        def join(self, timeout: float | None = None) -> None:
            return None

        def kill(self) -> None:
            self.alive = False

    process = Process()
    context = SimpleNamespace(
        Pipe=lambda duplex: (Receiver(), Sender()),
        Process=lambda **kwargs: process,
    )
    engine = FasterWhisperEngine(timeout_seconds=30)

    with (
        patch("app.transcription.engine.multiprocessing.get_context", return_value=context),
        pytest.raises(AppError) as error,
    ):
        engine.transcribe(
            tmp_path / "audio.wav",
            model_name="tiny",
            language=None,
            word_timestamps=False,
            progress=lambda _: None,
            cancelled=lambda: True,
        )

    assert error.value.code == "JOB_CANCELLED"
    assert process.terminated is True


def test_ffmpeg_extraction_cancellation_terminates_process(tmp_path: Path) -> None:
    project_id = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"
    storage = ProjectStorage(tmp_path / "storage")
    project_dir = storage.project_dir(project_id, create=True)
    source = project_dir / "screen.mp4"
    source.write_bytes(b"media")
    media = SimpleNamespace(
        project_id=project_id,
        relative_path=storage.relative(source),
        sha256="a" * 64,
        probe_data={"duration_ms": 1000},
    )

    class Process:
        returncode: int | None = None
        terminated = False

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode or 0

        def kill(self) -> None:
            self.returncode = -9

    process = Process()
    service = TranscriptionService(
        Settings(storage_root=storage.root),
        storage,
        None,  # type: ignore[arg-type]
    )
    with (
        patch("app.transcription.service.shutil.which", return_value="ffmpeg"),
        patch("app.transcription.service.subprocess.Popen", return_value=process) as popen,
        pytest.raises(AppError) as error,
    ):
        service._extract_audio(media, 1, lambda: True)  # type: ignore[arg-type]

    assert error.value.code == "JOB_CANCELLED"
    assert process.terminated is True
    command = popen.call_args.args[0]
    assert command[command.index("-f") + 1] == "wav"
    assert not list((project_dir / "cache").glob("*.tmp"))


@pytest.mark.asyncio
async def test_multipart_is_rejected_before_parser_when_slot_is_busy() -> None:
    release = asyncio.Event()

    async def downstream(scope: object, receive: object, send: object) -> None:
        await release.wait()

    middleware = LocalRequestGuardMiddleware(
        downstream,  # type: ignore[arg-type]
        max_bytes=1024,
        allowed_origins=set(),
        max_concurrent_uploads=1,
        body_timeout_seconds=1,
        min_free_space_bytes=0,
    )
    scope: Any = {
        "type": "http",
        "method": "POST",
        "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    first_messages: list[dict[str, object]] = []
    second_messages: list[dict[str, object]] = []

    async def send_first(message: dict[str, object]) -> None:
        first_messages.append(message)

    async def send_second(message: dict[str, object]) -> None:
        second_messages.append(message)

    first = asyncio.create_task(middleware(scope, receive, send_first))  # type: ignore[arg-type]
    await asyncio.sleep(0)
    await middleware(scope, receive, send_second)  # type: ignore[arg-type]
    release.set()
    await first

    assert second_messages[0]["status"] == 429


@pytest.mark.asyncio
async def test_body_timeout_is_per_receive_inactivity_not_total_deadline() -> None:
    messages = iter(
        [
            {"type": "http.request", "body": b"a", "more_body": True},
            {"type": "http.request", "body": b"b", "more_body": True},
            {"type": "http.request", "body": b"c", "more_body": False},
        ]
    )
    received: list[bytes] = []

    async def receive() -> dict[str, object]:
        await asyncio.sleep(0.02)
        return next(messages)

    async def downstream(scope: object, guarded_receive: Any, send: Any) -> None:
        while True:
            message = await guarded_receive()
            received.append(message["body"])
            if not message["more_body"]:
                break

    async def send(_: dict[str, object]) -> None:
        return None

    middleware = LocalRequestGuardMiddleware(
        downstream,  # type: ignore[arg-type]
        max_bytes=10,
        allowed_origins=set(),
        max_concurrent_uploads=1,
        body_timeout_seconds=0.04,
        min_free_space_bytes=0,
    )
    scope: Any = {"type": "http", "method": "POST", "headers": []}
    await middleware(scope, receive, send)  # type: ignore[arg-type]

    assert received == [b"a", b"b", b"c"]


@pytest.mark.asyncio
async def test_duplicate_content_type_cannot_bypass_multipart_admission() -> None:
    downstream_called = False

    async def downstream(scope: object, receive: object, send: object) -> None:
        nonlocal downstream_called
        downstream_called = True

    middleware = LocalRequestGuardMiddleware(
        downstream,  # type: ignore[arg-type]
        max_bytes=1024,
        allowed_origins=set(),
        max_concurrent_uploads=1,
        body_timeout_seconds=1,
        min_free_space_bytes=0,
    )
    scope: Any = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"content-type", b"text/plain"),
        ],
    }
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await middleware(scope, receive, send)  # type: ignore[arg-type]

    assert messages[0]["status"] == 400
    assert downstream_called is False
