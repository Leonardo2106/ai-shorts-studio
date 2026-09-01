from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.rendering_routes import _build_plan
from app.core.errors import AppError
from app.core.settings import Settings
from app.db.models import JobModel, JobStatus, ProjectModel
from app.db.session import build_engine, build_session_factory, initialize_database
from app.editor.schemas import (
    CaptionCue,
    EditConfig,
    FitMode,
    LayoutPreset,
    normalize_legacy_edit_config,
    preset_config,
)
from app.jobs.runner import LocalJobRunner
from app.projects.storage import ProjectStorage
from app.rendering.captions import build_ass_document
from app.rendering.filter_graph import FilterGraphBuilder
from app.rendering.plan import RenderPlanBuilder
from app.rendering.renderer import Renderer
from app.rendering.schemas import (
    RenderInputRole,
    RenderKind,
    RenderQuality,
    ResolvedBannerAsset,
    ResolvedMediaInput,
    ResolvedRenderContext,
    ResolvedTranscriptSource,
)
from app.rendering.service import RenderingService

PROJECT_ID = "11111111-1111-4111-8111-111111111111"


def _roadmap01_literal(preset: LayoutPreset) -> dict[str, object]:
    def element(**values: object) -> dict[str, object]:
        return {
            "z_index": 0,
            "visible": True,
            "fit": "COVER",
            "opacity": 1,
            "border_width": 0,
            "border_color": "#000000",
            "radius": 0,
            "padding": 0,
            **values,
        }

    captions = element(
        id="captions", kind="CAPTIONS", x=60, y=1390, width=960, height=300, z_index=20, padding=24, radius=16
    )
    banner = element(
        id="banner", kind="BANNER", x=0, y=1680, width=1080, height=240,
        visible=False, z_index=10, opacity=0.95, radius=24, padding=28,
    )
    if preset == LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY:
        elements = [
            element(id="screen", kind="SCREEN", x=0, y=0, width=1080, height=1920),
            element(
                id="webcam", kind="WEBCAM", x=700, y=80, width=320, height=480,
                z_index=2, radius=36, border_width=6, border_color="#FFFFFF",
            ),
            captions,
            banner,
        ]
    else:
        elements = [
            element(id="webcam", kind="WEBCAM", x=0, y=0, width=1080, height=1920),
            element(
                id="screen", kind="SCREEN", x=80, y=1300, width=920, height=520,
                z_index=2, fit="CONTAIN", radius=28, border_width=6, border_color="#FFFFFF",
            ),
            captions,
            banner,
        ]
    return {
        "schema_version": 1,
        "canvas_width": 1080,
        "canvas_height": 1920,
        "preset": preset.value,
        "elements": elements,
        "captions": {
            "enabled": True, "font_family": "Inter", "font_size": 64, "color": "#FFFFFF",
            "weight": 800, "uppercase": False, "outline_width": 4, "shadow": True,
            "box_color": "#000000", "max_width": 960, "words_per_line": 5,
            "words_per_block": 10, "active_word_color": "#38BDF8",
        },
        "banner": {
            "enabled": False, "text": "Seu título aqui", "image_relative_path": None,
            "background_color": "#0F172A", "opacity": 0.9, "start_ms": 0, "end_ms": None,
        },
        "background_color": "#080C14",
    }


def _plan(tmp_path: Path):
    screen = tmp_path / "screen.mp4"
    webcam = tmp_path / "webcam.mp4"
    screen.write_bytes(b"screen")
    webcam.write_bytes(b"webcam")
    config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
    return RenderPlanBuilder().build(
        ResolvedRenderContext(
            project_id=PROJECT_ID,
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
                ),
                ResolvedMediaInput(
                    media_id="webcam",
                    role=RenderInputRole.WEBCAM,
                    path=webcam,
                    sha256="b" * 64,
                    duration_ms=20_000,
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
                timing_source="WORDS",
            ),
        ),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )


@pytest.mark.parametrize("changed_field", ["edit", "dependency"])
def test_render_job_rejects_editor_or_clip_snapshot_changes(tmp_path: Path, changed_field: str) -> None:
    plan = _plan(tmp_path)
    changed = plan.model_copy(
        update={
            "edit_config_fingerprint": "c" * 64
            if changed_field == "edit"
            else plan.edit_config_fingerprint,
            "dependency_fingerprint": "d" * 64
            if changed_field == "dependency"
            else plan.dependency_fingerprint,
        }
    )
    storage = ProjectStorage(tmp_path / "storage")
    engine = build_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    service = RenderingService(
        storage,
        build_session_factory(engine),
        Renderer(Settings(storage_root=storage.root), storage),
    )
    service.plan = lambda *_args: changed  # type: ignore[method-assign]

    with pytest.raises(AppError) as error:
        service.run(
            PROJECT_ID,
            {
                "candidate_id": "candidate",
                "kind": "PREVIEW",
                "quality": "FAST",
                "_job_id": "job",
                "expected_edit_config_fingerprint": plan.edit_config_fingerprint,
                "expected_dependency_fingerprint": plan.dependency_fingerprint,
            },
            lambda _value: None,
            lambda: False,
        )

    assert error.value.code == "RENDER_PLAN_STALE"


def test_deterministic_preview_orphan_is_removed_before_regeneration(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    storage = ProjectStorage(tmp_path / "storage")
    storage.project_dir(PROJECT_ID, create=True)
    engine = build_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    renderer = Renderer(Settings(storage_root=storage.root), storage)
    service = RenderingService(storage, build_session_factory(engine), renderer)
    service.plan = lambda *_args: plan  # type: ignore[method-assign]
    orphan = storage.project_path(PROJECT_ID, f"previews/preview-{plan.dependency_fingerprint}.mp4")
    orphan.parent.mkdir()
    orphan.write_bytes(b"orphan")

    def stop_at_render(*args: object) -> object:
        assert args[1] == orphan
        assert not orphan.exists()
        raise AppError("EXPECTED_STOP", "stop", status_code=500)

    renderer.render = stop_at_render  # type: ignore[method-assign]
    with pytest.raises(AppError) as error:
        service.run(
            PROJECT_ID,
            {
                "candidate_id": "candidate",
                "kind": "PREVIEW",
                "quality": "FAST",
                "_job_id": "job",
                "expected_edit_config_fingerprint": plan.edit_config_fingerprint,
                "expected_dependency_fingerprint": plan.dependency_fingerprint,
            },
            lambda _value: None,
            lambda: False,
        )
    assert error.value.code == "EXPECTED_STOP"


def test_crop_padding_are_rendered_and_radius_is_rejected(tmp_path: Path) -> None:
    screen = tmp_path / "screen.mp4"
    screen.write_bytes(b"screen")
    config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
    for element in config.elements:
        if element.kind == "SCREEN":
            element.fit = FitMode.CROP
            element.padding = 10
            element.border_width = 2
        elif element.kind in {"WEBCAM", "CAPTIONS"}:
            element.visible = False
    config.captions.enabled = False
    context = ResolvedRenderContext(
        project_id=PROJECT_ID,
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
                path=screen,
                sha256="a" * 64,
                duration_ms=1_000,
                video_stream_indexes=[0],
            )
        ],
    )
    plan = RenderPlanBuilder().build(context, kind=RenderKind.PREVIEW, quality=RenderQuality.FAST)
    graph = FilterGraphBuilder().build(plan).value
    assert "crop=w='min(iw,ih*528/948)'" in graph
    assert "pad=538:958:5:5:color=black@0" in graph
    assert "force_original_aspect_ratio=increase" not in graph

    screen_element = next(item for item in config.elements if item.kind == "SCREEN")
    screen_element.radius = 8
    with pytest.raises(AppError) as error:
        RenderPlanBuilder().build(context, kind=RenderKind.PREVIEW, quality=RenderQuality.FAST)
    assert error.value.code == "RENDER_RADIUS_UNSUPPORTED"

    screen_element.radius = 0
    captions_element = next(item for item in config.elements if item.kind == "CAPTIONS")
    captions_element.visible = True
    captions_element.z_index = -1
    config.captions.enabled = True
    ordered_context = context.model_copy(
        update={
            "transcript": ResolvedTranscriptSource(
                transcript_id="transcript",
                cache_key="cache",
                media_id="screen",
                audio_stream_index=0,
                caption_cues=[],
                timing_source="SEGMENTS",
            )
        }
    )
    with pytest.raises(AppError) as ordering_error:
        RenderPlanBuilder().build(ordered_context, kind=RenderKind.PREVIEW, quality=RenderQuality.FAST)
    assert ordering_error.value.code == "RENDER_TEXT_LAYER_ORDER_UNSUPPORTED"


def test_word_cues_form_blocks_lines_and_active_word_events(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    style = plan.captions.style
    assert style is not None
    style = style.model_copy(update={"words_per_block": 4, "words_per_line": 2})
    cues = [
        CaptionCue(
            start_ms=index * 200,
            end_ms=(index + 1) * 200,
            text=f"word{index}",
            words=[{"start_ms": index * 200, "end_ms": (index + 1) * 200, "text": f"word{index}"}],
        )
        for index in range(5)
    ]
    captions = plan.captions.model_copy(update={"enabled": True, "style": style, "cues": cues})
    document = build_ass_document(plan.model_copy(update={"captions": captions}))
    assert document is not None
    dialogue = [line for line in document.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogue) == 5
    assert all("word0" in line and "word3" in line for line in dialogue[:4])
    assert r"\N" in dialogue[0]
    assert "&H0000E6FF" in dialogue[0]


def test_caption_and_banner_opacity_are_normalized_into_ass_and_filtergraph(tmp_path: Path) -> None:
    screen = tmp_path / "screen.mp4"
    webcam = tmp_path / "webcam.mp4"
    asset = tmp_path / "banner.png"
    for path in (screen, webcam, asset):
        path.write_bytes(b"fixture")
    config = preset_config(LayoutPreset.WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM)
    captions_layer = next(item for item in config.elements if item.kind == "CAPTIONS")
    banner_layer = next(item for item in config.elements if item.kind == "BANNER")
    captions_layer.opacity = 0.4
    banner_layer.opacity = 0.5
    config.banner.opacity = 0.6
    config.banner.text = "Banner"
    config.banner.image_relative_path = "assets/banner.png"
    context = ResolvedRenderContext(
        project_id=PROJECT_ID,
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
                path=screen,
                sha256="a" * 64,
                duration_ms=1_000,
                video_stream_indexes=[0],
            ),
            ResolvedMediaInput(
                media_id="webcam",
                role=RenderInputRole.WEBCAM,
                path=webcam,
                sha256="b" * 64,
                duration_ms=1_000,
                video_stream_indexes=[0],
                audio_stream_indexes=[1],
            ),
        ],
        transcript=ResolvedTranscriptSource(
            transcript_id="transcript",
            cache_key="cache",
            media_id="webcam",
            audio_stream_index=1,
            caption_cues=[CaptionCue(start_ms=0, end_ms=500, text="caption")],
            timing_source="SEGMENTS",
        ),
        banner_asset=ResolvedBannerAsset(
            relative_path="assets/banner.png",
            path=asset,
            sha256="c" * 64,
        ),
    )
    plan = RenderPlanBuilder().build(context, kind=RenderKind.PREVIEW, quality=RenderQuality.FAST)
    assert plan.captions.opacity == pytest.approx(0.4)
    assert plan.banner.opacity == pytest.approx(0.3)
    assert plan.banner.content_opacity == pytest.approx(0.5)
    assert "&H99" in (build_ass_document(plan) or "")
    graph = FilterGraphBuilder().build(plan).value
    assert "@0.3000" in graph
    assert "colorchannelmixer=aa=0.5000" in graph

    changed = config.model_copy(deep=True)
    next(item for item in changed.elements if item.kind == "CAPTIONS").opacity = 0.5
    changed_plan = RenderPlanBuilder().build(
        context.model_copy(update={"edit_config": changed}),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )
    assert changed_plan.dependency_fingerprint != plan.dependency_fingerprint

    inverse = config.model_copy(deep=True)
    next(item for item in inverse.elements if item.kind == "BANNER").z_index = 6
    with pytest.raises(AppError) as ordering_error:
        RenderPlanBuilder().build(
            context.model_copy(update={"edit_config": inverse}),
            kind=RenderKind.PREVIEW,
            quality=RenderQuality.FAST,
        )
    assert ordering_error.value.code == "RENDER_TEXT_LAYER_ORDER_UNSUPPORTED"


@pytest.mark.parametrize(
    ("preset", "kind", "legacy_radius"),
    [
        (LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY, "WEBCAM", 36),
        (LayoutPreset.WEBCAM_FULLSCREEN_SCREEN_PIP, "SCREEN", 28),
    ],
)
def test_exact_roadmap01_radius_presets_migrate_idempotently_and_custom_edits_do_not(
    tmp_path: Path,
    preset: LayoutPreset,
    kind: str,
    legacy_radius: int,
) -> None:
    opened = EditConfig.model_validate(_roadmap01_literal(preset))
    normalized, migrated = normalize_legacy_edit_config(opened)
    assert migrated is True
    assert next(item for item in normalized.elements if item.kind == kind).radius == 0
    for decorative_kind in {"CAPTIONS", "BANNER"}:
        decorative = next(item for item in normalized.elements if item.kind == decorative_kind)
        assert decorative.radius == 0
        assert decorative.padding == 0
    saved = EditConfig.model_validate_json(normalized.model_dump_json())
    repeated, migrated_again = normalize_legacy_edit_config(saved)
    assert migrated_again is False
    assert repeated == normalized

    custom = opened.model_copy(deep=True)
    custom.background_color = "#123456"
    untouched, custom_migrated = normalize_legacy_edit_config(custom)
    assert custom_migrated is False
    assert next(item for item in untouched.elements if item.kind == kind).radius == legacy_radius

    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    for element in normalized.elements:
        if element.kind not in {"SCREEN", kind}:
            element.visible = False
    normalized.captions.enabled = False
    context = ResolvedRenderContext(
        project_id=PROJECT_ID,
        candidate_id="candidate",
        edit_config_id="edit",
        clip_start_ms=0,
        clip_end_ms=1_000,
        webcam_offset_ms=0,
        edit_config=normalized,
        media=[
            ResolvedMediaInput(
                media_id="source",
                role=RenderInputRole(kind),
                path=source,
                sha256="a" * 64,
                duration_ms=1_000,
                video_stream_indexes=[0],
            )
        ],
    )
    if kind == "WEBCAM":
        next(item for item in normalized.elements if item.kind == "SCREEN").visible = False
    else:
        next(item for item in normalized.elements if item.kind == "WEBCAM").visible = False
    RenderPlanBuilder().build(context, kind=RenderKind.PREVIEW, quality=RenderQuality.FAST)

    custom_for_plan = untouched.model_copy(deep=True)
    for element in custom_for_plan.elements:
        if element.kind != kind:
            element.visible = False
    custom_for_plan.captions.enabled = False
    with pytest.raises(AppError) as custom_error:
        RenderPlanBuilder().build(
            context.model_copy(update={"edit_config": custom_for_plan}),
            kind=RenderKind.PREVIEW,
            quality=RenderQuality.FAST,
        )
    assert custom_error.value.code == "RENDER_RADIUS_UNSUPPORTED"


def test_successful_handler_wins_cancel_request_that_it_did_not_observe(tmp_path: Path) -> None:
    engine = build_engine(tmp_path / "jobs.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=PROJECT_ID, name="Race"))
    runner = LocalJobRunner(factory, object())  # type: ignore[arg-type]
    ready, release = threading.Event(), threading.Event()

    def handler(*_args: object) -> dict[str, object]:
        ready.set()
        assert release.wait(timeout=2)
        return {"artifact_id": "artifact"}

    runner.register_handler("RENDER_PREVIEW", handler)  # type: ignore[arg-type]
    job, _ = runner.create_job(PROJECT_ID, "RENDER_PREVIEW", {})
    runner.submit(job.id)
    assert ready.wait(timeout=2)
    runner.cancel(job.id)
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with factory() as session:
            completed = session.get(JobModel, job.id)
            assert completed is not None
            if completed.status == JobStatus.COMPLETED:
                assert completed.result_data == {"artifact_id": "artifact"}
                assert completed.cancellation_requested is False
                break
        time.sleep(0.01)
    else:
        raise AssertionError("job did not complete")
    runner.shutdown()


@pytest.mark.asyncio
async def test_cancelled_plan_request_holds_its_slot_until_running_worker_finishes() -> None:
    started, release = threading.Event(), threading.Event()

    class BlockingRendering:
        @staticmethod
        def plan(*_args: object) -> object:
            started.set()
            assert release.wait(timeout=2)
            return object()

    executor = ThreadPoolExecutor(max_workers=1)
    slots = asyncio.Semaphore(1)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                render_plan_slots=slots,
                render_plan_executor=executor,
                rendering=BlockingRendering(),
            )
        )
    )
    task = asyncio.create_task(
        _build_plan(request, PROJECT_ID, "candidate", RenderKind.PREVIEW, RenderQuality.FAST)  # type: ignore[arg-type]
    )
    deadline = time.monotonic() + 1
    while not started.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert started.is_set()
    task.cancel()
    await asyncio.sleep(0.05)
    assert slots.locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not slots.locked()
    executor.shutdown(wait=True)
