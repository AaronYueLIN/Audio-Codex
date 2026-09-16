from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, Lock
from typing import Callable

from .db import Database, db

executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio-codex")
_controls_lock = Lock()
_controls: dict[int, dict] = {}


class JobCancelled(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def recover_orphaned_jobs(database: Database = db) -> None:
    # Persisted queued/running jobs cannot survive a process restart. Keep them as history
    # instead of showing them forever as active.
    now = utc_now()
    database.execute(
        """UPDATE jobs
           SET status='interrupted', progress=CASE WHEN progress>0 THEN progress ELSE 0 END,
               message='Interrupted by previous Audio Codex shutdown', updated_at=?
           WHERE status IN ('queued','running','canceling')""",
        (now,),
    )


def create_job(kind: str, episode_id: int | None = None, database: Database = db) -> int:
    return database.execute(
        "INSERT INTO jobs(episode_id, kind, status, progress, message) VALUES(?, ?, 'queued', 0, 'Queued')",
        (episode_id, kind),
    )


def update_job(job_id: int, status: str | None = None, progress: float | None = None,
               message: str | None = None, database: Database = db) -> None:
    parts = ["updated_at=?"]
    args: list[object] = [utc_now()]
    if status is not None:
        parts.append("status=?")
        args.append(status)
    if progress is not None:
        parts.append("progress=?")
        args.append(float(max(0, min(1, progress))))
    if message is not None:
        parts.append("message=?")
        args.append(str(message)[:12000])
    args.append(job_id)
    database.execute(f"UPDATE jobs SET {', '.join(parts)} WHERE id=?", args)


def _control(job_id: int) -> dict | None:
    with _controls_lock:
        return _controls.get(job_id)


def is_cancel_requested(job_id: int) -> bool:
    control = _control(job_id)
    return bool(control and control["cancel"].is_set())


def raise_if_cancelled(job_id: int) -> None:
    if is_cancel_requested(job_id):
        raise JobCancelled("Cancelled by user")


def submit(job_id: int, fn: Callable[[], None], database: Database = db) -> None:
    cancel_event = Event()
    control = {"cancel": cancel_event, "future": None}
    with _controls_lock:
        _controls[job_id] = control

    def wrapped():
        if cancel_event.is_set():
            update_job(job_id, status="cancelled", message="Cancelled before start", database=database)
            return
        update_job(job_id, status="running", progress=0.01, message="Running", database=database)
        try:
            raise_if_cancelled(job_id)
            fn()
            raise_if_cancelled(job_id)
            update_job(job_id, status="done", progress=1, message="Complete", database=database)
        except JobCancelled:
            update_job(job_id, status="cancelled", message="Cancelled by user", database=database)
        except Exception as exc:
            update_job(job_id, status="failed", message=str(exc), database=database)
        finally:
            with _controls_lock:
                _controls.pop(job_id, None)

    future = executor.submit(wrapped)
    control["future"] = future


def cancel_job(job_id: int, database: Database = db) -> dict:
    row = database.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not row:
        raise KeyError(job_id)
    if row["status"] in {"done", "failed", "cancelled", "interrupted"}:
        return row
    control = _control(job_id)
    if control is None:
        update_job(job_id, status="interrupted", message="Job process is no longer attached", database=database)
        return database.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    control["cancel"].set()
    future: Future | None = control.get("future")
    if future is not None and future.cancel():
        update_job(job_id, status="cancelled", message="Cancelled before start", database=database)
    else:
        update_job(job_id, status="canceling", message="Cancel requested; stopping at next safe checkpoint", database=database)
    return database.one("SELECT * FROM jobs WHERE id=?", (job_id,))


def delete_job(job_id: int, database: Database = db) -> bool:
    row = database.one("SELECT status FROM jobs WHERE id=?", (job_id,))
    if not row:
        return False
    if row["status"] in {"queued", "running", "canceling"}:
        raise RuntimeError("Active jobs must be cancelled before deletion")
    database.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    return True


def clear_history(database: Database = db) -> int:
    row = database.one(
        "SELECT COUNT(*) AS n FROM jobs WHERE status NOT IN ('queued','running','canceling')"
    )
    n = int((row or {}).get("n") or 0)
    database.execute("DELETE FROM jobs WHERE status NOT IN ('queued','running','canceling')")
    return n


recover_orphaned_jobs(db)
