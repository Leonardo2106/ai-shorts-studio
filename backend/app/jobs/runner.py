from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.db.models import JobModel, JobStatus
from app.transcription.schemas import TranscriptionRequest
from app.transcription.service import TranscriptionService


def utcnow() -> datetime:
    return datetime.now(UTC)


class LocalJobRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        transcription: TranscriptionService,
        *,
        workers: int = 1,
        max_active_jobs: int = 4,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        self.session_factory = session_factory
        self.transcription = transcription
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ai-shorts-job")
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.Lock()
        self.max_active_jobs = max_active_jobs
        self.shutdown_timeout_seconds = shutdown_timeout_seconds

    def create_transcription_job(self, project_id: str, request_data: dict[str, object]) -> tuple[JobModel, bool]:
        with self._lock, self.session_factory.begin() as session:
            active = session.scalars(
                select(JobModel).where(JobModel.status.in_([JobStatus.PENDING, JobStatus.RUNNING]))
            ).all()
            for job in active:
                if job.project_id == project_id and job.kind == "TRANSCRIPTION" and job.request_data == request_data:
                    return job, False
            if len(active) >= self.max_active_jobs:
                raise AppError(
                    "JOB_QUEUE_FULL",
                    "The local job queue has reached its configured limit.",
                    status_code=429,
                    details={"max_active_jobs": self.max_active_jobs},
                )
            job = JobModel(
                project_id=project_id,
                kind="TRANSCRIPTION",
                status=JobStatus.PENDING,
                request_data=request_data,
            )
            session.add(job)
            session.flush()
            job_id = job.id
        with self.session_factory() as session:
            created = session.get(JobModel, job_id)
            assert created is not None
            return created, True

    def reconcile_interrupted(self) -> None:
        with self.session_factory.begin() as session:
            session.execute(
                update(JobModel)
                .where(JobModel.status.in_([JobStatus.PENDING, JobStatus.RUNNING]))
                .values(
                    status=JobStatus.FAILED,
                    error_code="JOB_INTERRUPTED",
                    error_message="The application stopped while this job was running.",
                    finished_at=utcnow(),
                )
            )

    def submit_transcription(self, job_id: str) -> None:
        future = self.executor.submit(self._execute_transcription, job_id)
        with self._lock:
            self._futures[job_id] = future
        future.add_done_callback(lambda _future: self._forget(job_id))

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)

    def _is_cancelled(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job = session.get(JobModel, job_id)
            return job is None or job.cancellation_requested or job.status == JobStatus.CANCELLED

    def _progress(self, job_id: str, value: float) -> None:
        with self.session_factory.begin() as session:
            job = session.get(JobModel, job_id)
            if job is not None and job.status == JobStatus.RUNNING:
                job.progress = max(job.progress, min(1.0, max(0.0, value)))

    def _execute_transcription(self, job_id: str) -> None:
        with self.session_factory.begin() as session:
            job = session.get(JobModel, job_id)
            if job is None or job.status != JobStatus.PENDING:
                return
            if job.cancellation_requested:
                job.status = JobStatus.CANCELLED
                job.finished_at = utcnow()
                return
            job.status = JobStatus.RUNNING
            job.started_at = utcnow()
            project_id = job.project_id
            request_data = dict(job.request_data)
        try:
            result = self.transcription.run(
                project_id,
                TranscriptionRequest.model_validate(request_data),
                lambda value: self._progress(job_id, value),
                lambda: self._is_cancelled(job_id),
            )
        except AppError as exc:
            with self.session_factory.begin() as session:
                job = session.get(JobModel, job_id)
                if job is None:
                    return
                cancelled = exc.code == "JOB_CANCELLED" or job.cancellation_requested
                job.status = JobStatus.CANCELLED if cancelled else JobStatus.FAILED
                job.error_code = None if cancelled else exc.code
                job.error_message = None if cancelled else exc.message
                job.finished_at = utcnow()
            return
        except Exception:
            with self.session_factory.begin() as session:
                job = session.get(JobModel, job_id)
                if job is not None:
                    job.status = JobStatus.FAILED
                    job.error_code = "INTERNAL_JOB_ERROR"
                    job.error_message = "The background job failed unexpectedly."
                    job.finished_at = utcnow()
            return
        with self.session_factory.begin() as session:
            job = session.get(JobModel, job_id)
            if job is not None:
                if job.cancellation_requested:
                    job.status = JobStatus.CANCELLED
                else:
                    job.status = JobStatus.COMPLETED
                    job.progress = 1.0
                    job.result_data = result
                job.finished_at = utcnow()

    def cancel(self, job_id: str) -> JobModel:
        with self.session_factory.begin() as session:
            job = session.get(JobModel, job_id)
            if job is None:
                raise AppError("JOB_NOT_FOUND", "Job was not found.", status_code=404)
            if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
                raise AppError("JOB_ALREADY_FINISHED", "A finished job cannot be cancelled.", status_code=409)
            job.cancellation_requested = True
            if job.status == JobStatus.PENDING:
                job.status = JobStatus.CANCELLED
                job.finished_at = utcnow()
                with self._lock:
                    future = self._futures.get(job_id)
                if future is not None:
                    future.cancel()
        with self.session_factory() as session:
            result = session.get(JobModel, job_id)
            assert result is not None
            return result

    def shutdown(self) -> None:
        with self.session_factory.begin() as session:
            session.execute(
                update(JobModel)
                .where(JobModel.status.in_([JobStatus.PENDING, JobStatus.RUNNING]))
                .values(cancellation_requested=True)
            )
        with self._lock:
            futures = list(self._futures.values())
        for future in futures:
            future.cancel()
        deadline = time.monotonic() + self.shutdown_timeout_seconds
        while any(not future.done() for future in futures) and time.monotonic() < deadline:
            time.sleep(0.05)
        with self.session_factory.begin() as session:
            session.execute(
                update(JobModel)
                .where(JobModel.status.in_([JobStatus.PENDING, JobStatus.RUNNING]))
                .values(
                    status=JobStatus.CANCELLED,
                    cancellation_requested=True,
                    error_code=None,
                    error_message=None,
                    finished_at=utcnow(),
                )
            )
        self.executor.shutdown(wait=False, cancel_futures=True)
