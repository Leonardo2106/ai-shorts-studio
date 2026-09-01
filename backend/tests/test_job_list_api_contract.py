from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.core.settings import Settings
from app.db.models import JobModel, JobStatus, ProjectModel
from app.main import create_app


@pytest.mark.asyncio
async def test_project_job_list_is_sanitized_filtered_limited_and_newest_first(tmp_path: Path) -> None:
    app = create_app(Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"]))
    async with app.router.lifespan_context(app):
        project_id = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"
        other_project_id = "1db23039-1e08-49c3-ac37-7a121416e556"
        now = datetime.now(UTC)
        with app.state.session_factory.begin() as session:
            session.add_all(
                [
                    ProjectModel(id=project_id, name="Reload jobs"),
                    ProjectModel(id=other_project_id, name="Other project"),
                ]
            )
            session.flush()
            session.add_all(
                [
                    JobModel(
                        id="00000000-0000-4000-8000-000000000001",
                        project_id=project_id,
                        kind="SEMANTIC_ANALYSIS",
                        status=JobStatus.FAILED,
                        request_data={"api_key": "must-never-be-listed", "prompt": "private transcript"},
                        error_code="PROVIDER_REQUEST_FAILED",
                        error_message="Provider request failed.",
                        result_data={"error_details": {"provider": "OPENAI", "provider_status": 429}},
                        created_at=now,
                    ),
                    JobModel(
                        id="00000000-0000-4000-8000-000000000002",
                        project_id=project_id,
                        kind="VISION_ANALYSIS",
                        status=JobStatus.COMPLETED,
                        request_data={"private": "vision request"},
                        result_data={"candidate_ids": ["candidate"]},
                        progress=1,
                        created_at=now + timedelta(seconds=1),
                    ),
                    JobModel(
                        id="00000000-0000-4000-8000-000000000003",
                        project_id=project_id,
                        kind="RENDER_FINAL",
                        status=JobStatus.RUNNING,
                        request_data={"expected_dependency_fingerprint": "private-internal"},
                        created_at=now + timedelta(seconds=2),
                    ),
                    JobModel(
                        id="00000000-0000-4000-8000-000000000004",
                        project_id=other_project_id,
                        kind="SEMANTIC_ANALYSIS",
                        status=JobStatus.FAILED,
                        request_data={"prompt": "other private transcript"},
                        created_at=now + timedelta(seconds=3),
                    ),
                ]
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            limited = await client.get(f"/api/v1/projects/{project_id}/jobs", params={"limit": 2})
            filtered = await client.get(
                f"/api/v1/projects/{project_id}/jobs",
                params={"kind": "SEMANTIC_ANALYSIS", "status": "FAILED", "limit": 1},
            )

            assert limited.status_code == filtered.status_code == 200
            assert [item["id"] for item in limited.json()["items"]] == [
                "00000000-0000-4000-8000-000000000003",
                "00000000-0000-4000-8000-000000000002",
            ]
            assert [item["id"] for item in filtered.json()["items"]] == [
                "00000000-0000-4000-8000-000000000001"
            ]
            assert filtered.json()["items"][0]["error"]["details"] == {
                "provider": "OPENAI",
                "provider_status": 429,
            }
            serialized = limited.text + filtered.text
            assert "request_data" not in serialized
            assert "must-never-be-listed" not in serialized
            assert "private transcript" not in serialized
            assert other_project_id not in serialized


@pytest.mark.asyncio
async def test_project_job_list_validates_project_filters_and_limit(tmp_path: Path) -> None:
    app = create_app(Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"]))
    async with app.router.lifespan_context(app):
        project_id = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"
        with app.state.session_factory.begin() as session:
            session.add(ProjectModel(id=project_id, name="Filters"))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.get("/api/v1/projects/00000000-0000-4000-8000-000000000000/jobs")
            invalid_kind = await client.get(
                f"/api/v1/projects/{project_id}/jobs", params={"kind": "ARBITRARY_JOB"}
            )
            invalid_status = await client.get(
                f"/api/v1/projects/{project_id}/jobs", params={"status": "UNKNOWN"}
            )
            zero_limit = await client.get(f"/api/v1/projects/{project_id}/jobs", params={"limit": 0})
            large_limit = await client.get(f"/api/v1/projects/{project_id}/jobs", params={"limit": 201})

            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "PROJECT_NOT_FOUND"
            for response in (invalid_kind, invalid_status, zero_limit, large_limit):
                assert response.status_code == 422
                assert response.json()["error"]["code"] == "VALIDATION_ERROR"
