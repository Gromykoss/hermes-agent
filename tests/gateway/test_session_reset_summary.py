"""Behavior contract for compaction-summary continuity across a session reset."""

from unittest.mock import patch

from agent.context_compressor import SUMMARY_PREFIX
from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource, SessionStore
from hermes_state import SessionDB


def _store(tmp_path):
    with patch("gateway.session.SessionStore._ensure_loaded"):
        store = SessionStore(sessions_dir=tmp_path / "sessions", config=GatewayConfig())
    store._db = SessionDB(db_path=tmp_path / "state.db")
    store._loaded = True
    return store


def _source():
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="reset-summary-chat",
        chat_type="dm",
        user_id="reset-summary-user",
    )


def test_session_reset_seeds_latest_parent_compaction_summary(tmp_path):
    store = _store(tmp_path)
    parent = store.get_or_create_session(_source())
    older = SUMMARY_PREFIX + "\n\nolder compacted context"
    latest = SUMMARY_PREFIX + "\n\nlatest compacted context"
    store._db.append_message(parent.session_id, "user", older, _compressed_summary=True)
    store._db.append_message(parent.session_id, "assistant", latest, _compressed_summary=True)

    child = store.reset_session(parent.session_key)

    messages = store._db.get_messages(child.session_id, include_inactive=True)
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"] == latest
    assert messages[0]["_compressed_summary"] is True
    assert messages[0]["content"].startswith("[CONTEXT COMPACTION — REFERENCE ONLY]")
    assert "Respond ONLY to the latest user message that appears AFTER this summary" in messages[0]["content"]
    store._db.close()


def test_session_reset_without_parent_summary_starts_empty(tmp_path):
    store = _store(tmp_path)
    parent = store.get_or_create_session(_source())

    child = store.reset_session(parent.session_key)

    assert store._db.get_messages(child.session_id, include_inactive=True) == []
    store._db.close()
