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
    with kbc.connect() as connection:
        yield connection


def _claimed(conn, *, contract=None):
    task_id = kb.create_task(
        conn, title="evidence", assignee="coder", completion_contract=contract,
    )
    task = kb.claim_task(conn, task_id, claimer="worker")
    assert task is not None
    return task_id, task.current_run_id


def test_worker_close_without_result_is_refused(conn):
    task_id, run_id = _claimed(conn)
    with pytest.raises(kb.EmptyCompletionError, match="no result evidence"):
        kb.complete_task(conn, task_id, summary="summary is not evidence", expected_run_id=run_id)


def test_real_adapter_contract_rejects_result_without_marker(conn):
    task_id, run_id = _claimed(conn, contract="require_real_adapter: true")
    with pytest.raises(kb.RealAdapterEvidenceError, match="real-adapter"):
        kb.complete_task(conn, task_id, result="ordinary unit tests passed", expected_run_id=run_id)


def test_real_adapter_contract_accepts_marker(conn):
    task_id, run_id = _claimed(conn, contract="require_real_adapter: true")
    assert kb.complete_task(
        conn, task_id, result="real-adapter: gateway-e2e 12 passed", expected_run_id=run_id,
    )
    assert kb.get_task(conn, task_id).status == "done"


@pytest.mark.parametrize("suffix", [".", "!"])
def test_real_adapter_marker_accepts_indentation_words_and_punctuation(conn, suffix):
    task_id, run_id = _claimed(conn, contract="require_real_adapter: true")
    assert kb.complete_task(
        conn, task_id,
        result=f"  real-adapter: gateway end-to-end 12 passed{suffix}",
        expected_run_id=run_id,
    )


def test_real_adapter_marker_rejects_zero_passed(conn):
    task_id, run_id = _claimed(conn, contract="require_real_adapter: true")
    with pytest.raises(kb.RealAdapterEvidenceError):
        kb.complete_task(
            conn, task_id, result="real-adapter: gateway-e2e 0 passed",
            expected_run_id=run_id,
        )
