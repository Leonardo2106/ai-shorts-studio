from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from app.core.settings import Settings
from app.db.models import CandidateModel, EditConfigModel, MediaModel, MediaRole, ProjectModel, TranscriptModel
from app.editor.schemas import AudioConfig, AudioTrackConfig, FitMode, LayoutPreset, preset_config
from app.main import create_app
from app.projects.storage import ProjectStorage
from app.rendering.plan import RenderPlanBuilder
from app.rendering.renderer import Renderer
from app.rendering.schemas import RenderInputRole, RenderKind, RenderQuality, ResolvedMediaInput, ResolvedRenderContext

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg and ffprobe are required for the optional rendering integration test.",
)


def _make_fixture(ffmpeg: str, path: Path, *, duration_seconds: str = "1.5") -> None:
    command = [
        ffmpeg,
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=24",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-t",
        duration_seconds,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, shell=False)


def _make_multitrack_fixture(ffmpeg: str, path: Path) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-t",
            "1.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )


def _make_banner(ffmpeg: str, path: Path) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=#1188CC:s=160x80",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )


def _make_split_fixture(ffmpeg: str, path: Path, *, horizontal: bool) -> None:
    first, second = ("red", "blue") if horizontal else ("green", "yellow")
    size = "200x100" if horizontal else "100x200"
    stack = "hstack" if horizontal else "vstack"
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={first}:s={size}:r=24:d=0.6",
            "-f",
            "lavfi",
            "-i",
            f"color=c={second}:s={size}:r=24:d=0.6",
            "-filter_complex",
            stack,
            "-t",
            "0.6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )


def _average_rgb(ffmpeg: str, path: Path) -> tuple[int, int, int]:
    sampled = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=1:1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        shell=False,
    )
    return sampled.stdout[0], sampled.stdout[1], sampled.stdout[2]


def test_renderer_creates_valid_9x16_preview_with_audio(tmp_path: Path) -> None:
    settings = Settings(
        storage_root=tmp_path / "storage",
        ffmpeg_binary=shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_binary=shutil.which("ffprobe") or "ffprobe",
        allowed_hosts=["test"],
        min_free_space_bytes=64 * 1024**2,
    )
    storage = ProjectStorage(settings.resolved_storage_root)
    project_id = "123e4567-e89b-12d3-a456-426614174000"
    project_dir = storage.project_dir(project_id, create=True)
    source = project_dir / "media com espaço" / "vídeo áudio.mp4"
    source.parent.mkdir()
    _make_fixture(settings.ffmpeg_binary, source)

    config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
    for element in config.elements:
        if element.kind in {"WEBCAM", "CAPTIONS", "BANNER"}:
            element.visible = False
    config.captions.enabled = False
    config.banner.enabled = False
    plan = RenderPlanBuilder().build(
        ResolvedRenderContext(
            project_id=project_id,
            candidate_id="candidate",
            edit_config_id="edit",
            clip_start_ms=0,
            clip_end_ms=1_200,
            webcam_offset_ms=0,
            edit_config=config,
            media=[
                ResolvedMediaInput(
                    media_id="screen-media",
                    role=RenderInputRole.SCREEN,
                    path=source,
                    sha256="a" * 64,
                    duration_ms=1_500,
                    video_stream_indexes=[0],
                    audio_stream_indexes=[1],
                )
            ],
        ),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )
    # This integration path needs audio without captions; the renderer consumes
    # only the normalized audio plan, not the original transcript document.
    plan = plan.model_copy(
        update={
            "audio": plan.audio.model_validate(
                {
                    "mode": "PRIMARY_TRANSCRIPT_STREAM",
                    "source": {
                        "input_index": 0,
                        "media_id": "screen-media",
                        "stream_index": 1,
                        "source_start_ms": 0,
                        "source_end_ms": 1200,
                        "timeline_offset_ms": 0,
                    },
                }
            )
        }
    )
    destination = storage.project_path(project_id, "previews/preview.mp4")
    progress: list[float] = []
    result = Renderer(settings, storage).render(plan, destination, progress.append, lambda: False)

    probed = subprocess.run(
        [settings.ffprobe_binary, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(destination)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    data = json.loads(probed.stdout)
    video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
    assert destination.is_file()
    assert (result.width, result.height) == (video["width"], video["height"]) == (540, 960)
    assert result.has_audio is True
    assert any(stream["codec_type"] == "audio" for stream in data["streams"])
    assert abs(float(data["format"]["duration"]) - 1.2) <= 0.25
    assert progress and progress[-1] == pytest.approx(0.99)


def test_renderer_mixes_two_explicit_audio_tracks(tmp_path: Path) -> None:
    settings = Settings(
        storage_root=tmp_path / "storage",
        ffmpeg_binary=shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_binary=shutil.which("ffprobe") or "ffprobe",
        allowed_hosts=["test"],
        min_free_space_bytes=64 * 1024**2,
    )
    storage = ProjectStorage(settings.resolved_storage_root)
    project_id = "133e4567-e89b-12d3-a456-426614174000"
    source = storage.project_dir(project_id, create=True) / "multitrack.mp4"
    _make_multitrack_fixture(settings.ffmpeg_binary, source)
    config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
    for element in config.elements:
        if element.kind in {"WEBCAM", "CAPTIONS", "BANNER"}:
            element.visible = False
    config.captions.enabled = False
    config.audio = AudioConfig(
        mode="CUSTOM",
        tracks=[
            AudioTrackConfig(media_id="screen", stream_index=1, gain_db=-6),
            AudioTrackConfig(media_id="screen", stream_index=2, gain_db=-3),
        ],
    )
    plan = RenderPlanBuilder().build(
        ResolvedRenderContext(
            project_id=project_id,
            candidate_id="candidate",
            edit_config_id="edit",
            clip_start_ms=0,
            clip_end_ms=1_000,
            webcam_offset_ms=0,
            edit_config=config,
            media=[
                ResolvedMediaInput(
                    media_id="screen",
                    role=RenderInputRole.SCREEN,
                    path=source,
                    sha256="a" * 64,
                    duration_ms=1_200,
                    video_stream_indexes=[0],
                    audio_stream_indexes=[1, 2],
                )
            ],
        ),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )
    destination = storage.project_path(project_id, "previews/mixed.mp4")

    result = Renderer(settings, storage).render(plan, destination, lambda _progress: None, lambda: False)

    assert result.has_audio is True
    assert destination.is_file()


def test_renderer_focal_point_selects_source_edges_at_zoom_one(tmp_path: Path) -> None:
    settings = Settings(
        storage_root=tmp_path / "storage",
        ffmpeg_binary=shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_binary=shutil.which("ffprobe") or "ffprobe",
        allowed_hosts=["test"],
        min_free_space_bytes=64 * 1024**2,
    )
    storage = ProjectStorage(settings.resolved_storage_root)
    project_id = "223e4567-e89b-12d3-a456-426614174000"
    project_dir = storage.project_dir(project_id, create=True)
    horizontal, vertical = project_dir / "horizontal.mp4", project_dir / "vertical.mp4"
    _make_split_fixture(settings.ffmpeg_binary, horizontal, horizontal=True)
    _make_split_fixture(settings.ffmpeg_binary, vertical, horizontal=False)
    renderer = Renderer(settings, storage)

    def render(source: Path, *, focal_x: float, focal_y: float, name: str) -> tuple[int, int, int]:
        config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
        for element in config.elements:
            if element.kind != "SCREEN":
                element.visible = False
        config.captions.enabled = False
        config.banner.enabled = False
        screen = next(element for element in config.elements if element.kind == "SCREEN")
        screen.fit = FitMode.COVER
        screen.zoom = 1.0
        screen.focal_x = focal_x
        screen.focal_y = focal_y
        plan = RenderPlanBuilder().build(
            ResolvedRenderContext(
                project_id=project_id,
                candidate_id=name,
                edit_config_id=f"edit-{name}",
                clip_start_ms=0,
                clip_end_ms=400,
                webcam_offset_ms=0,
                edit_config=config,
                media=[
                    ResolvedMediaInput(
                        media_id=f"media-{name}",
                        role=RenderInputRole.SCREEN,
                        path=source,
                        sha256="d" * 64,
                        duration_ms=600,
                        video_stream_indexes=[0],
                    )
                ],
            ),
            kind=RenderKind.PREVIEW,
            quality=RenderQuality.FAST,
        )
        destination = storage.project_path(project_id, f"previews/{name}.mp4")
        renderer.render(plan, destination, lambda _value: None, lambda: False)
        return _average_rgb(settings.ffmpeg_binary, destination)

    left = render(horizontal, focal_x=0.0, focal_y=0.5, name="left")
    right = render(horizontal, focal_x=1.0, focal_y=0.5, name="right")
    top = render(vertical, focal_x=0.5, focal_y=0.0, name="top")
    bottom = render(vertical, focal_x=0.5, focal_y=1.0, name="bottom")
    assert left[0] > left[2] + 100
    assert right[2] > right[0] + 100
    assert bottom[0] > top[0] + 100


@pytest.mark.asyncio
async def test_final_render_api_runs_real_project_media_captions_banner_and_job(tmp_path: Path) -> None:
    settings = Settings(
        storage_root=tmp_path / "storage",
        ffmpeg_binary=shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_binary=shutil.which("ffprobe") or "ffprobe",
        allowed_hosts=["test"],
        min_free_space_bytes=64 * 1024**2,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/projects", json={"name": "End-to-end FFmpeg"})
            assert created.status_code == 201
            project_id = created.json()["id"]
            project_dir = app.state.storage.project_dir(project_id)
            screen, webcam = project_dir / "screen.mp4", project_dir / "webcam.mp4"
            _make_fixture(settings.ffmpeg_binary, screen)
            _make_fixture(settings.ffmpeg_binary, webcam)
            banner = app.state.storage.project_path(project_id, "assets/banner.png")
            banner.parent.mkdir()
            _make_banner(settings.ffmpeg_binary, banner)

            config = preset_config(LayoutPreset.WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM)
            config.banner.enabled = True
            config.banner.text = "Real render"
            config.banner.image_relative_path = "assets/banner.png"
            for element in config.elements:
                if element.kind in {"SCREEN", "WEBCAM"}:
                    element.fit = FitMode.CROP
                    element.padding = 4
                    element.border_width = 4
                    element.border_color = "#FFFFFF"
                    element.radius = 0
            screen_element = next(item for item in config.elements if item.kind == "SCREEN")
            screen_element.zoom = 1.35
            screen_element.focal_x = 0.2
            screen_element.focal_y = 0.8
            transcript_id, candidate_id, edit_config_id = "transcript-e2e", "candidate-e2e", "edit-e2e"
            transcript_path = app.state.storage.project_path(project_id, "transcripts/words.json")
            transcript_path.parent.mkdir()
            transcript_path.write_text(
                json.dumps(
                    {
                        "id": transcript_id,
                        "project_id": project_id,
                        "media_id": "webcam-e2e",
                        "source": "WEBCAM",
                        "audio_stream_index": 1,
                        "language": "en",
                        "duration_ms": 1500,
                        "engine": "fixture",
                        "model": "fixture",
                        "segments": [
                            {
                                "start_ms": 100,
                                "end_ms": 900,
                                "text": "real render",
                                "words": [
                                    {"start_ms": 100, "end_ms": 450, "text": "real"},
                                    {"start_ms": 450, "end_ms": 900, "text": "render"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with app.state.session_factory.begin() as session:
                project = session.get(ProjectModel, project_id)
                assert project is not None
                project.webcam_offset_ms = 100
                session.add_all(
                    [
                        MediaModel(
                            id="screen-e2e",
                            project_id=project_id,
                            role=MediaRole.SCREEN,
                            relative_path=f"projects/{project_id}/screen.mp4",
                            original_filename="screen.mp4",
                            size_bytes=screen.stat().st_size,
                            sha256="a" * 64,
                            probe_data={
                                "duration_ms": 1500,
                                "video_streams": [{"index": 0, "width": 320, "height": 180, "fps": 24}],
                                "audio_streams": [{"index": 1, "sample_rate": 48000, "channels": 1}],
                            },
                        ),
                        MediaModel(
                            id="webcam-e2e",
                            project_id=project_id,
                            role=MediaRole.WEBCAM,
                            relative_path=f"projects/{project_id}/webcam.mp4",
                            original_filename="webcam.mp4",
                            size_bytes=webcam.stat().st_size,
                            sha256="b" * 64,
                            probe_data={
                                "duration_ms": 1500,
                                "video_streams": [{"index": 0, "width": 320, "height": 180, "fps": 24}],
                                "audio_streams": [{"index": 1, "sample_rate": 48000, "channels": 1}],
                            },
                        ),
                    ]
                )
                session.flush()
                session.add(
                    TranscriptModel(
                        id=transcript_id,
                        project_id=project_id,
                        media_id="webcam-e2e",
                        cache_key="c" * 64,
                        relative_path="transcripts/words.json",
                        language="en",
                        duration_ms=1500,
                    )
                )
                session.flush()
                session.add_all(
                    [
                        CandidateModel(
                            id=candidate_id,
                            project_id=project_id,
                            transcript_id=transcript_id,
                            start_ms=100,
                            end_ms=1100,
                            title="E2E",
                            reasons=[],
                            context={},
                            signals={},
                            score=None,
                            score_breakdown=None,
                        ),
                        EditConfigModel(
                            id=edit_config_id,
                            project_id=project_id,
                            candidate_id=candidate_id,
                            config=config.model_dump(mode="json"),
                        ),
                    ]
                )

            plan_started = time.monotonic()
            direct_plan = app.state.rendering.plan(
                project_id,
                candidate_id,
                RenderKind.FINAL,
                RenderQuality.FAST,
            )
            assert direct_plan.captions.enabled and direct_plan.banner.enabled
            assert time.monotonic() - plan_started < 5
            queued = await asyncio.wait_for(
                client.post(
                    f"/api/v1/projects/{project_id}/candidates/{candidate_id}/render-jobs",
                    json={"quality": "FAST"},
                ),
                timeout=10,
            )
            assert queued.status_code == 202
            job_id = queued.json()["id"]
            deadline = time.monotonic() + 30
            while True:
                job = await asyncio.wait_for(client.get(f"/api/v1/jobs/{job_id}"), timeout=5)
                assert job.status_code == 200
                status = job.json()["status"]
                if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                assert time.monotonic() < deadline, job.json()
                await asyncio.sleep(0.1)
            assert status == "COMPLETED", job.json()
            artifact_id = job.json()["result"]["artifact_id"]
            artifact = await client.get(f"/api/v1/projects/{project_id}/artifacts/{artifact_id}")
            assert artifact.status_code == 200
            content = await client.get(artifact.json()["content_url"])
            assert content.status_code == 200
            output = app.state.storage.project_path(project_id, "renders")
            rendered = next(output.glob("*.mp4"))
            probe = subprocess.run(
                [settings.ffprobe_binary, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(rendered)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=True,
                shell=False,
            )
            data = json.loads(probe.stdout)
            video = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
            assert (video["width"], video["height"]) == (1080, 1920)
            assert any(stream["codec_type"] == "audio" for stream in data["streams"])
    assert abs(float(data["format"]["duration"]) - 1.0) <= 0.25


@pytest.mark.asyncio
async def test_final_render_api_cancellation_terminates_ffmpeg_and_cleans_temporary_files(tmp_path: Path) -> None:
    settings = Settings(
        storage_root=tmp_path / "storage",
        ffmpeg_binary=shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_binary=shutil.which("ffprobe") or "ffprobe",
        allowed_hosts=["test"],
        min_free_space_bytes=64 * 1024**2,
        render_cancel_grace_seconds=0.2,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/projects", json={"name": "FFmpeg cancellation"})
            assert created.status_code == 201
            project_id = created.json()["id"]
            screen = app.state.storage.project_path(project_id, "screen.mp4")
            _make_fixture(settings.ffmpeg_binary, screen, duration_seconds="20")
            config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
            for element in config.elements:
                if element.kind in {"WEBCAM", "BANNER"}:
                    element.visible = False
            config.banner.enabled = False
            transcript_id, candidate_id, edit_config_id = "transcript-cancel", "candidate-cancel", "edit-cancel"
            transcript_path = app.state.storage.project_path(project_id, "transcripts/cancel.json")
            transcript_path.parent.mkdir()
            transcript_path.write_text(
                json.dumps(
                    {
                        "id": transcript_id,
                        "project_id": project_id,
                        "media_id": "screen-cancel",
                        "source": "SCREEN",
                        "audio_stream_index": 1,
                        "language": "en",
                        "duration_ms": 20_000,
                        "engine": "fixture",
                        "model": "fixture",
                        "segments": [
                            {
                                "start_ms": 0,
                                "end_ms": 20_000,
                                "text": "cancellation fixture",
                                "words": [{"start_ms": 0, "end_ms": 20_000, "text": "cancellation"}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with app.state.session_factory.begin() as session:
                session.add(
                    MediaModel(
                        id="screen-cancel",
                        project_id=project_id,
                        role=MediaRole.SCREEN,
                        relative_path=f"projects/{project_id}/screen.mp4",
                        original_filename="screen.mp4",
                        size_bytes=screen.stat().st_size,
                        sha256="d" * 64,
                        probe_data={
                            "duration_ms": 20_000,
                            "video_streams": [{"index": 0, "width": 320, "height": 180, "fps": 24}],
                            "audio_streams": [{"index": 1, "sample_rate": 48000, "channels": 1}],
                        },
                    )
                )
                session.flush()
                session.add(
                    TranscriptModel(
                        id=transcript_id,
                        project_id=project_id,
                        media_id="screen-cancel",
                        cache_key="e" * 64,
                        relative_path="transcripts/cancel.json",
                        language="en",
                        duration_ms=20_000,
                    )
                )
                session.flush()
                session.add_all(
                    [
                        CandidateModel(
                            id=candidate_id,
                            project_id=project_id,
                            transcript_id=transcript_id,
                            start_ms=0,
                            end_ms=20_000,
                            title="Cancel",
                            reasons=[],
                            context={},
                            signals={},
                            score=None,
                            score_breakdown=None,
                        ),
                        EditConfigModel(
                            id=edit_config_id,
                            project_id=project_id,
                            candidate_id=candidate_id,
                            config=config.model_dump(mode="json"),
                        ),
                    ]
                )

            queued = await client.post(
                f"/api/v1/projects/{project_id}/candidates/{candidate_id}/render-jobs", json={"quality": "FAST"}
            )
            assert queued.status_code == 202
            job_id = queued.json()["id"]
            deadline = time.monotonic() + 20
            while True:
                job = await client.get(f"/api/v1/jobs/{job_id}")
                assert job.status_code == 200
                payload = job.json()
                if payload["status"] == "RUNNING" and payload["progress"] > 0:
                    break
                assert payload["status"] == "PENDING", payload
                assert time.monotonic() < deadline, payload
                await asyncio.sleep(0.05)
            cancelled = await client.post(f"/api/v1/jobs/{job_id}/cancel")
            assert cancelled.status_code == 200
            while True:
                job = await client.get(f"/api/v1/jobs/{job_id}")
                payload = job.json()
                if payload["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
                assert time.monotonic() < deadline, payload
                await asyncio.sleep(0.05)
            assert payload["status"] == "CANCELLED", payload
            assert payload["result"] is None
            artifacts = await client.get(f"/api/v1/projects/{project_id}/artifacts")
            assert artifacts.status_code == 200
            assert artifacts.json()["items"] == []
            project_dir = app.state.storage.project_dir(project_id)
            renders = project_dir / "renders"
            if renders.exists():
                assert not list(renders.glob("*.mp4"))
            assert not list(project_dir.rglob("*.partial.mp4"))
            temporary = project_dir / "temp"
            if temporary.exists():
                assert not list(temporary.glob("captions-*.ass"))
