from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.db.models import JobModel, JobStatus, ProjectModel
from app.db.session import build_engine, build_session_factory, initialize_database
from app.jobs.runner import LocalJobRunner

PROJECT_ID = "2ef83f41-3d17-4cf1-a09b-8ca04882dd0f"
RENDER_KIND = "RENDER_PREVIEW"


class _UnusedTranscription:
    def run(self, *_: object) -> dict[str, object]:
        raise AssertionError("render tests must not invoke transcription")


def _factory(tmp_path: Path):
    engine = build_engine(tmp_path / "render-jobs.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id=PROJECT_ID, name="Render jobs"))
    return factory


def _wait_for_status(factory: sessionmaker[Session], job_id: str, expected: JobStatus) -> JobModel:
    # The runner is deliberately asynchronous; polling its persisted state keeps
    # this portable and does not rely on Unix-only process primitives.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with factory() as session:
            job = session.get(JobModel, job_id)
            assert job is not None
            if job.status == expected:
                return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {expected}")


def test_registered_render_handler_persists_structured_progress_and_result(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    runner = LocalJobRunner(factory, _UnusedTranscription())  # type: ignore[arg-type]

    def render(
        _project_id: str,
        data: dict[str, object],
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, object]:
        assert data["render_request"] == "safe-schema-only"
        assert isinstance(data["_job_id"], str)
        assert cancelled() is False
        progress(0.27)
        progress(0.63)
        return {"artifact_id": "preview-artifact", "relative_path": "previews/preview.mp4"}

    runner.register_handler(RENDER_KIND, render)
    job, created = runner.create_job(PROJECT_ID, RENDER_KIND, {"render_request": "safe-schema-only"})
    assert created is True
    runner.submit(job.id)

    completed = _wait_for_status(factory, job.id, JobStatus.COMPLETED)
    assert completed.progress == 1.0
    assert completed.result_data == {"artifact_id": "preview-artifact", "relative_path": "previews/preview.mp4"}
    runner.shutdown()


def test_render_handler_failure_is_a_safe_failed_job(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    runner = LocalJobRunner(factory, _UnusedTranscription())  # type: ignore[arg-type]

    def fail(*_: object) -> dict[str, object]:
        raise AppError("FFMPEG_RENDER_FAILED", "Render failed; see technical details.", status_code=422)

    runner.register_handler(RENDER_KIND, fail)  # type: ignore[arg-type]
    job, _ = runner.create_job(PROJECT_ID, RENDER_KIND, {})
    runner.submit(job.id)

    failed = _wait_for_status(factory, job.id, JobStatus.FAILED)
    assert failed.error_code == "FFMPEG_RENDER_FAILED"
    assert failed.error_message == "Render failed; see technical details."
    assert failed.finished_at is not None
    runner.shutdown()


def test_render_handler_cooperates_with_cancellation(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    runner = LocalJobRunner(factory, _UnusedTranscription())  # type: ignore[arg-type]
    started = threading.Event()
    release = threading.Event()

    def cancellable(
        _project_id: str,
        _data: dict[str, object],
        _progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, object]:
        started.set()
        assert release.wait(timeout=2.0)
        if cancelled():
            raise AppError("JOB_CANCELLED", "cancelled", status_code=409)
        return {}

    runner.register_handler(RENDER_KIND, cancellable)
    job, _ = runner.create_job(PROJECT_ID, RENDER_KIND, {})
    runner.submit(job.id)
    assert started.wait(timeout=2.0)
    assert runner.cancel(job.id).cancellation_requested is True
    release.set()

    cancelled = _wait_for_status(factory, job.id, JobStatus.CANCELLED)
    assert cancelled.error_code is None
    assert cancelled.finished_at is not None
    runner.shutdown()
