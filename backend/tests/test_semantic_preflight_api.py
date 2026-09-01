from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.ai.schemas import AIProvider, SemanticAnalysisRequest
from app.core.settings import Settings
from app.db.models import CandidateModel, JobModel, MediaModel, MediaRole, TranscriptModel
from app.main import create_app


class _NeverCalledProvider:
    calls = 0

    def analyze(
        self, _payload: list[dict[str, object]], _request: SemanticAnalysisRequest
    ) -> list[dict[str, object]]:
        self.calls += 1
        raise AssertionError("preflight must not call a provider adapter")


@pytest.mark.asyncio
async def test_semantic_preflight_is_synchronous_shared_and_does_not_create_jobs(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            storage_root=tmp_path / "storage",
            allowed_hosts=["test"],
            openai_api_key=SecretStr("test-openai-key"),
            gemini_api_key=None,
            groq_api_key=None,
        )
    )
    async with app.router.lifespan_context(app):
        adapter = _NeverCalledProvider()
        app.state.semantic_analysis.adapters[AIProvider.OPENAI] = adapter
        app.state.job_runner.submit = lambda _job_id: None
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/projects", json={"name": "Semantic preflight"})
            project_id = created.json()["id"]
            with app.state.session_factory.begin() as session:
                session.add(
                    MediaModel(
                        id="media",
                        project_id=project_id,
                        role=MediaRole.SCREEN,
                        relative_path=f"projects/{project_id}/media/screen.mp4",
                        original_filename="screen.mp4",
                        size_bytes=1,
                        sha256="a" * 64,
                        probe_data={"duration_ms": 10_000, "audio_streams": []},
                    )
                )
                session.flush()
                session.add(
                    TranscriptModel(
                        id="transcript",
                        project_id=project_id,
                        media_id="media",
                        cache_key="b" * 64,
                        relative_path="transcripts/transcript.json",
                        language="pt",
                        duration_ms=10_000,
                    )
                )
                session.flush()
                session.add(
                    CandidateModel(
                        id="candidate",
                        project_id=project_id,
                        transcript_id="transcript",
                        start_ms=0,
                        end_ms=5_000,
                        title="Candidate",
                        reasons=[],
                        context={"text": "local excerpt"},
                        signals={},
                    )
                )

            base = {
                "provider": "OPENAI",
                "model": "gpt-4.1-mini",
                "candidate_ids": ["candidate"],
                "opt_in_external_processing": True,
                "retries": 0,
            }
            invalid_requests = [
                ({**base, "model": "not-a-model"}, 422, "UNSUPPORTED_PROVIDER_MODEL"),
                ({**base, "reasoning_effort": "low"}, 422, "UNSUPPORTED_PROVIDER_PARAMETER"),
                ({**base, "fallback_provider": "GROQ"}, 409, "FALLBACK_PROVIDER_NOT_CONFIGURED"),
            ]
            for payload, expected_status, expected_code in invalid_requests:
                estimate = await client.post(
                    f"/api/v1/projects/{project_id}/semantic-analysis/estimate", json=payload
                )
                started = await client.post(
                    f"/api/v1/projects/{project_id}/semantic-analysis-jobs", json=payload
                )
                assert estimate.status_code == started.status_code == expected_status
                assert estimate.json()["error"]["code"] == started.json()["error"]["code"] == expected_code

            with app.state.session_factory() as session:
                assert session.scalars(select(JobModel)).all() == []
            assert adapter.calls == 0

            valid_estimate = await client.post(
                f"/api/v1/projects/{project_id}/semantic-analysis/estimate", json=base
            )
            valid_job = await client.post(
                f"/api/v1/projects/{project_id}/semantic-analysis-jobs", json=base
            )

            assert valid_estimate.status_code == 200
            assert valid_estimate.json()["planned_provider_calls"] == 1
            assert valid_job.status_code == 202
            with app.state.session_factory() as session:
                jobs = session.scalars(select(JobModel)).all()
                assert len(jobs) == 1
                assert jobs[0].id == valid_job.json()["id"]
            assert adapter.calls == 0
