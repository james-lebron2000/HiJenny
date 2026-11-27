"""Workflow orchestrator (frozen contract, Lead-owned).

The pipeline is a deterministic state machine.  LLM/rule modules are plugged in
at fixed stages; every stage is idempotent and resumable so that a 1000-page
package can be re-run without duplicating evidence.

    INGEST -> EXTRACT -> VALIDATE -> CONSISTENCY -> GAP -> QUESTIONS -> REPORT

Stage handlers receive a SQLAlchemy ``Session`` and a project id and return a
stats dict.  They are imported lazily so that a missing optional dependency
never breaks the CLI/API import path.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Advisory file locking is available on POSIX deployments.
    fcntl = None

import threading
import traceback
from typing import Any, Callable

from hijenny import config, store
from hijenny.schemas import RunState, utcnow

STAGES: list[str] = [
    "INGEST",
    "EXTRACT",
    "VALIDATE",
    "CONSISTENCY",
    "GAP",
    "QUESTIONS",
    "REPORT",
]

_STAGE_STATE: dict[str, RunState] = {
    "INGEST": RunState.PARSING,
    "EXTRACT": RunState.EXTRACTING,
    "VALIDATE": RunState.VALIDATING,
    "CONSISTENCY": RunState.ANALYZING,
    "GAP": RunState.ANALYZING,
    "QUESTIONS": RunState.GENERATING_QUESTIONS,
    "REPORT": RunState.REPORT_READY,
}

_ACTIVE: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


class StageError(RuntimeError):
    def __init__(self, stage: str, cause: BaseException) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(f"stage {stage} failed: {cause}")


def _load_handler(stage: str) -> Callable[..., dict]:
    if stage == "INGEST":
        from hijenny.ingestion import ingest_project

        return ingest_project
    if stage == "EXTRACT":
        from hijenny.extraction import extract_project

        return extract_project
    if stage == "VALIDATE":
        from hijenny.validation import validate_project

        return validate_project
    if stage == "CONSISTENCY":
        from hijenny.agents.consistency import run as consistency_run

        return consistency_run
    if stage == "GAP":
        from hijenny.agents.gap import run as gap_run

        return gap_run
    if stage == "QUESTIONS":
        from hijenny.agents.questions import run as questions_run

        return questions_run
    if stage == "REPORT":
        from hijenny.reporting import generate_project_outputs

        return generate_project_outputs
    raise ValueError(f"unknown stage {stage}")


def _execute(run_id: str, project_id: str, force: bool = False) -> None:
    try:
        with store.session_scope() as session:
            store.update_run(session, run_id, state=RunState.CREATED, stats={"started_at": utcnow()})
            store.update_project(session, project_id, status="ANALYZING")

        for stage in STAGES:
            with store.session_scope() as session:
                store.update_run(
                    session,
                    run_id,
                    state=_STAGE_STATE[stage],
                    stats={f"stage_{stage.lower()}": {"state": "running"}},
                )
            try:
                handler = _load_handler(stage)
                with store.session_scope() as session:
                    stage_stats = handler(session, project_id, force=force) or {}
            except Exception as exc:  # noqa: BLE001 - re-raised as StageError
                raise StageError(stage, exc) from exc
            with store.session_scope() as session:
                store.update_run(
                    session,
                    run_id,
                    stats={f"stage_{stage.lower()}": {"state": "done", **stage_stats}},
                )

        with store.session_scope() as session:
            store.update_run(
                session,
                run_id,
                state=RunState.REPORT_READY,
                status="succeeded",
                finished=True,
                stats={"finished_at": utcnow()},
            )
            store.update_project(session, project_id, status="REPORT_READY")
    except StageError as exc:
        _fail(run_id, project_id, str(exc))
    except Exception as exc:  # noqa: BLE001 - surface any unexpected crash
        _fail(run_id, project_id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")


def _fail(run_id: str, project_id: str, message: str) -> None:
    try:
        with store.session_scope() as session:
            store.update_run(
                session, run_id, state=RunState.FAILED, status="failed", error=message, finished=True
            )
            store.update_project(session, project_id, status="FAILED")
    finally:
        with _LOCK:
            _ACTIVE.pop(run_id, None)


def _lock_directory() -> Path:
    return config.DB_PATH.parent / ("." + config.DB_PATH.name + ".run-locks")


@contextmanager
def _owned_run(project_id: str):
    lock = None
    try:
        with store.session_scope() as session:
            run = store.reserve_run(session, project_id, STAGES)
            if fcntl is not None:
                directory = _lock_directory()
                directory.mkdir(mode=0o700, exist_ok=True)
                lock = (directory / (run["run_id"] + ".lock")).open("xb")
                fcntl.flock(lock, fcntl.LOCK_EX)
        yield run
    finally:
        if lock is not None:
            try:
                with store.session_scope() as session:
                    row = store.get_run(session, run["run_id"])
                if row is None or row["status"] != "running":
                    Path(lock.name).unlink(missing_ok=True)
            finally:
                lock.close()


def recover_interrupted_runs() -> None:
    """An unlocked marker for a committed running job means its owner exited."""
    if fcntl is None:
        return
    for path in _lock_directory().glob("run_*.lock"):
        try:
            lock = path.open("rb")
        except FileNotFoundError:
            continue
        with lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            with store.session_scope() as session:
                row = store.get_run(session, path.stem)
                if not row:
                    continue  # The owning transaction may still be creating the run.
                if row["status"] == "running":
                    store.update_run(session, path.stem, state=RunState.FAILED, status="failed",
                                     error="分析进程已中断。可重新发起分析，已提交的阶段记录保留。", finished=True)
                    latest = store.list_runs(session, row["project_id"], limit=1)
                    if latest and latest[0]["run_id"] == path.stem:
                        store.update_project(session, row["project_id"], status="FAILED")
            path.unlink(missing_ok=True)


def run_pipeline(project_id: str, force: bool = False) -> dict:
    """Synchronous run; returns the final run row."""
    with _owned_run(project_id) as run:
        _execute(run["run_id"], project_id, force=force)
        with store.session_scope() as session:
            return store.get_run(session, run["run_id"]) or run


def start_pipeline(project_id: str, force: bool = False) -> str:
    """Async run in a daemon thread; returns run_id immediately."""
    owner = _owned_run(project_id)
    run = owner.__enter__()
    run_id = run["run_id"]

    def work():
        try:
            _execute(run_id, project_id, force=force)
        finally:
            owner.__exit__(None, None, None)
            with _LOCK:
                _ACTIVE.pop(run_id, None)

    thread = threading.Thread(target=work, name=f"hijenny-{run_id}", daemon=True)
    try:
        with _LOCK:
            _ACTIVE[run_id] = thread
            thread.start()
    except Exception as exc:
        try:
            _fail(run_id, project_id, str(exc))
        finally:
            owner.__exit__(None, None, None)
        raise
    return run_id


def wait_for_run(run_id: str, timeout: float = 600.0) -> dict | None:
    with _LOCK:
        thread = _ACTIVE.get(run_id)
    if thread is not None:
        thread.join(timeout)
    with store.session_scope() as session:
        return store.get_run(session, run_id)


def progress(run: dict) -> dict:
    """UI-friendly progress summary for a run row."""
    done = 0
    stats = run.get("stats") or {}
    for stage in STAGES:
        info = stats.get(f"stage_{stage.lower()}") or {}
        if isinstance(info, dict) and info.get("state") == "done":
            done += 1
    return {
        "run_id": run["run_id"],
        "state": run["state"],
        "status": run["status"],
        "stages_done": done,
        "stages_total": len(STAGES),
        "percent": round(100 * done / len(STAGES)),
        "error": run.get("error"),
    }
