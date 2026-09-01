from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import AppError
from app.editor.schemas import AudioConfig, AudioTrackConfig, CaptionCue, LayoutPreset, preset_config
from app.rendering.plan import RenderPlanBuilder
from app.rendering.schemas import (
    AudioMode,
    RenderInputRole,
    RenderKind,
    RenderQuality,
    ResolvedMediaInput,
    ResolvedRenderContext,
    ResolvedTranscriptSource,
)


def _context(tmp_path: Path) -> ResolvedRenderContext:
    screen = tmp_path / "mídia com espaço" / "screen source.mp4"
    webcam = tmp_path / "mídia com espaço" / "câmera.mp4"
    screen.parent.mkdir()
    screen.write_bytes(b"screen")
    webcam.write_bytes(b"webcam")
    return ResolvedRenderContext(
        project_id="project",
        candidate_id="candidate",
        edit_config_id="edit-config",
        clip_start_ms=5_000,
        clip_end_ms=15_000,
        webcam_offset_ms=1_000,
        edit_config=preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY),
        media=[
            ResolvedMediaInput(
                media_id="screen-media",
                role=RenderInputRole.SCREEN,
                path=screen,
                sha256="a" * 64,
                duration_ms=30_000,
                video_stream_indexes=[0],
                audio_stream_indexes=[1, 2],
            ),
            ResolvedMediaInput(
                media_id="webcam-media",
                role=RenderInputRole.WEBCAM,
                path=webcam,
                sha256="b" * 64,
                duration_ms=30_000,
                video_stream_indexes=[0],
                audio_stream_indexes=[3],
            ),
        ],
        transcript=ResolvedTranscriptSource(
            transcript_id="transcript",
            cache_key="transcript-cache-v1",
            media_id="webcam-media",
            audio_stream_index=3,
            timing_source="WORDS",
            caption_cues=[
                CaptionCue(
                    start_ms=0,
                    end_ms=1_000,
                    text="Olá",
                    words=[{"start_ms": 0, "end_ms": 1_000, "text": "Olá"}],
                )
            ],
        ),
    )


def test_preview_plan_normalizes_canvas_sync_layers_captions_audio_and_output(tmp_path: Path) -> None:
    plan = RenderPlanBuilder().build(_context(tmp_path), kind=RenderKind.PREVIEW, quality=RenderQuality.FAST)

    assert plan.model_dump(mode="json")["schema_version"] == 1
    assert (plan.canvas.output_width, plan.canvas.output_height, plan.canvas.fps) == (540, 960, 24)
    assert [(item.role, item.source_start_ms, item.source_end_ms) for item in plan.inputs] == [
        (RenderInputRole.SCREEN, 5_000, 15_000),
        (RenderInputRole.WEBCAM, 4_000, 14_000),
    ]
    assert [item.z_index for item in plan.layers] == sorted(item.z_index for item in plan.layers)
    webcam_layer = next(item for item in plan.layers if item.kind.value == "WEBCAM")
    assert webcam_layer.rect.model_dump() == {"x": 350, "y": 40, "width": 160, "height": 240}
    assert plan.captions.enabled is True
    assert plan.captions.style is not None and plan.captions.style.font_size == 32
    assert plan.audio.mode == AudioMode.PRIMARY_TRANSCRIPT_STREAM
    assert plan.audio.source is not None
    assert (plan.audio.source.media_id, plan.audio.source.stream_index, plan.audio.source.timeline_offset_ms) == (
        "webcam-media",
        3,
        1_000,
    )
    assert plan.output.relative_directory == "previews"
    assert plan.output.encoder_preset == "ultrafast"
    assert plan.cacheable is True


def test_final_plan_is_full_size_not_cacheable_and_fingerprint_tracks_visual_dependencies(tmp_path: Path) -> None:
    builder = RenderPlanBuilder()
    context = _context(tmp_path)
    first = builder.build(context, kind=RenderKind.FINAL, quality=RenderQuality.BALANCED)
    repeated = builder.build(context, kind=RenderKind.FINAL, quality=RenderQuality.BALANCED)
    changed_config = context.edit_config.model_copy(
        update={"background_color": "#123456"},
    )
    changed = builder.build(
        context.model_copy(update={"edit_config": changed_config}),
        kind=RenderKind.FINAL,
        quality=RenderQuality.BALANCED,
    )

    assert (first.canvas.output_width, first.canvas.output_height, first.canvas.fps) == (1080, 1920, 30)
    assert first.output.relative_directory == "renders"
    assert first.cacheable is False
    assert first.dependency_fingerprint == repeated.dependency_fingerprint
    assert first.edit_config_fingerprint != changed.edit_config_fingerprint
    assert first.dependency_fingerprint != changed.dependency_fingerprint


def test_plan_normalizes_caption_timing_style_and_video_framing_into_fingerprint(tmp_path: Path) -> None:
    context = _context(tmp_path)
    config = context.edit_config.model_copy(deep=True)
    webcam = next(item for item in config.elements if item.kind == "WEBCAM")
    webcam.zoom = 1.75
    webcam.focal_x = 0.2
    webcam.focal_y = 0.8
    config.captions.outline_color = "#123456"
    config.captions.italic = True
    config.captions.gap_tolerance_ms = 400
    config.captions.min_display_ms = 500
    config.captions.hold_ms = 275

    builder = RenderPlanBuilder()
    plan = builder.build(
        context.model_copy(update={"edit_config": config}),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )
    webcam_plan = next(item for item in plan.layers if item.kind.value == "WEBCAM")
    assert (webcam_plan.zoom, webcam_plan.focal_x, webcam_plan.focal_y) == (1.75, 0.2, 0.8)
    assert plan.captions.style is not None
    assert plan.captions.style.outline_color == "#123456"
    assert plan.captions.style.italic is True
    assert (
        plan.captions.style.gap_tolerance_ms,
        plan.captions.style.min_display_ms,
        plan.captions.style.hold_ms,
    ) == (400, 500, 275)

    changed = config.model_copy(deep=True)
    next(item for item in changed.elements if item.kind == "WEBCAM").focal_x = 0.3
    changed_plan = builder.build(
        context.model_copy(update={"edit_config": changed}),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )
    assert changed_plan.dependency_fingerprint != plan.dependency_fingerprint


def test_plan_rejects_missing_files_invalid_audio_and_out_of_bounds_sync(tmp_path: Path) -> None:
    builder = RenderPlanBuilder()
    context = _context(tmp_path)
    missing = context.media[0].model_copy(update={"path": tmp_path / "missing.mp4"})
    with pytest.raises(AppError) as missing_error:
        builder.build(
            context.model_copy(update={"media": [missing, context.media[1]]}),
            kind=RenderKind.PREVIEW,
            quality=RenderQuality.FAST,
        )
    assert missing_error.value.code == "RENDER_INPUT_FILE_MISSING"

    invalid_transcript = context.transcript.model_copy(update={"audio_stream_index": 99})
    with pytest.raises(AppError) as audio_error:
        builder.build(
            context.model_copy(update={"transcript": invalid_transcript}),
            kind=RenderKind.PREVIEW,
            quality=RenderQuality.FAST,
        )
    assert audio_error.value.code == "RENDER_AUDIO_STREAM_MISSING"

    with pytest.raises(AppError) as sync_error:
        builder.build(
            context.model_copy(update={"webcam_offset_ms": 20_000}),
            kind=RenderKind.PREVIEW,
            quality=RenderQuality.FAST,
        )
    assert sync_error.value.code == "RENDER_CLIP_OUTSIDE_INPUT"


def test_plan_uses_silence_without_transcript_and_never_infers_an_audio_track(tmp_path: Path) -> None:
    context = _context(tmp_path)
    config = context.edit_config.model_copy(
        update={"captions": context.edit_config.captions.model_copy(update={"enabled": False})}
    )

    plan = RenderPlanBuilder().build(
        context.model_copy(update={"edit_config": config, "transcript": None}),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )

    assert plan.audio.mode == AudioMode.SILENT
    assert plan.audio.source is None


def test_custom_audio_resolves_enabled_tracks_gain_sync_and_fingerprint(tmp_path: Path) -> None:
    builder = RenderPlanBuilder()
    context = _context(tmp_path)
    config = context.edit_config.model_copy(
        update={
            "audio": AudioConfig(
                mode="CUSTOM",
                tracks=[
                    AudioTrackConfig(media_id="screen-media", stream_index=2, gain_db=-6.0),
                    AudioTrackConfig(media_id="webcam-media", stream_index=3, gain_db=3.0),
                    AudioTrackConfig(media_id="missing-disabled", stream_index=99, enabled=False),
                ],
            )
        }
    )

    plan = builder.build(
        context.model_copy(update={"edit_config": config}),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )

    assert plan.audio.mode == AudioMode.MIXED_TRACKS
    assert [
        (source.media_id, source.stream_index, source.gain_db, source.source_start_ms)
        for source in plan.audio.sources
    ] == [
        ("screen-media", 2, -6.0, 5_000),
        ("webcam-media", 3, 3.0, 4_000),
    ]
    changed = config.model_copy(deep=True)
    changed.audio.tracks[0].gain_db = -5.0
    changed_plan = builder.build(
        context.model_copy(update={"edit_config": changed}),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )
    assert changed_plan.dependency_fingerprint != plan.dependency_fingerprint


def test_custom_audio_ignores_disabled_missing_but_rejects_enabled_missing_sources(tmp_path: Path) -> None:
    builder = RenderPlanBuilder()
    context = _context(tmp_path)
    disabled = context.edit_config.model_copy(
        update={
            "audio": AudioConfig(
                mode="CUSTOM",
                tracks=[AudioTrackConfig(media_id="missing", stream_index=99, enabled=False)],
            )
        }
    )
    silent = builder.build(
        context.model_copy(update={"edit_config": disabled}),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )
    assert silent.audio.mode == AudioMode.SILENT
    assert silent.audio.sources == []

    missing_media = disabled.model_copy(
        update={
            "audio": AudioConfig(
                mode="CUSTOM",
                tracks=[AudioTrackConfig(media_id="missing", stream_index=99)],
            )
        }
    )
    with pytest.raises(AppError) as input_error:
        builder.build(
            context.model_copy(update={"edit_config": missing_media}),
            kind=RenderKind.PREVIEW,
            quality=RenderQuality.FAST,
        )
    assert input_error.value.code == "RENDER_AUDIO_INPUT_MISSING"

    missing_stream = disabled.model_copy(
        update={
            "audio": AudioConfig(
                mode="CUSTOM",
                tracks=[AudioTrackConfig(media_id="screen-media", stream_index=99)],
            )
        }
    )
    with pytest.raises(AppError) as stream_error:
        builder.build(
            context.model_copy(update={"edit_config": missing_stream}),
            kind=RenderKind.PREVIEW,
            quality=RenderQuality.FAST,
        )
    assert stream_error.value.code == "RENDER_AUDIO_STREAM_MISSING"


def test_preview_fingerprint_ignores_caption_content_when_captions_are_disabled(tmp_path: Path) -> None:
    builder = RenderPlanBuilder()
    context = _context(tmp_path)
    config = context.edit_config.model_copy(
        update={"captions": context.edit_config.captions.model_copy(update={"enabled": False})}
    )
    disabled = context.model_copy(update={"edit_config": config})
    first = builder.build(disabled, kind=RenderKind.PREVIEW, quality=RenderQuality.FAST)
    changed_transcript = context.transcript.model_copy(
        update={
            "cache_key": "different-transcript-cache",
            "caption_cues": [CaptionCue(start_ms=2_000, end_ms=3_000, text="ignored")],
        }
    )
    repeated = builder.build(
        disabled.model_copy(update={"transcript": changed_transcript}),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )

    assert first.dependency_fingerprint == repeated.dependency_fingerprint
