from __future__ import annotations

import json
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from fastapi import UploadFile

from app.core.settings import Settings
from app.db.models import TranscriptModel
from app.main import create_app
from app.media.importer import ImportedMedia
from app.media.schemas import AudioStream, MediaProbe, VideoStream


class FakeImporter:
    """Writes fixed test bytes while exercising the HTTP multipart contract."""

    def __init__(self, storage: object) -> None:
        self.storage = storage

    @asynccontextmanager
    async def reserve(self, *_: object):
        yield

    async def import_upload(self, project_id: str, role: object, upload: UploadFile, **_: object) -> ImportedMedia:
        payload = await upload.read()
        destination = self.storage.project_path(project_id, f"{role.value.lower()}.mp4")
        destination.write_bytes(payload)
        probe = MediaProbe(
            duration_ms=5_000,
            format_name="mov,mp4",
            video_streams=[VideoStream(index=0, codec_name="h264", width=640, height=360, fps=30)],
            audio_streams=[AudioStream(index=1, codec_name="aac", sample_rate=48_000, channels=2)],
        )
        return ImportedMedia(destination, len(payload), sha256(payload).hexdigest(), probe)


@pytest.mark.asyncio
async def test_vertical_project_media_range_sync_job_and_transcript(tmp_path: Path) -> None:
    app = create_app(Settings(storage_root=tmp_path / "storage", allowed_hosts=["test"]))
    async with app.router.lifespan_context(app):
        app.state.importer = FakeImporter(app.state.storage)
        # Keep a newly-created job pending: no local FFmpeg/Whisper is needed in this API test.
        app.state.transcription.ensure_available = lambda: None
        app.state.job_runner.submit_transcription = lambda _job_id: None
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            created = await client.post("/api/v1/projects", json={"name": "Vertical"})
            assert created.status_code == 201
            project = created.json()
            project_id = project["id"]

            screen = await client.post(
                f"/api/v1/projects/{project_id}/media",
                data={"role": "SCREEN"},
                files={"file": ("screen.mp4", b"screen-bytes", "video/mp4")},
            )
            webcam = await client.post(
                f"/api/v1/projects/{project_id}/media",
                data={"role": "WEBCAM"},
                files={"file": ("webcam.mp4", b"webcam-bytes", "video/mp4")},
            )
            assert screen.status_code == webcam.status_code == 201
            screen_media = screen.json()
            assert screen_media["probe"]["audio_streams"][0]["index"] == 1
            opened = await client.get(f"/api/v1/projects/{project_id}")
            assert opened.json()["stage"] == "MEDIA_READY"

            content_url = screen_media["content_url"]
            full = await client.get(content_url)
            partial = await client.get(content_url, headers={"Range": "bytes=1-6"})
            invalid_range = await client.get(content_url, headers={"Range": "bytes=99-100"})
            assert (full.status_code, full.content) == (200, b"screen-bytes")
            assert (partial.status_code, partial.content) == (206, b"creen-")
            assert partial.headers["content-range"] == "bytes 1-6/12"
            assert invalid_range.status_code == 416

            synced = await client.patch(
                f"/api/v1/projects/{project_id}/sync", json={"webcam_offset_ms": 500}
            )
            assert synced.status_code == 200
            assert synced.json()["webcam_offset_ms"] == 500

            job = await client.post(
                f"/api/v1/projects/{project_id}/transcription-jobs",
                json={"media_id": screen_media["id"], "audio_stream_index": 1, "preset": "BALANCED"},
            )
            assert job.status_code == 202
            job_id = job.json()["id"]
            polled = await client.get(f"/api/v1/jobs/{job_id}")
            assert polled.json()["status"] == "PENDING"
            cancelled = await client.post(f"/api/v1/jobs/{job_id}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "CANCELLED"

            transcript_id = "c8effc6b-676b-45fd-8e8e-34eb97c3641c"
            transcript_path = app.state.storage.project_path(project_id, "transcripts/fake.json")
            transcript_path.parent.mkdir(exist_ok=True)
            transcript_path.write_text(
                json.dumps(
                    {
                        "id": transcript_id,
                        "project_id": project_id,
                        "media_id": screen_media["id"],
                        "source": "SCREEN",
                        "audio_stream_index": 1,
                        "language": "pt",
                        "duration_ms": 1000,
                        "engine": "fake",
                        "model": "fake",
                        "segments": [{"start_ms": 0, "end_ms": 1000, "text": "olá"}],
                    }
                ),
                encoding="utf-8",
            )
            with app.state.session_factory.begin() as session:
                session.add(
                    TranscriptModel(
                        id=transcript_id,
                        project_id=project_id,
                        media_id=screen_media["id"],
                        cache_key="c" * 64,
                        relative_path="transcripts/fake.json",
                        language="pt",
                        duration_ms=1000,
                    )
                )
            transcript = await client.get(f"/api/v1/projects/{project_id}/transcripts/{transcript_id}")
            assert transcript.status_code == 200
            assert transcript.json()["segments"] == [
                {"start_ms": 0, "end_ms": 1000, "text": "olá", "words": None}
            ]
