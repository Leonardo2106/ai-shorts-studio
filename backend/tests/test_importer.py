from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.db.models import MediaRole
from app.media.importer import MediaImporter
from app.projects.storage import ProjectStorage


class RejectingProber:
    def inspect(self, _: Path):
        raise AppError("INVALID_MEDIA", "not a video", status_code=422)


class UploadStub:
    def __init__(self, content: bytes) -> None:
        self.file = BytesIO(content)


@pytest.mark.asyncio
async def test_invalid_upload_is_rejected_without_final_media_file(tmp_path: Path) -> None:
    storage = ProjectStorage(tmp_path / "storage")
    project_id = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"
    project_dir = storage.project_dir(project_id, create=True)
    upload = UploadStub(b"not a video")
    importer = MediaImporter(storage, RejectingProber(), max_bytes=1024, chunk_bytes=64)  # type: ignore[arg-type]

    with pytest.raises(AppError) as error:
        await importer.import_upload(project_id, MediaRole.SCREEN, upload)  # type: ignore[arg-type]

    assert error.value.code == "INVALID_MEDIA"
    assert not (project_dir / "screen.mp4").exists()
    assert not list(project_dir.glob(".upload-*.tmp"))
