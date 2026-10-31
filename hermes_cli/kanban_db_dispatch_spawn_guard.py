"""Run-bound worker spawn reservation."""

from __future__ import annotations

import sqlite3
import time


def call_spawn_fn(spawn_fn, task, workspace: str, board: str | None) -> int | None:
    """Pass ``board`` when supported while retaining older spawn callback signatures."""
    import inspect

    try:
        signature = inspect.signature(spawn_fn)
        if "board" in signature.parameters:
            return spawn_fn(task, workspace, board=board)
        return spawn_fn(task, workspace)
    except (TypeError, ValueError):
        return spawn_fn(task, workspace)


def reserve_worker_spawn(conn: sqlite3.Connection, task) -> None:
    """Reserve exactly one spawn for the task's live run, or fail closed."""
    from hermes_cli import kanban_db as kb

    now = int(time.time())
    with kb.write_txn(conn):
        row = conn.execute(
            "SELECT status, current_run_id, claim_expires FROM tasks WHERE id = ?", (task.id,),
        ).fetchone()
        run_id = task.current_run_id
        already_started = run_id is not None and conn.execute(
            "SELECT 1 FROM task_events WHERE task_id = ? AND run_id = ? "
            "AND kind = 'spawn_started' LIMIT 1", (task.id, int(run_id)),
        ).fetchone()
        expires = kb._to_epoch(row["claim_expires"]) if row is not None else None
        if (row is None or row["status"] != "running" or run_id is None
                or row["current_run_id"] != int(run_id) or expires is None
                or expires <= now or already_started is not None):
            raise RuntimeError(
                f"spawn refused: task {task.id} does not own an unspent live run lease"
            )
        kb._append_event(
            conn, task.id, "spawn_started", {"run_id": int(run_id)}, run_id=int(run_id),
        )
