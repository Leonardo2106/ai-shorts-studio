from __future__ import annotations

import os
import stat
import uuid
from contextlib import suppress
from pathlib import Path, PurePath
from typing import BinaryIO

from app.core.errors import AppError


class ProjectStorage:
    def __init__(self, root: Path) -> None:
        requested_root = root.absolute()
        requested_root.mkdir(parents=True, exist_ok=True)
        if requested_root.is_symlink():
            raise AppError("UNSAFE_STORAGE", "Storage root cannot be a symbolic link.", status_code=500)
        self.root = requested_root.resolve()
        self.make_private(self.root, directory=True)
        projects_path = self.root / "projects"
        if projects_path.exists() and projects_path.is_symlink():
            raise AppError("UNSAFE_STORAGE", "Projects root cannot be a symbolic link.", status_code=500)
        self.projects_root = projects_path.resolve()
        self.projects_root.mkdir(parents=True, exist_ok=True)
        self.make_private(self.projects_root, directory=True)

    def project_dir(self, project_id: str | uuid.UUID, *, create: bool = False) -> Path:
        try:
            normalized = str(uuid.UUID(str(project_id)))
        except ValueError as exc:
            raise AppError("INVALID_PROJECT_ID", "Project id must be a UUID.", status_code=422) from exc
        path = self.safe_path(Path("projects") / normalized)
        if create:
            path.mkdir(parents=False, exist_ok=True)
            self.make_private(path, directory=True)
        return path

    def project_path(self, project_id: str | uuid.UUID, relative_path: str | Path) -> Path:
        project_dir = self.project_dir(project_id)
        relative = Path(relative_path)
        if relative.is_absolute() or not relative.parts or any(part in {"..", ""} for part in relative.parts):
            raise AppError("INVALID_PATH", "Project path must be a safe relative path.", status_code=400)
        candidate = self.safe_path(Path("projects") / str(uuid.UUID(str(project_id))) / relative)
        try:
            candidate.relative_to(project_dir)
        except ValueError as exc:
            raise AppError("PATH_TRAVERSAL", "Path escapes project storage.", status_code=400) from exc
        return candidate

    def safe_path(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or any(part in {"..", ""} for part in PurePath(relative).parts):
            raise AppError("INVALID_PATH", "Path must be relative and contained in storage.", status_code=400)
        candidate = self.root.joinpath(relative)
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise AppError(
                    "SYMLINK_REJECTED",
                    "Symbolic links are not allowed in storage.",
                    status_code=400,
                )
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise AppError("PATH_TRAVERSAL", "Path escapes the configured storage.", status_code=400) from exc
        return resolved

    def relative(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise AppError("PATH_TRAVERSAL", "Path escapes the configured storage.", status_code=400) from exc

    @staticmethod
    def atomic_replace(source: Path, destination: Path) -> None:
        os.replace(source, destination)
        ProjectStorage.make_private(destination)

    @staticmethod
    def make_private(path: Path, *, directory: bool = False) -> None:
        # Windows ACL semantics differ; configured-user ownership remains the portability baseline.
        with suppress(OSError):
            path.chmod(0o700 if directory else 0o600)

    @staticmethod
    def open_binary(path: Path) -> BinaryIO:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise AppError("INVALID_FILE", "Storage entry is not a regular file.", status_code=400)
            return os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise
