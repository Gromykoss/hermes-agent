"""``complete_task`` refuses empty / whitespace-only evidence (#117483)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def conn(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()
    with kbc.connect() as c:
        yield c


def _ready_task(conn) -> str:
    tid = kb.create_task(conn, title="demo", assignee="coder")
    assert kb.claim_task(conn, tid, claimer=kb._claimer_id()) is not None
    return tid


def test_complete_without_evidence_raises(conn):
    tid = _ready_task(conn)
    with pytest.raises(kb.EmptyCompletionError):
        kb.complete_task(conn, tid)
    row = conn.execute("SELECT status, result FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "running"
    assert not row["result"]
    kinds = [r["kind"] for r in conn.execute(
        "SELECT kind FROM task_events WHERE task_id = ? ORDER BY id", (tid,)
    ).fetchall()]
    assert "completion_blocked_empty_result" in kinds
    assert "completed" not in kinds


def test_whitespace_result_raises(conn):
    tid = _ready_task(conn)
    with pytest.raises(kb.EmptyCompletionError):
        kb.complete_task(conn, tid, result="   ")
    assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()["status"] == "running"


def test_whitespace_stored_result_does_not_count(conn):
    tid = _ready_task(conn)
    conn.execute("UPDATE tasks SET result = ? WHERE id = ?", ("  ", tid))
    conn.commit()
    with pytest.raises(kb.EmptyCompletionError):
        kb.complete_task(conn, tid)
    assert kb.complete_task(conn, tid, result="shipped the gate") is True
    assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()["status"] == "done"


def test_review_approval_without_result_is_exempt(conn):
    # Spec-claimed exemption (P1-SPEC, verdict fix-round): a human approval
    # completing a card from ``review`` is itself the evidence. Completing
    # without a result succeeds and records the durable _REVIEW_APPROVED_NOTE
    # on the run/event; the empty-result gate stays strict for every other
    # source status (the fake-closure class this gate exists for).
    tid = kb.create_task(conn, title="review-me", assignee="coder")
    assert kb.claim_task(conn, tid, claimer=kb._claimer_id()) is not None
    assert kb.request_review(conn, tid, summary="please look") is True
    assert kb.get_task(conn, tid).status == "review"
    assert kb.complete_task(conn, tid) is True
    assert kb.get_task(conn, tid).status == "done"
    last = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (tid,),
    ).fetchone()
    assert kb._REVIEW_APPROVED_NOTE in (last["payload"] or "")


def test_review_approval_with_result_records_result(conn):
    tid = kb.create_task(conn, title="review-me-2", assignee="coder")
    assert kb.claim_task(conn, tid, claimer=kb._claimer_id()) is not None
    assert kb.request_review(conn, tid, summary="please look") is True
    assert kb.complete_task(conn, tid, result="review approved") is True
    assert kb.get_task(conn, tid).status == "done"
    assert kb.get_task(conn, tid).result == "review approved"


def test_reject_then_succeed(conn):
    tid = _ready_task(conn)
    with pytest.raises(kb.EmptyCompletionError):
        kb.complete_task(conn, tid, summary="   ")
    assert kb.complete_task(conn, tid, result="fixed empty complete") is True
    assert conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()["status"] == "done"
