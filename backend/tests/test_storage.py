from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.projects.storage import ProjectStorage


def test_project_directories_are_isolated(tmp_path: Path) -> None:
    storage = ProjectStorage(tmp_path / "storage")
    first = storage.project_dir(uuid.uuid4(), create=True)
    second = storage.project_dir(uuid.uuid4(), create=True)

    assert first.is_dir()
    assert second.is_dir()
    assert first != second
    assert first.parent == second.parent == storage.projects_root


@pytest.mark.parametrize("value", ["../outside", "/absolute/path", "projects/../../outside"])
def test_safe_path_rejects_untrusted_paths(tmp_path: Path, value: str) -> None:
    storage = ProjectStorage(tmp_path / "storage")

    with pytest.raises(AppError) as error:
        storage.safe_path(value)

    assert error.value.code in {"INVALID_PATH", "PATH_TRAVERSAL"}


def test_safe_path_rejects_existing_symlink(tmp_path: Path) -> None:
    storage = ProjectStorage(tmp_path / "storage")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = storage.root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable on this platform or test environment")

    with pytest.raises(AppError, match="Symbolic links"):
        storage.safe_path("linked/escape.mp4")
