from __future__ import annotations

import asyncio
import hashlib
import shutil
import threading
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile

from app.core.errors import AppError
from app.db.models import MediaRole
from app.media.probe import FFprobeService
from app.media.schemas import MediaProbe
from app.projects.storage import ProjectStorage


class ImportedMedia:
    def __init__(self, path: Path, size_bytes: int, sha256: str, probe: MediaProbe) -> None:
        self.path = path
        self.size_bytes = size_bytes
        self.sha256 = sha256
        self.probe = probe


class MediaImporter:
    def __init__(
        self,
        storage: ProjectStorage,
        prober: FFprobeService,
        *,
        max_bytes: int,
        chunk_bytes: int,
        min_free_bytes: int = 1024**3,
        max_concurrent: int = 1,
        probe_executor: Executor | None = None,
    ) -> None:
        self.storage = storage
        self.prober = prober
        self.max_bytes = max_bytes
        self.chunk_bytes = chunk_bytes
        self.min_free_bytes = min_free_bytes
        self.probe_executor = probe_executor
        self._upload_slots = asyncio.Semaphore(max_concurrent)
        self._role_locks: dict[tuple[str, MediaRole], asyncio.Lock] = {}
        self._locks_guard = threading.Lock()

    @asynccontextmanager
    async def reserve(self, project_id: str, role: MediaRole) -> AsyncIterator[None]:
        key = (project_id, role)
        with self._locks_guard:
            lock = self._role_locks.setdefault(key, asyncio.Lock())
        async with self._upload_slots, lock:
            yield

    async def import_upload(
        self, project_id: str, role: MediaRole, upload: UploadFile, *, reserved: bool = False
    ) -> ImportedMedia:
        if not reserved:
            async with self.reserve(project_id, role):
                return await self.import_upload(project_id, role, upload, reserved=True)
        reported_size = upload.size if isinstance(getattr(upload, "size", None), int) else None
        if self.probe_executor is None:
            return self._import_sync(project_id, role, upload.file, reported_size)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.probe_executor,
            self._import_sync,
            project_id,
            role,
            upload.file,
            reported_size,
        )

    def _import_sync(
        self, project_id: str, role: MediaRole, source: BinaryIO, reported_size: int | None
    ) -> ImportedMedia:
        project_dir = self.storage.project_dir(project_id)
        if not project_dir.is_dir():
            raise AppError("PROJECT_STORAGE_MISSING", "Project storage is missing.", status_code=500)
        expected_size = reported_size if isinstance(reported_size, int) and reported_size >= 0 else self.max_bytes
        if expected_size > self.max_bytes:
            raise AppError(
                "UPLOAD_TOO_LARGE",
                "Uploaded media exceeds the configured size limit.",
                status_code=413,
                details={"max_bytes": self.max_bytes},
            )
        self._check_free_space(project_dir, expected_size)
        temp_path = self.storage.project_path(project_id, f".upload-{uuid.uuid4().hex}.tmp")
        destination = self.storage.project_path(project_id, f"{role.value.lower()}.mp4")
        digest = hashlib.sha256()
        size = 0
        try:
            with temp_path.open("xb") as target:
                self.storage.make_private(temp_path)
                while chunk := source.read(self.chunk_bytes):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise AppError(
                            "UPLOAD_TOO_LARGE",
                            "Uploaded media exceeds the configured size limit.",
                            status_code=413,
                            details={"max_bytes": self.max_bytes},
                        )
                    digest.update(chunk)
                    target.write(chunk)
                    if size % (64 * 1024**2) < len(chunk):
                        self._check_free_space(project_dir, max(0, expected_size - size))
            if size == 0:
                raise AppError("EMPTY_UPLOAD", "Uploaded media is empty.", status_code=422)
            probe = self.prober.inspect(temp_path)
            self.storage.atomic_replace(temp_path, destination)
            return ImportedMedia(destination, size, digest.hexdigest(), probe)
        finally:
            source.close()
            temp_path.unlink(missing_ok=True)

    def _check_free_space(self, directory: Path, remaining_bytes: int) -> None:
        if shutil.disk_usage(directory).free < self.min_free_bytes + max(0, remaining_bytes):
            raise AppError("INSUFFICIENT_STORAGE", "Not enough free disk space for media import.", status_code=507)


def safe_original_filename(value: str | None, fallback: str) -> str:
    raw = (value or fallback).replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in raw if ord(character) >= 32 and character not in '<>:"/\\|?*')
    cleaned = cleaned.strip(" .")[:255]
    stem = cleaned.partition(".")[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if not cleaned or stem in reserved:
        return fallback
    return cleaned
