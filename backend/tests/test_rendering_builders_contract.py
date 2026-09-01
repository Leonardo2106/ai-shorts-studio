from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.editor.schemas import BannerStyle, CaptionCue, EditorElement, FitMode, LayoutPreset, preset_config
from app.rendering.captions import _ass_time, _caption_events, build_ass_document
from app.rendering.command import FFmpegCommandBuilder
from app.rendering.filter_graph import FilterGraphBuilder
from app.rendering.plan import RenderPlanBuilder
from app.rendering.schemas import (
    AudioMode,
    AudioPlan,
    RenderInputRole,
    RenderKind,
    RenderQuality,
    ResolvedBannerAsset,
    ResolvedMediaInput,
    ResolvedRenderContext,
    ResolvedTranscriptSource,
)
from app.rendering.service import _load_transcript
from app.transcription.schemas import TranscriptSegment, TranscriptWord


def _plan(tmp_path: Path, *, banner_asset: bool = False):
    directory = tmp_path / "mídia com espaço"
    directory.mkdir()
    screen, webcam = directory / "screen source.mp4", directory / "câmera.mp4"
    screen.write_bytes(b"screen")
    webcam.write_bytes(b"webcam")
    config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
    if banner_asset:
        asset_dir = tmp_path / "assets"
        asset_dir.mkdir()
        asset = asset_dir / "logo.png"
        asset.write_bytes(b"asset")
        banner = next(item for item in config.elements if item.kind == "BANNER")
        banner.visible = True
        config.banner = BannerStyle(enabled=True, text="Launch", image_relative_path="assets/logo.png")
        resolved_asset = ResolvedBannerAsset(relative_path="assets/logo.png", path=asset, sha256="c" * 64)
    else:
        resolved_asset = None
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
                media_id="screen",
                role=RenderInputRole.SCREEN,
                path=screen,
                sha256="a" * 64,
                duration_ms=20_000,
                video_stream_indexes=[0],
                audio_stream_indexes=[],
            ),
            ResolvedMediaInput(
                media_id="webcam",
                role=RenderInputRole.WEBCAM,
                path=webcam,
                sha256="b" * 64,
                duration_ms=20_000,
                video_stream_indexes=[0],
                audio_stream_indexes=[2],
            ),
        ],
        transcript=ResolvedTranscriptSource(
            transcript_id="transcript",
            cache_key="cache",
            media_id="webcam",
            audio_stream_index=2,
            timing_source="WORDS",
            caption_cues=[
                CaptionCue(
                    start_ms=0,
                    end_ms=700,
                    text="unsafe {brace}\\slash\nnext",
                    words=[
                        {"start_ms": 0, "end_ms": 350, "text": "unsafe"},
                        {"start_ms": 350, "end_ms": 700, "text": "word"},
                    ],
                )
            ],
        ),
        banner_asset=resolved_asset,
    )
    return RenderPlanBuilder().build(context, kind=RenderKind.PREVIEW, quality=RenderQuality.FAST)


def test_ass_document_escapes_untrusted_text_and_uses_word_timestamps(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    document = build_ass_document(plan)

    assert document is not None
    assert "{\\kf35}unsafe {\\kf35}word" in document
    escaped_cues = [CaptionCue(start_ms=0, end_ms=100, text="unsafe {brace}\\slash\nnext")]
    escaped_captions = plan.captions.model_copy(update={"cues": escaped_cues})
    escaped_plan = plan.model_copy(update={"captions": escaped_captions})
    assert "unsafe （brace）／slash\\Nnext" in build_ass_document(escaped_plan)
    assert "[Script Info]" in document
    assert _ass_time(3_723_450) == "1:02:03.45"


def test_filter_graph_has_internal_trim_fit_z_order_banner_and_escaped_ass_path(tmp_path: Path) -> None:
    plan = _plan(tmp_path, banner_asset=True)
    ass_path = tmp_path / "temp files" / "cap'tion: [safe].ass"
    graph = FilterGraphBuilder().build(plan, ass_path=ass_path)

    # Layer ordering comes from the normalized z-index order, not arbitrary UI filters.
    screen = next(layer for layer in plan.layers if layer.kind.value == "SCREEN")
    webcam = next(layer for layer in plan.layers if layer.kind.value == "WEBCAM")
    assert graph.value.index(f"overlay=x={screen.rect.x}:y={screen.rect.y}") < graph.value.index(
        f"overlay=x={webcam.rect.x}:y={webcam.rect.y}"
    )
    assert graph.value.count("]trim=start=0:end=4.000") == 2
    assert "atrim=start=0:end=4.000" in graph.value
    assert "scale=540:960:force_original_aspect_ratio=increase,crop=540:960" in graph.value
    assert "drawbox=" in graph.value and "between(t,0.000,4.000)" in graph.value
    assert "[2:v]scale=" in graph.value
    assert "ass=filename='" in graph.value
    assert "cap\\'tion\\: \\[safe\\].ass" in graph.value
    assert graph.audio_output == "audio_out"


def test_filter_graph_normalizes_gains_and_mixes_multiple_audio_sources(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = plan.audio.sources[0].model_copy(update={"gain_db": -6.0})
    second = first.model_copy(update={"stream_index": 3, "gain_db": 2.5})
    audio = AudioPlan(mode=AudioMode.MIXED_TRACKS, sources=[first, second])

    graph = FilterGraphBuilder().build(plan.model_copy(update={"audio": audio}))

    assert graph.value.count("atrim=start=0:end=4.000") == 2
    assert graph.value.count("asetpts=PTS-STARTPTS,aresample=48000") == 2
    assert graph.value.count("channel_layouts=stereo") == 2
    assert "volume=-6.00dB[audio_source0]" in graph.value
    assert "volume=2.50dB[audio_source1]" in graph.value
    assert "[audio_source0][audio_source1]amix=inputs=2:duration=longest:" in graph.value
    assert "dropout_transition=0:normalize=1,alimiter=limit=0.95[audio_out]" in graph.value


def test_editor_framing_validation_and_filter_zoom_focus_are_safe(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="only for SCREEN and WEBCAM"):
        EditorElement(id="captions", kind="CAPTIONS", x=0, y=0, width=100, height=100, zoom=1.2)
    with pytest.raises(ValidationError, match="not supported with CONTAIN"):
        EditorElement(
            id="screen", kind="SCREEN", x=0, y=0, width=100, height=100, fit=FitMode.CONTAIN, zoom=1.2
        )
    with pytest.raises(ValidationError):
        EditorElement(id="screen", kind="SCREEN", x=0, y=0, width=100, height=100, focal_x=1.1)
    with pytest.raises(ValidationError, match="not supported with CONTAIN"):
        EditorElement(
            id="screen", kind="SCREEN", x=0, y=0, width=100, height=100, fit=FitMode.CONTAIN, focal_x=0.2
        )

    plan = _plan(tmp_path)
    layer = next(item for item in plan.layers if item.kind.value == "WEBCAM")
    legacy = FilterGraphBuilder._fit(layer)
    assert "iw/1." not in legacy

    centered = layer.model_copy(update={"zoom": 2.0, "focal_x": 0.5, "focal_y": 0.5})
    centered_graph = FilterGraphBuilder._fit(centered)
    assert "crop=w='min(iw,ih*" in centered_graph
    assert "/2.000000':h='min(ih,iw*" in centered_graph
    assert centered_graph.count("crop=") == 1
    assert "(iw-ow)*0.500000" in centered_graph and "(ih-oh)*0.500000" in centered_graph

    top_left = FilterGraphBuilder._fit(centered.model_copy(update={"focal_x": 0.0, "focal_y": 0.0}))
    bottom_right = FilterGraphBuilder._fit(centered.model_copy(update={"focal_x": 1.0, "focal_y": 1.0}))
    assert "(iw-ow)*0.000000" in top_left and "(ih-oh)*0.000000" in top_left
    assert "(iw-ow)*1.000000" in bottom_right and "(ih-oh)*1.000000" in bottom_right
    assert "max(0,min(iw-ow" in bottom_right and "max(0,min(ih-oh" in bottom_right

    focus_at_zoom_one = FilterGraphBuilder._fit(layer.model_copy(update={"focal_x": 0.0}))
    assert focus_at_zoom_one != legacy
    assert focus_at_zoom_one.count("crop=") == 1
    assert "(iw-ow)*0.000000" in focus_at_zoom_one


def test_caption_ass_style_and_event_timing_policy(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    assert plan.captions.style is not None
    style = plan.captions.style.model_copy(
        update={
            "outline_color": "#FF0000",
            "italic": True,
            "gap_tolerance_ms": 250,
            "min_display_ms": 300,
            "hold_ms": 150,
        }
    )
    word_cues = [
        CaptionCue(start_ms=0, end_ms=100, text="one", words=[{"start_ms": 0, "end_ms": 100, "text": "one"}]),
        CaptionCue(
            start_ms=200,
            end_ms=250,
            text="two",
            words=[{"start_ms": 200, "end_ms": 250, "text": "two"}],
        ),
        CaptionCue(
            start_ms=1000,
            end_ms=1050,
            text="three",
            words=[{"start_ms": 1000, "end_ms": 1050, "text": "three"}],
        ),
    ]
    events = _caption_events(word_cues, style, 1.0, 1200)
    assert [(item.start_ms, item.end_ms) for item in events] == [(0, 200), (200, 500), (1000, 1200)]

    segment_events = _caption_events(
        [CaptionCue(start_ms=0, end_ms=100, text="segment"), CaptionCue(start_ms=800, end_ms=850, text="next")],
        style,
        1.0,
        1000,
    )
    assert [(item.start_ms, item.end_ms) for item in segment_events] == [(0, 300), (800, 1000)]

    captions = plan.captions.model_copy(update={"style": style, "cues": word_cues})
    document = build_ass_document(plan.model_copy(update={"captions": captions}))
    assert document is not None
    caption_style = next(line for line in document.splitlines() if line.startswith("Style: Captions"))
    assert "&H000000FF" in caption_style  # ASS stores red as BGR.
    assert ",-1,-1,0,0," in caption_style
    assert f"Dialogue: {(plan.captions.z_index or 0) + 100},0:00:00.00,0:00:00.20" in document

    mixed_style = style.model_copy(update={"words_per_line": 2, "words_per_block": 5})
    mixed = _caption_events(
        [
            CaptionCue(
                start_ms=0,
                end_ms=100,
                text="active",
                words=[{"start_ms": 0, "end_ms": 100, "text": "active"}],
            ),
            CaptionCue(start_ms=400, end_ms=700, text="one two three four five"),
            CaptionCue(
                start_ms=900,
                end_ms=1000,
                text="again",
                words=[{"start_ms": 900, "end_ms": 1000, "text": "again"}],
            ),
        ],
        mixed_style,
        1.0,
        1200,
    )
    assert "\\1c" in mixed[0].text and "\\1c" in mixed[2].text
    assert mixed[1].text == r"one two\Nthree four\Nfive"


def test_word_caption_blocks_break_on_size_pause_and_strong_terminal_punctuation(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    assert plan.captions.style is not None
    style = plan.captions.style.model_copy(update={"words_per_block": 4, "gap_tolerance_ms": 250})
    values_and_times = [
        ("one", 0, 100),
        ('two!”', 120, 200),
        ("three", 220, 300),
        ("four", 700, 800),
        ("five", 810, 900),
        ("six", 910, 1_000),
        ("seven", 1_010, 1_100),
    ]
    cues = [
        CaptionCue(
            start_ms=start_ms,
            end_ms=end_ms,
            text=text,
            words=[{"start_ms": start_ms, "end_ms": end_ms, "text": text}],
        )
        for text, start_ms, end_ms in values_and_times
    ]

    events = _caption_events(cues, style, 1.0, 1_200)

    assert len(events) == len(cues)
    assert all("one" in event.text and "two!”" in event.text for event in events[:2])
    assert "three" in events[2].text and "four" not in events[2].text
    assert all("four" in event.text and "seven" in event.text for event in events[3:])


def test_word_caption_blocks_are_formed_after_stable_chronological_sort(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    assert plan.captions.style is not None
    style = plan.captions.style.model_copy(update={"words_per_block": 2, "gap_tolerance_ms": 250})

    def cue(text: str, start_ms: int, end_ms: int) -> CaptionCue:
        return CaptionCue(
            start_ms=start_ms,
            end_ms=end_ms,
            text=text,
            words=[{"start_ms": start_ms, "end_ms": end_ms, "text": text}],
        )

    events = _caption_events(
        [cue("late", 400, 500), cue("early", 0, 100), cue("middle", 110, 200)],
        style,
        1.0,
        700,
    )

    assert [event.start_ms for event in events] == [0, 110, 400]
    assert all("early" in event.text and "middle" in event.text for event in events[:2])
    assert "late" not in events[0].text and "late" not in events[1].text
    assert "late" in events[2].text and "early" not in events[2].text


def test_render_transcript_load_is_bounded_after_initial_size_check() -> None:
    read_sizes: list[int] = []

    class GrowingSource:
        def __enter__(self) -> GrowingSource:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return b"x" * size

    path = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=1))
    storage = SimpleNamespace(open_binary=lambda _path: GrowingSource())

    with pytest.raises(AppError) as error:
        _load_transcript(storage, path)  # type: ignore[arg-type]

    assert error.value.code == "TRANSCRIPT_TOO_LARGE"
    assert error.value.status_code == 413
    assert read_sizes == [16 * 1024 * 1024 + 1]


def test_caption_text_and_ass_expansion_budgets_reject_before_ffmpeg(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="at most 20000 characters"):
        CaptionCue(start_ms=0, end_ms=100, text="x" * 20_001)
    with pytest.raises(ValidationError, match="1000 characters"):
        CaptionCue(
            start_ms=0,
            end_ms=100,
            text="word",
            words=[{"start_ms": 0, "end_ms": 100, "text": "x" * 1_001}],
        )
    with pytest.raises(ValidationError):
        TranscriptWord(start_ms=0, end_ms=100, text="x" * 1_001)
    with pytest.raises(ValidationError):
        TranscriptSegment(start_ms=0, end_ms=100, text="x" * 20_001)

    plan = _plan(tmp_path)
    assert plan.captions.style is not None
    repeated_word = "w" * 100
    cues = [
        CaptionCue(
            start_ms=index * 10,
            end_ms=index * 10 + 5,
            text=repeated_word,
            words=[{"start_ms": index * 10, "end_ms": index * 10 + 5, "text": repeated_word}],
        )
        for index in range(3_000)
    ]
    with pytest.raises(AppError) as budget_error:
        _caption_events(cues, plan.captions.style, 1.0, 60_000)
    assert budget_error.value.code == "CAPTION_ASS_LIMIT_EXCEEDED"
    assert budget_error.value.details["estimated_bytes"] > budget_error.value.details["max_bytes"]

    too_many = [CaptionCue(start_ms=index, end_ms=index + 1, text="x") for index in range(10_001)]
    with pytest.raises(AppError) as event_error:
        _caption_events(too_many, plan.captions.style, 1.0, 60_000)
    assert event_error.value.code == "CAPTION_ASS_LIMIT_EXCEEDED"
    assert event_error.value.details["event_count"] == 10_001

    normal = build_ass_document(plan)
    assert normal is not None and len(normal.encode("utf-8")) < 8 * 1024 * 1024


def test_command_builder_returns_safe_argument_vector_for_unicode_space_paths(tmp_path: Path) -> None:
    plan = _plan(tmp_path, banner_asset=True)
    graph = FilterGraphBuilder().build(plan)
    output = tmp_path / "renders com espaço" / "Short áé.mp4"
    command = FFmpegCommandBuilder("ffmpeg custom").build(plan, graph, output)

    assert command[0] == "ffmpeg custom"
    assert command.count("-i") == 3
    # Synchronization is applied by safe per-input seek arguments. The filter
    # graph therefore works in the normalized local 0..clip-duration timeline.
    for item, expected_start in zip(plan.inputs, ("5.000", "4.000"), strict=True):
        input_index = command.index(str(item.path))
        assert command[input_index - 5 : input_index] == ["-ss", expected_start, "-t", "4.000", "-i"]
    assert command[command.index("-filter_complex") + 1] == graph.value
    assert command[command.index("-map") + 1] == "[video_out]"
    assert command[-2:] == ["-n", str(output)]
    assert str(plan.inputs[0].path) in command and str(plan.banner.asset_path) in command
    assert all(not item.startswith("ffmpeg ") for item in command[1:])
    assert "shell" not in " ".join(command).lower()
