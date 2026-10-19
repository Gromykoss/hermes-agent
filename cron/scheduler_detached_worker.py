"""Cron: teardown of a worker that outlived its ``run_job``.

``ThreadPoolExecutor.shutdown(wait=False)`` after an inactivity timeout does not stop a
worker already inside ``run_conversation``. Finalizing its SessionDB from ``run_job``'s
``finally`` would close a handle the worker is still writing to — the checkpoint/WAL-unlink
overlap behind #102827. The worker's Future owns the teardown instead.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import os
import signal
import subprocess
import threading
from typing import Optional


_WORKER_TERMINATED_ERROR = (
    "Worker terminated by SIGTERM (gateway teardown/update/stop); "
    "side effects may be partial."
)


def install_execution_signal_handlers(execution_id: str) -> dict[int, object]:
    """Terminalize an owned execution before the process obeys TERM/INT.

    Signal handlers can only be installed by the main thread. External cron workers
    always run there; the guard also covers synchronous ``hermes cron run`` owners
    without changing gateway ticker threads' process-wide signal policy.
    """
    if threading.current_thread() is not threading.main_thread():
        return {}

    handled = (signal.SIGTERM, signal.SIGINT)
    previous = {signum: signal.getsignal(signum) for signum in handled}

    def _terminalize_and_reraise(signum, _frame) -> None:
        for handled_signum in handled:
            signal.signal(handled_signum, signal.SIG_DFL)
        try:
            from cron.executions import finish_execution

            finish_execution(
                execution_id,
                success=False,
                error=_WORKER_TERMINATED_ERROR,
            )
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Cron worker failed to terminalize execution %s on signal %s",
                execution_id,
                signum,
            )
        os.kill(os.getpid(), signum)

    for signum in handled:
        signal.signal(signum, _terminalize_and_reraise)
    return previous


def restore_execution_signal_handlers(previous: dict[int, object]) -> None:
    """Restore handlers after a normally completed main-thread execution."""
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _close_late_session_db_result(future: concurrent.futures.Future) -> None:
    """Done-callback: close a SessionDB whose constructor finished after run_job's init timeout
    (worker abandoned via ``shutdown(wait=False)``), else its .db/WAL/SHM handles leak to EMFILE.

    If the constructor later completes inside that abandoned worker, the Future's result — an open
    SessionDB holding .db / WAL / SHM file handles — would be orphaned and never closed, leaking
    descriptors until EMFILE (#72782). This callback retrieves and closes that eventual late result.
    """
    with contextlib.suppress(Exception):
        db = future.result()
        if db is not None:
            from hermes_state_registry import release_or_close
            release_or_close(db)


def defer_teardown_to_running_worker(
    future: Optional[concurrent.futures.Future], session_db, agent, job_id: str, job_name: str,
    cron_session_id: str, workdir: Optional[str] = None,
) -> bool:
    """Return True when the worker is still running and its Future will finalize the session
    and tear the agent down on completion; False when the caller must do it now."""
    if future is None or future.done():
        return False
    from cron.scheduler import _finalize_cron_session, _teardown_cron_agent

    def _finish(_future) -> None:
        try:
            if session_db:
                _finalize_cron_session(session_db, agent, job_id, job_name, cron_session_id,
                                       workdir=workdir)
        finally:
            _teardown_cron_agent(agent, job_id)

    # Runs inline if the worker finished between done() and here — still exactly once.
    future.add_done_callback(_finish)
    return True


def reap_terminal_worker_in_background(process: subprocess.Popen) -> None:
    """Keep the reap contract when the waiter returns before the worker exits.

    The ledger turning terminal lets ``_wait_for_external_cron_worker_body``
    return while the worker is still in final teardown. The gateway remains the
    worker's parent, so if nobody calls ``wait()`` afterwards the worker lingers
    as a zombie (STAT=Z) under the gateway until it is restarted (#114509). A
    short-lived daemon thread holds that single responsibility and ends with
    the process exit it waits for.
    """
    threading.Thread(
        target=process.wait,
        name=f"cron-worker-reap-{getattr(process, 'pid', '?')}",
        daemon=True,
    ).start()
