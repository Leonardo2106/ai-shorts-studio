from __future__ import annotations

from pathlib import Path

from app.core.errors import AppError
from app.db.models import JobModel, JobStatus, ProjectModel
from app.db.session import build_engine, build_session_factory, initialize_database
from app.jobs.runner import LocalJobRunner


class SuccessfulTranscription:
    def run(self, *_: object) -> dict[str, object]:
        return {"transcript_id": "transcript", "cached": False}


class CancelledTranscription:
    def run(self, *_: object) -> dict[str, object]:
        raise AppError("JOB_CANCELLED", "cancelled", status_code=409)


def _factory(tmp_path: Path):
    engine = build_engine(tmp_path / "jobs.sqlite3")
    initialize_database(engine)
    factory = build_session_factory(engine)
    with factory.begin() as session:
        session.add(ProjectModel(id="2ef83f41-3d17-4cf1-a09b-8ca04882dd0f", name="Jobs"))
    return factory


def _job(factory, status: JobStatus = JobStatus.PENDING) -> str:
    with factory.begin() as session:
        job = JobModel(
            project_id="2ef83f41-3d17-4cf1-a09b-8ca04882dd0f",
            kind="TRANSCRIPTION",
            status=status,
            request_data={"media_id": "media", "audio_stream_index": 1},
        )
        session.add(job)
        session.flush()
        return job.id


def test_runner_completes_job_and_persists_result(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    job_id = _job(factory)
    runner = LocalJobRunner(factory, SuccessfulTranscription())  # type: ignore[arg-type]

    runner._execute_transcription(job_id)

    with factory() as session:
        job = session.get(JobModel, job_id)
        assert job is not None
        assert job.status == JobStatus.COMPLETED
        assert job.progress == 1.0
        assert job.result_data == {"transcript_id": "transcript", "cached": False}
    runner.shutdown()


def test_cancel_pending_job_is_terminal(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    job_id = _job(factory)
    runner = LocalJobRunner(factory, SuccessfulTranscription())  # type: ignore[arg-type]

    result = runner.cancel(job_id)

    assert result.status == JobStatus.CANCELLED
    assert result.cancellation_requested is True
    runner.shutdown()


def test_runner_recovers_interrupted_running_jobs(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    job_id = _job(factory, JobStatus.RUNNING)
    runner = LocalJobRunner(factory, SuccessfulTranscription())  # type: ignore[arg-type]

    runner.reconcile_interrupted()

    with factory() as session:
        job = session.get(JobModel, job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error_code == "JOB_INTERRUPTED"
        assert job.finished_at is not None
    runner.shutdown()


def test_runner_turns_cooperative_cancellation_into_cancelled(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    job_id = _job(factory)
    runner = LocalJobRunner(factory, CancelledTranscription())  # type: ignore[arg-type]

    runner._execute_transcription(job_id)

    with factory() as session:
        job = session.get(JobModel, job_id)
        assert job is not None
        assert job.status == JobStatus.CANCELLED
        assert job.error_code is None
    runner.shutdown()


def test_shutdown_marks_running_jobs_cancelled(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    job_id = _job(factory, JobStatus.RUNNING)
    runner = LocalJobRunner(
        factory,
        SuccessfulTranscription(),  # type: ignore[arg-type]
        shutdown_timeout_seconds=0.5,
    )

    runner.shutdown()

    with factory() as session:
        job = session.get(JobModel, job_id)
        assert job is not None
        assert job.status == JobStatus.CANCELLED
        assert job.cancellation_requested is True
