from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.core.settings import Settings
from app.db.models import CandidateModel, MediaModel, MediaRole, TranscriptModel
from app.editor.schemas import LayoutPreset, preset_config
from app.main import create_app


@pytest.mark.asyncio
async def test_roadmap01_api_preserves_editor_intent_and_never_exposes_provider_key(tmp_path: Path) -> None:
    secret = "secret-that-must-never-reach-browser"
    app = create_app(
        Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"], openai_api_key=SecretStr(secret))
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            project_response = await client.post("/api/v1/projects", json={"name": "Roadmap 01"})
            assert project_response.status_code == 201
            project_id = project_response.json()["id"]
            media_id, transcript_id, candidate_id = "media", "transcript", "candidate"
            transcript_path = app.state.storage.project_path(project_id, "transcripts/fixture.json")
            transcript_path.parent.mkdir()
            transcript_path.write_text(
                json.dumps(
                    {
                        "id": transcript_id,
                        "project_id": project_id,
                        "media_id": media_id,
                        "source": "SCREEN",
                        "audio_stream_index": 1,
                        "language": "pt",
                        "duration_ms": 30_000,
                        "engine": "fixture",
                        "model": "fixture",
                        "segments": [
                            {
                                "start_ms": 0,
                                "end_ms": 4_000,
                                "text": "opening context",
                                "words": [
                                    {"start_ms": 0, "end_ms": 2_000, "text": "opening"},
                                    {"start_ms": 2_000, "end_ms": 4_000, "text": "context"},
                                ],
                            },
                            {"start_ms": 4_000, "end_ms": 8_000, "text": "new selected excerpt"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
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
                        probe_data={"duration_ms": 30_000, "audio_streams": []},
                    )
                )
                session.flush()
                session.add(
                    TranscriptModel(
                        id=transcript_id,
                        project_id=project_id,
                        media_id=media_id,
                        cache_key="b" * 64,
                        relative_path="transcripts/fixture.json",
                        language="pt",
                        duration_ms=30_000,
                    )
                )
                session.flush()
                session.add(
                    CandidateModel(
                        id=candidate_id,
                        project_id=project_id,
                        transcript_id=transcript_id,
                        start_ms=0,
                        end_ms=20_000,
                        title="Candidate",
                        reasons=[],
                        context={"source": "SCREEN", "text": "stale excerpt"},
                        signals={"hook": 1.0},
                        score=90,
                        score_breakdown=[{"rule": "hook", "contribution": 1.0}],
                    )
                )

            capabilities = await client.get("/api/v1/capabilities")
            assert capabilities.status_code == 200
            assert secret not in capabilities.text
            provider = next(item for item in capabilities.json()["ai_providers"] if item["provider"] == "OPENAI")
            assert provider["configured"] is True
            assert set(provider) == {"provider", "configured", "models", "parameters"}

            estimate = await client.post(
                f"/api/v1/projects/{project_id}/semantic-analysis/estimate",
                json={
                    "provider": "OPENAI",
                    "model": "gpt-4.1-mini",
                    "candidate_ids": [candidate_id],
                    "opt_in_external_processing": True,
                    "retries": 1,
                },
            )
            assert estimate.status_code == 200
            assert estimate.json() == {
                "chunks": 1,
                "estimated_input_tokens": 29,
                "candidates": 1,
                "planned_provider_calls": 2,
                "max_provider_calls": 24,
            }

            presets = await client.get("/api/v1/editor/presets")
            assert presets.status_code == 200
            assert set(presets.json()) == {item.value for item in LayoutPreset}
            preset_payloads = presets.json()
            expected_contract = {
                "WEBCAM_TOP_SCREEN_BOTTOM": (["WEBCAM", "SCREEN", "CAPTIONS", "BANNER"], "CONTAIN", False),
                "WEBCAM_TOP_SCREEN_MIDDLE_BANNER_BOTTOM": (
                    ["WEBCAM", "SCREEN", "CAPTIONS", "BANNER"],
                    "CONTAIN",
                    True,
                ),
                "SCREEN_FULLSCREEN_WEBCAM_OVERLAY": (
                    ["SCREEN", "WEBCAM", "CAPTIONS", "BANNER"],
                    "COVER",
                    False,
                ),
                "WEBCAM_FULLSCREEN_SCREEN_PIP": (
                    ["WEBCAM", "SCREEN", "CAPTIONS", "BANNER"],
                    "CONTAIN",
                    False,
                ),
            }
            for preset_id, (element_order, screen_fit, banner_enabled) in expected_contract.items():
                config = preset_payloads[preset_id]
                assert [item["kind"] for item in config["elements"]] == element_order
                screen_element = next(item for item in config["elements"] if item["kind"] == "SCREEN")
                assert screen_element["fit"] == screen_fit
                assert all(
                    (item["radius"], item["padding"], item["zoom"], item["focal_x"], item["focal_y"])
                    == (0, 0, 1.0, 0.5, 0.5)
                    for item in config["elements"]
                )
                assert config["background_color"] == "#080C14"
                assert config["banner"] == {
                    "enabled": banner_enabled,
                    "text": "Seu título aqui",
                    "image_relative_path": None,
                    "background_color": "#0F172A",
                    "opacity": 0.9,
                    "start_ms": 0,
                    "end_ms": None,
                }
                assert config["captions"]["font_family"] == "Arial"
                assert config["captions"]["outline_color"] == "#000000"

            payload = preset_config(LayoutPreset.WEBCAM_TOP_SCREEN_BOTTOM).model_dump(mode="json")
            payload["filtergraph"] = "[0:v]unsafe-command"
            rejected = await client.put(
                f"/api/v1/projects/{project_id}/candidates/{candidate_id}/edit-config", json=payload
            )
            assert rejected.status_code == 422
            assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"

            payload.pop("filtergraph")
            saved = await client.put(
                f"/api/v1/projects/{project_id}/candidates/{candidate_id}/edit-config", json=payload
            )
            assert saved.status_code == 200
            assert "filtergraph" not in json.dumps(saved.json())

            reopened = await client.get(f"/api/v1/projects/{project_id}/candidates/{candidate_id}/edit-config")
            assert reopened.status_code == 200
            assert reopened.json()["config"] == saved.json()["config"]

            adjusted = await client.patch(
                f"/api/v1/projects/{project_id}/candidates/{candidate_id}",
                json={"start_ms": 1_000, "end_ms": 6_000},
            )
            assert adjusted.status_code == 200
            adjusted_body = adjusted.json()
            assert adjusted_body["text"] == "opening context new selected excerpt"
            assert adjusted_body["context"]["source_start_ms"] == 1_000
            assert adjusted_body["score"] is None
            assert adjusted_body["score_breakdown"] is None
            assert adjusted_body["signals"]["context_completeness"] == 1.0

            invalid_cut = await client.patch(
                f"/api/v1/projects/{project_id}/candidates/{candidate_id}", json={"end_ms": 30_001}
            )
            assert invalid_cut.status_code == 422
            assert invalid_cut.json()["error"]["code"] == "INVALID_CANDIDATE_RANGE"
