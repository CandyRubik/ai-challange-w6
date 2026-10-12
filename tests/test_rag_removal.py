"""Retiring the second chat keeps one public chat product."""

import pytest
import sqlite3
from fastapi.testclient import TestClient

from app.main import app
from app.memory.service import SQLiteMemoryRepository
from app.storage.chat_sessions import SQLiteChatSessionRepository


@pytest.mark.parametrize("path", [
    "/rag-chat/", "/api/rag-chat/sessions", "/api/rag-chat/configurations",
])
def test_retired_chat_is_not_available(path):
    with TestClient(app) as client:
        assert client.get(path).status_code == 404


def test_retired_tables_are_removed_idempotently_without_losing_main_data(tmp_path):
    path = tmp_path / "chat.sqlite3"
    chat = SQLiteChatSessionRepository(path).create()
    memories = SQLiteMemoryRepository(path)
    memories.add(layer="long_term", category="fact", content="Сохранённое знание", session_id=None)
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE rag_chat_sessions (id TEXT PRIMARY KEY);
            CREATE TABLE rag_chat_state (session_id TEXT REFERENCES rag_chat_sessions(id));
            CREATE TABLE rag_chat_turns (session_id TEXT REFERENCES rag_chat_sessions(id));
            INSERT INTO rag_chat_sessions VALUES ('retired');
            INSERT INTO rag_chat_state VALUES ('retired');
            INSERT INTO rag_chat_turns VALUES ('retired');
        """)
    for _ in range(2):
        restored = SQLiteChatSessionRepository(path)
        assert restored.get(chat.id).id == chat.id
        assert SQLiteMemoryRepository(path).list_long_term()[0].content == "Сохранённое знание"
        with sqlite3.connect(path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not tables & {"rag_chat_sessions", "rag_chat_state", "rag_chat_turns"}
