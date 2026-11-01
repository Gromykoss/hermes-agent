from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.kanban_db_dispatch_spawn_guard import SpawnLeaseRefused, reserve_worker_spawn


def test_second_spawn_reservation_for_live_claim_is_refused(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()

    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="one worker", assignee="coder")
        claimed = kb.claim_task(conn, task_id, claimer="worker-a", ttl_seconds=60)
        assert claimed is not None
        reserve_worker_spawn(conn, claimed)

        with pytest.raises(RuntimeError, match="unspent live run lease"):
            reserve_worker_spawn(conn, claimed)

        events = conn.execute(
            "SELECT kind FROM task_events WHERE task_id = ? AND kind = 'spawn_started'",
            (task_id,),
        ).fetchall()
        assert len(events) == 1


def test_dispatch_guard_refusal_preserves_live_claim_and_run(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()

    import hermes_cli.kanban_db_dispatch_spawn_guard as guard

    monkeypatch.setattr(kbd, "_profile_exists_fn", lambda: None)
    # Deterministic regardless of host memory state: a critical-pressure tick
    # defers spawning entirely and would mask the lease-refusal path.
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "unknown")
    monkeypatch.setattr(
        guard,
        "reserve_worker_spawn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SpawnLeaseRefused("live lease")),
    )
    spawned = []
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="one worker", assignee="coder")
        result = kbd.dispatch_once(
            conn, spawn_fn=lambda *_args, **_kwargs: spawned.append(True),
            reconcile_orphans=False,
        )
        task = kb.get_task(conn, task_id)
        run = kb.latest_run(conn, task_id)

        assert result.spawned == []
        assert spawned == []
        assert task is not None
        assert task.status == "running"
        assert task.claim_lock is not None
        assert run is not None
        assert run.ended_at is None
