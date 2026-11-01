"""Completion evidence gates shared by every Kanban completion surface."""

from __future__ import annotations

import re
import sqlite3
from typing import Optional


class EmptyCompletionError(ValueError):
    """Completion was refused because it had no substantive result evidence."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"completion blocked: {task_id} has no result evidence")


class RealAdapterEvidenceError(ValueError):
    """Completion was refused because its contract requires adapter evidence."""

    def __init__(self, task_id: str):
        super().__init__(
            f"completion blocked: {task_id} requires result marker "
            "'real-adapter: <suite> <N passed>'"
        )


_REAL_ADAPTER_MARKER = re.compile(
    # Single-line marker only: every whitespace class excludes \n so the
    # match cannot span lines and glue a foreign "N passed" onto the suite.
    r"(?im)^[^\S\n]*real-adapter:[^\S\n]+"
    r"(?P<suite>\S+(?:[^\S\n]+\S+)*)"
    r"[^\S\n]+(?P<count>[1-9]\d*)[^\S\n]+passed\b[.!]?[^\S\n]*$"
)


def gate_completion_evidence(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    result: Optional[str],
    summary: Optional[str],
) -> None:
    """Refuse a completion without result evidence required by its contract."""
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_pr_acceptance import requires_real_adapter

    row = conn.execute(
        "SELECT status, completion_contract FROM tasks WHERE id = ?", (task_id,),
    ).fetchone()
    if row is None:
        return
    # Real-adapter evidence is required for EVERY done transition, including
    # a human approval from review (owner decision 2026-11-01, variant б):
    # review exempts only the empty-result rule, never the marker.
    if requires_real_adapter(row["completion_contract"]) and not (
        result is not None and _REAL_ADAPTER_MARKER.search(str(result))
    ):
        with kb.write_txn(conn):
            kb._append_event(conn, task_id, "completion_blocked_real_adapter", {
                "result_preview": kb._first_line(result, 200) or None,
            })
        raise RealAdapterEvidenceError(task_id)
    # A human approval is itself the result evidence. complete_task records the
    # durable _REVIEW_APPROVED_NOTE on both the run and completion event.
    if row["status"] == "review":
        return
    if not (result is not None and str(result).strip()):
        with kb.write_txn(conn):
            kb._append_event(conn, task_id, "completion_blocked_empty_result", {
                "result_preview": kb._first_line(result, 200) or None,
                "summary_preview": kb._first_line(summary, 200) or None,
            })
        raise EmptyCompletionError(task_id)
