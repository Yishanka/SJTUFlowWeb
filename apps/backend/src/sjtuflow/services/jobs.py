from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4


JobStatus = str  # "pending" | "running" | "succeeded" | "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    """A single background task tracked by :class:`JobManager`.

    Long-running media work (audio extraction, transcription) runs off the
    HTTP request thread so the browser never blocks. The frontend polls
    ``GET /api/jobs/{job_id}`` to follow progress.
    """

    id: str
    kind: str
    status: JobStatus = "pending"
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobHandle:
    """Passed to a job worker so it can publish progress updates."""

    def __init__(self, manager: "JobManager", job: Job) -> None:
        self._manager = manager
        self._job = job

    @property
    def id(self) -> str:
        return self._job.id

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        self._manager._update(self._job.id, progress=progress, message=message)


class JobManager:
    """Process-local registry of background jobs.

    Like :class:`LocalAppService`, this is intentionally single-process: the
    first web version of SJTUFlow is a local single-user app. Jobs live in
    memory and are lost on restart, which is acceptable for transcription work
    the user can simply re-run.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, worker: Callable[[JobHandle], Any]) -> dict[str, Any]:
        """Register a job and run ``worker`` on a daemon thread.

        ``worker`` receives a :class:`JobHandle` and returns the job result
        (any JSON-serializable value). Exceptions are captured into the job's
        ``error`` field instead of crashing the thread.
        """

        job = Job(id=uuid4().hex, kind=kind)
        with self._lock:
            self._jobs[job.id] = job

        def runner() -> None:
            self._update(job.id, status="running", progress=0.0)
            handle = JobHandle(self, job)
            try:
                result = worker(handle)
                self._update(job.id, status="succeeded", progress=1.0, result=result)
            except Exception as exc:  # noqa: BLE001 - surface to the job payload
                self._update(
                    job.id,
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    message="".join(traceback.format_exception_only(type(exc), exc)).strip(),
                )

        thread = threading.Thread(target=runner, name=f"job-{job.id}", daemon=True)
        thread.start()
        return self.get(job.id)

    def run_sync(self, kind: str, worker: Callable[[JobHandle], Any]) -> dict[str, Any]:
        """Run a job inline and wait for it. Useful for small/fast tasks and tests."""

        job = Job(id=uuid4().hex, kind=kind)
        with self._lock:
            self._jobs[job.id] = job
        self._update(job.id, status="running", progress=0.0)
        handle = JobHandle(self, job)
        try:
            result = worker(handle)
            self._update(job.id, status="succeeded", progress=1.0, result=result)
        except Exception as exc:  # noqa: BLE001 - surface to the job payload
            self._update(
                job.id,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                message="".join(traceback.format_exception_only(type(exc), exc)).strip(),
            )
        return self.get(job.id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Job not found: {job_id}")
            return job.to_payload()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [job.to_payload() for job in jobs]

    def _update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        progress: float | None = None,
        message: str | None = None,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = max(0.0, min(1.0, progress))
            if message is not None:
                job.message = message
            if result is not None:
                job.result = result
            if error is not None:
                job.error = error
            job.updated_at = _now()
