from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from app.core.settings import Settings
from app.db.models import (
    CandidateModel,
    EditConfigModel,
    JobModel,
    MediaModel,
    MediaRole,
    RenderArtifactModel,
    TranscriptModel,
)
from app.editor.schemas import EditConfig, LayoutPreset, preset_config
from app.main import create_app
from app.rendering.plan import RenderPlanBuilder
from app.rendering.schemas import (
    RenderInputRole,
    RenderKind,
    RenderQuality,
    ResolvedMediaInput,
    ResolvedRenderContext,
)


@dataclass
class _FakeRendering:
    result: object
    calls: list[tuple[str, str, RenderKind, RenderQuality]]

    def plan(self, project_id: str, candidate_id: str, kind: RenderKind, quality: RenderQuality) -> object:
        self.calls.append((project_id, candidate_id, kind, quality))
        return self.result


class _AvailableRenderer:
    def __init__(self) -> None:
        self.calls = 0

    def ensure_available(self) -> None:
        self.calls += 1


def _plan(tmp_path: Path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    config = preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY)
    for element in config.elements:
        if element.kind in {"WEBCAM", "CAPTIONS", "BANNER"}:
            element.visible = False
    config.captions.enabled = False
    config.banner.enabled = False
    return RenderPlanBuilder().build(
        ResolvedRenderContext(
            project_id="placeholder",
            candidate_id="candidate",
            edit_config_id="edit",
            clip_start_ms=0,
            clip_end_ms=1_000,
            webcam_offset_ms=0,
            edit_config=config,
            media=[
                ResolvedMediaInput(
                    media_id="screen-media",
                    role=RenderInputRole.SCREEN,
                    path=source,
                    sha256="a" * 64,
                    duration_ms=2_000,
                    video_stream_indexes=[0],
                    audio_stream_indexes=[],
                )
            ],
        ),
        kind=RenderKind.PREVIEW,
        quality=RenderQuality.FAST,
    )


async def _seed_project_candidate(app, client: httpx.AsyncClient) -> tuple[str, str, str]:
    response = await client.post("/api/v1/projects", json={"name": "Rendering API"})
    assert response.status_code == 201
    project_id = response.json()["id"]
    media_id, transcript_id = "media-render", "transcript-render"
    candidate_id, edit_config_id = "candidate-render", "edit-render"
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
                probe_data={"duration_ms": 2_000, "video_streams": [{"index": 0}], "audio_streams": []},
            )
        )
        session.flush()
        session.add(
            TranscriptModel(
                id=transcript_id,
                project_id=project_id,
                media_id=media_id,
                cache_key="c" * 64,
                relative_path="transcripts/unused.json",
                duration_ms=1_000,
            )
        )
        session.flush()
        session.add(
            CandidateModel(
                id=candidate_id,
                project_id=project_id,
                transcript_id=transcript_id,
                start_ms=0,
                end_ms=1_000,
                title="Candidate",
                reasons=[],
                context={},
                signals={},
                score=None,
                score_breakdown=None,
            )
        )
        session.add(
            EditConfigModel(
                id=edit_config_id,
                project_id=project_id,
                candidate_id=candidate_id,
                config=preset_config(LayoutPreset.SCREEN_FULLSCREEN_WEBCAM_OVERLAY).model_dump(mode="json"),
            )
        )
    return project_id, candidate_id, edit_config_id


@pytest.mark.asyncio
async def test_rendering_api_exposes_sanitized_plan_jobs_artifacts_range_and_cancellation(tmp_path: Path) -> None:
    app = create_app(Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"]))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=2.0) as client:
            project_id, candidate_id, edit_config_id = await _seed_project_candidate(app, client)
            plan = _plan(tmp_path).model_copy(
                update={
                    "project_id": project_id,
                    "candidate_id": candidate_id,
                    "edit_config_id": edit_config_id,
                }
            )
            rendering = _FakeRendering(plan, [])
            renderer = _AvailableRenderer()
            submitted: list[str] = []
            app.state.rendering = rendering
            app.state.renderer = renderer
            app.state.job_runner.submit = submitted.append

            inspected = await asyncio.wait_for(
                client.get(
                    f"/api/v1/projects/{project_id}/candidates/{candidate_id}/render-plan",
                    params={"kind": "PREVIEW", "quality": "FAST"},
                ),
                timeout=2.0,
            )
            assert inspected.status_code == 200
            inspected_json = inspected.json()
            assert inspected_json["inputs"][0]["path"] == "media/screen-media"
            assert str(tmp_path) not in inspected.text

            preview = await asyncio.wait_for(
                client.post(
                    f"/api/v1/projects/{project_id}/candidates/{candidate_id}/preview-jobs",
                    json={"quality": "FAST"},
                ),
                timeout=2.0,
            )
            final = await asyncio.wait_for(
                client.post(
                    f"/api/v1/projects/{project_id}/candidates/{candidate_id}/render-jobs",
                    json={"quality": "HIGH"},
                ),
                timeout=2.0,
            )
            assert preview.status_code == final.status_code == 202
            assert {preview.json()["kind"], final.json()["kind"]} == {"RENDER_PREVIEW", "RENDER_FINAL"}
            assert submitted == [preview.json()["id"], final.json()["id"]]
            assert renderer.calls == 2
            with app.state.session_factory() as session:
                queued = session.get(JobModel, final.json()["id"])
                assert queued is not None
                assert queued.request_data["expected_edit_config_fingerprint"] == plan.edit_config_fingerprint
                assert queued.request_data["expected_dependency_fingerprint"] == plan.dependency_fingerprint

            cancelled = await asyncio.wait_for(client.post(f"/api/v1/jobs/{preview.json()['id']}/cancel"), timeout=2.0)
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "CANCELLED"
            assert cancelled.json()["cancellation_requested"] is True
            with app.state.session_factory.begin() as session:
                session.add_all(
                    [
                        JobModel(
                            project_id=project_id,
                            kind="RENDER_PREVIEW",
                            status="CANCELLED",
                            request_data={"candidate_id": f"other-{index}"},
                        )
                        for index in range(205)
                    ]
                )
            recovered = await client.get(
                f"/api/v1/projects/{project_id}/render-jobs",
                params={
                    "candidate_id": candidate_id,
                    "kind": "RENDER_PREVIEW",
                    "status": "CANCELLED",
                },
            )
            assert recovered.status_code == 200
            assert [item["id"] for item in recovered.json()["items"]] == [preview.json()["id"]]

            content = b"small mp4 fixture"
            output = app.state.storage.project_path(project_id, "renders/short áé.mp4")
            output.parent.mkdir()
            output.write_bytes(content)
            with app.state.session_factory.begin() as session:
                job = session.get(JobModel, final.json()["id"])
                assert job is not None
                artifact = RenderArtifactModel(
                    id="artifact-render",
                    project_id=project_id,
                    candidate_id=candidate_id,
                    edit_config_id=edit_config_id,
                    job_id=job.id,
                    kind="FINAL",
                    quality="HIGH",
                    dependency_fingerprint="b" * 64,
                    relative_path="renders/short áé.mp4",
                    size_bytes=len(content),
                    duration_ms=1_000,
                    width=1080,
                    height=1920,
                    has_audio=True,
                )
                session.add(artifact)

            listed = await asyncio.wait_for(client.get(f"/api/v1/projects/{project_id}/artifacts"), timeout=2.0)
            fetched = await asyncio.wait_for(
                client.get(f"/api/v1/projects/{project_id}/artifacts/artifact-render"), timeout=2.0
            )
            assert listed.status_code == fetched.status_code == 200
            assert listed.json()["items"][0]["content_url"].endswith("/artifact-render/content")
            assert "relative_path" not in fetched.json()
            filtered = await client.get(
                f"/api/v1/projects/{project_id}/artifacts",
                params={"candidate_id": candidate_id, "kind": "FINAL"},
            )
            empty = await client.get(
                f"/api/v1/projects/{project_id}/artifacts",
                params={"candidate_id": candidate_id, "kind": "PREVIEW"},
            )
            assert [item["id"] for item in filtered.json()["items"]] == ["artifact-render"]
            assert empty.json() == {"items": []}

            ranged = await asyncio.wait_for(
                client.get(
                    f"/api/v1/projects/{project_id}/artifacts/artifact-render/content",
                    headers={"Range": "bytes=1-5"},
                ),
                timeout=2.0,
            )
            assert ranged.status_code == 206
            assert ranged.content == content[1:6]
            assert ranged.headers["content-range"] == f"bytes 1-5/{len(content)}"

            def legacy_element(**values: object) -> dict[str, object]:
                return {
                    "z_index": 0, "visible": True, "fit": "COVER", "opacity": 1,
                    "border_width": 0, "border_color": "#000000", "radius": 0, "padding": 0,
                    **values,
                }

            legacy = EditConfig.model_validate(
                {
                    "schema_version": 1,
                    "canvas_width": 1080,
                    "canvas_height": 1920,
                    "preset": "SCREEN_FULLSCREEN_WEBCAM_OVERLAY",
                    "elements": [
                        legacy_element(id="screen", kind="SCREEN", x=0, y=0, width=1080, height=1920),
                        legacy_element(
                            id="webcam", kind="WEBCAM", x=700, y=80, width=320, height=480,
                            z_index=2, radius=36, border_width=6, border_color="#FFFFFF",
                        ),
                        legacy_element(
                            id="captions", kind="CAPTIONS", x=60, y=1390, width=960, height=300,
                            z_index=20, padding=24, radius=16,
                        ),
                        legacy_element(
                            id="banner", kind="BANNER", x=0, y=1680, width=1080, height=240,
                            visible=False, z_index=10, opacity=0.95, radius=24, padding=28,
                        ),
                    ],
                    "captions": {
                        "enabled": True, "font_family": "Inter", "font_size": 64,
                        "color": "#FFFFFF", "weight": 800, "uppercase": False,
                        "outline_width": 4, "shadow": True, "box_color": "#000000",
                        "max_width": 960, "words_per_line": 5, "words_per_block": 10,
                        "active_word_color": "#38BDF8",
                    },
                    "banner": {
                        "enabled": False, "text": "Seu título aqui", "image_relative_path": None,
                        "background_color": "#0F172A", "opacity": 0.9,
                        "start_ms": 0, "end_ms": None,
                    },
                    "background_color": "#080C14",
                }
            )
            with app.state.session_factory.begin() as session:
                stored_edit = session.get(EditConfigModel, edit_config_id)
                assert stored_edit is not None
                legacy_payload = legacy.model_dump(mode="json")
                legacy_payload["schema_version"] = 1
                legacy_payload.pop("audio")
                stored_edit.schema_version = 1
                stored_edit.config = legacy_payload
            opened = await client.get(
                f"/api/v1/projects/{project_id}/candidates/{candidate_id}/edit-config"
            )
            assert opened.status_code == 200
            assert opened.json()["schema_version"] == 2
            assert opened.json()["config"]["schema_version"] == 2
            assert opened.json()["config"]["audio"] == {"mode": "TRANSCRIPT_DEFAULT", "tracks": []}
            opened_webcam = next(item for item in opened.json()["config"]["elements"] if item["kind"] == "WEBCAM")
            assert opened_webcam["radius"] == 0
            opened_captions = next(
                item for item in opened.json()["config"]["elements"] if item["kind"] == "CAPTIONS"
            )
            assert opened_captions["radius"] == opened_captions["padding"] == 0
            saved = await client.put(
                f"/api/v1/projects/{project_id}/candidates/{candidate_id}/edit-config",
                json=legacy_payload,
            )
            assert saved.status_code == 200
            assert saved.json()["schema_version"] == 2
            saved_webcam = next(item for item in saved.json()["config"]["elements"] if item["kind"] == "WEBCAM")
            assert saved_webcam["radius"] == 0
