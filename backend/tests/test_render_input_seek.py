from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import AppError
from app.core.settings import Settings
from app.editor.schemas import LayoutPreset, preset_config
from app.projects.storage import ProjectStorage
from app.rendering.command import FFmpegCommandBuilder
from app.rendering.filter_graph import FilterGraphBuilder
from app.rendering.plan import RenderPlanBuilder
from app.rendering.renderer import Renderer
from app.rendering.schemas import (
    RenderInputRole,
    RenderKind,
    RenderQuality,
    ResolvedMediaInput,
    ResolvedRenderContext,
    ResolvedTranscriptSource,
)


def _plan(tmp_path: Path, *, clip_start_ms: int = 5_000, clip_end_ms: int = 9_000):
    screen = tmp_path / "screen with spaces.mp4"
    webcam = tmp_path / "câmera.mp4"
    screen.write_bytes(b"screen")
    webcam.write_bytes(b"webcam")
    config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
    config.captions.enabled = False
    return RenderPlanBuilder().build(
        ResolvedRenderContext(
            project_id="project",
            candidate_id="candidate",
            edit_config_id="edit",
            clip_start_ms=clip_start_ms,
            clip_end_ms=clip_end_ms,
            webcam_offset_ms=1_000,
            edit_config=config,
            media=[
                ResolvedMediaInput(
                    media_id="screen",
                    role=RenderInputRole.SCREEN,
                    path=screen,
                    sha256="a" * 64,
                    duration_ms=10_000,
                    video_stream_indexes=[0],
                ),
                ResolvedMediaInput(
                    media_id="webcam",
                    role=RenderInputRole.WEBCAM,
                    path=webcam,
                    sha256="b" * 64,
                    duration_ms=10_000,
                    video_stream_indexes=[0],
                    audio_stream_indexes=[1],
                ),
            ],
            transcript=ResolvedTranscriptSource(
                transcript_id="transcript",
                cache_key="cache",
                media_id="webcam",
                audio_stream_index=1,
                caption_cues=[],
                timing_source="SEGMENTS",
            ),
        ),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )


def _input_options(command: list[str], path: Path) -> list[str]:
    index = command.index(str(path))
    assert command[index - 1] == "-i"
    return command[index - 5 : index + 1]


def test_command_seeks_and_bounds_each_media_input_after_sync_resolution(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    graph = FilterGraphBuilder().build(plan)
    command = FFmpegCommandBuilder().build(plan, graph, tmp_path / "preview.mp4")

    assert _input_options(command, plan.inputs[0].path) == [
        "-ss",
        "5.000",
        "-t",
        "4.000",
        "-i",
        str(plan.inputs[0].path),
    ]
    assert _input_options(command, plan.inputs[1].path) == [
        "-ss",
        "4.000",
        "-t",
        "4.000",
        "-i",
        str(plan.inputs[1].path),
    ]
    assert graph.value.count("]trim=start=0:end=4.000") == 2
    assert "atrim=start=0:end=4.000" in graph.value
    assert "trim=start=5.000" not in graph.value
    assert "trim=start=4.000" not in graph.value


def test_seek_is_bounded_when_clip_ends_at_media_duration(tmp_path: Path) -> None:
    plan = _plan(tmp_path, clip_start_ms=6_000, clip_end_ms=10_000)
    graph = FilterGraphBuilder().build(plan)
    command = FFmpegCommandBuilder().build(plan, graph, tmp_path / "final.mp4")

    assert _input_options(command, plan.inputs[0].path)[1:4] == ["6.000", "-t", "4.000"]
    assert _input_options(command, plan.inputs[1].path)[1:4] == ["5.000", "-t", "4.000"]
    assert "trim=start=0:end=4.000" in graph.value


def test_ass_font_family_cannot_add_style_fields(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
    config.captions.font_family = "Arial,Injected\r\n{Bad}\\Tail"
    context = ResolvedRenderContext(
        project_id="project",
        candidate_id="candidate",
        edit_config_id="edit",
        clip_start_ms=5_000,
        clip_end_ms=9_000,
        webcam_offset_ms=1_000,
        edit_config=config,
        media=[
            ResolvedMediaInput(
                media_id=item.media_id,
                role=item.role,
                path=item.path,
                sha256=item.sha256,
                duration_ms=item.source_duration_ms,
                video_stream_indexes=[item.video_stream_index],
                audio_stream_indexes=item.audio_stream_indexes,
            )
            for item in plan.inputs
        ],
        transcript=ResolvedTranscriptSource(
            transcript_id="transcript",
            cache_key="cache",
            media_id="webcam",
            audio_stream_index=1,
            caption_cues=[],
            timing_source="SEGMENTS",
        ),
    )

    sanitized = RenderPlanBuilder().build(
        context, kind=RenderKind.PREVIEW, quality=RenderQuality.FAST
    ).captions.style
    assert sanitized is not None
    assert sanitized.font_family == "ArialInjectedBadTail"


def test_command_applies_internal_output_size_limit(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    command = FFmpegCommandBuilder(max_output_bytes=123_456_789).build(
        plan,
        FilterGraphBuilder().build(plan),
        tmp_path / "preview.mp4",
    )

    assert command[command.index("-fs") + 1] == "123456789"
    assert command[-2:] == ["-n", str(tmp_path / "preview.mp4")]


def test_renderer_cleans_partial_output_when_size_cap_is_reached(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    storage = ProjectStorage(tmp_path / "storage")
    settings = Settings(storage_root=storage.root).model_copy(
        update={"max_render_output_bytes": 1024, "min_free_space_bytes": 0}
    )
    renderer = Renderer(settings, storage)
    renderer.ensure_available = lambda: None  # type: ignore[method-assign]
    renderer._require_space = lambda *_args: None  # type: ignore[method-assign]

    def oversized(
        command: list[str],
        _duration_ms: int,
        _output_directory: Path,
        _progress: object,
        _cancelled: object,
    ) -> str:
        output = Path(command[-1])
        with output.open("wb") as target:
            target.seek(1023)
            target.write(b"x")
        return ""

    renderer._execute = oversized  # type: ignore[method-assign]
    destination = tmp_path / "preview.mp4"
    with pytest.raises(AppError) as error:
        renderer.render(plan, destination, lambda _value: None, lambda: False)

    assert error.value.code == "RENDER_OUTPUT_TOO_LARGE"
    assert not destination.exists()
    assert not list(tmp_path.glob("*.partial.mp4"))


def test_startup_cleanup_removes_only_strict_renderer_temporary_names(tmp_path: Path) -> None:
    storage = ProjectStorage(tmp_path / "storage")
    project_id = "11111111-1111-4111-8111-111111111111"
    storage.project_dir(project_id, create=True)
    previews = storage.project_path(project_id, "previews")
    temp = storage.project_path(project_id, "temp")
    previews.mkdir()
    temp.mkdir()
    partial = previews / f".preview-abc.{'a' * 32}.partial.mp4"
    ass = temp / f"captions-{'b' * 32}.ass"
    final = previews / "preview-valid.mp4"
    lookalike = temp / "captions-user.ass"
    for path in (partial, ass, final, lookalike):
        path.write_bytes(b"data")

    renderer = Renderer(Settings(storage_root=storage.root), storage)
    assert renderer.cleanup_orphans() == 2
    assert not partial.exists() and not ass.exists()
    assert final.exists() and lookalike.exists()
