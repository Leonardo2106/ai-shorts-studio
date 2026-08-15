from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.core.settings import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_health_and_project_lifecycle(tmp_path: Path) -> None:
    app = create_app(Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"]))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/health")).json() == {"status": "ok"}
            created = await client.post("/api/v1/projects", json={"name": "  Recording  "})
            assert created.status_code == 201
            project = created.json()
            assert project["name"] == "Recording"
            assert project["stage"] == "EMPTY"
            assert (tmp_path / "storage" / "projects" / project["id"]).is_dir()
            projects = await client.get("/api/v1/projects")
            assert projects.json()["items"][0]["id"] == project["id"]
            opened = await client.get(f"/api/v1/projects/{project['id']}")
            assert opened.status_code == 200


@pytest.mark.asyncio
async def test_sync_requires_both_media_and_returns_uniform_error(tmp_path: Path) -> None:
    app = create_app(Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"]))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/projects", json={"name": "Sync"})
            project_id = created.json()["id"]
            response = await client.patch(f"/api/v1/projects/{project_id}/sync", json={"webcam_offset_ms": 100})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MEDIA_NOT_READY"


@pytest.mark.asyncio
async def test_invalid_project_id_has_predictable_error(tmp_path: Path) -> None:
    app = create_app(Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"]))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/projects/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PROJECT_ID"
