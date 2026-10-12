"""A single chat workspace, with lossless migration from former user scopes."""

import json
import sqlite3

from fastapi.testclient import TestClient

from app.agents.agent import Agent
from app.main import app, get_chat_session_service, get_memory_service
from app.memory.service import MemoryService, SQLiteMemoryRepository
from app.services.chat_sessions import ChatSessionService
from app.state.task import TaskContext
from app.storage.chat_sessions import SQLiteChatSessionRepository


class Model:
    def __init__(self):
        self.calls = []
    def generate(self, *, messages, **kwargs):
        self.calls.append(messages)
        return "Готово"


def legacy_database(path):
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript("""
            CREATE TABLE user_profiles(id TEXT PRIMARY KEY, name TEXT, language TEXT, tone TEXT);
            INSERT INTO user_profiles VALUES('default','ARCHIVED_DEFAULT','en','formal');
            INSERT INTO user_profiles VALUES('other','ARCHIVED_OTHER','en','technical');
            CREATE TABLE chat_sessions (
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL DEFAULT 'default' REFERENCES user_profiles(id),
                title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'ollama');
            CREATE INDEX idx_chat_sessions_profile ON chat_sessions(profile_id, updated_at);
            INSERT INTO chat_sessions VALUES('one','default','First','2026-10-12','2026-10-12','ollama');
            INSERT INTO chat_sessions VALUES('two','other','Second','2026-10-12','2026-10-12','ollama');
            CREATE TABLE chat_messages (
                id TEXT PRIMARY KEY, session_id TEXT REFERENCES chat_sessions(id) ON DELETE CASCADE,
                position INTEGER, role TEXT, kind TEXT DEFAULT 'message', content TEXT,
                is_refusal INTEGER DEFAULT 0, created_at TEXT);
            INSERT INTO chat_messages VALUES('m1','one',0,'user','message','Previous message',0,'2026-10-12');
            CREATE TABLE chat_tasks(session_id TEXT PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
                context TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, progress_revision INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE long_term_memory (
                id TEXT PRIMARY KEY, profile_id TEXT NOT NULL DEFAULT 'default' REFERENCES user_profiles(id),
                category TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX idx_long_term_memory_profile ON long_term_memory(profile_id, created_at);
            INSERT INTO long_term_memory VALUES('l1','default','knowledge','SHARED_FIRST','2026-10-12','2026-10-12');
            INSERT INTO long_term_memory VALUES('l2','other','knowledge','SHARED_SECOND','2026-10-12','2026-10-12');
            CREATE TABLE working_memory (
                id TEXT PRIMARY KEY, session_id TEXT REFERENCES chat_sessions(id) ON DELETE CASCADE,
                category TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            INSERT INTO working_memory VALUES('w1','one','constraint','FIRST_ONLY','2026-10-12','2026-10-12');
            INSERT INTO working_memory VALUES('w2','two','constraint','SECOND_ONLY','2026-10-12','2026-10-12');
        """)
        c.execute("INSERT INTO chat_tasks VALUES('two',?,4,2)", (TaskContext(task="Previous task").to_json(),))


def test_legacy_migration_preserves_history_tasks_memory_and_archived_settings(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    repository = SQLiteChatSessionRepository(path)
    memory = SQLiteMemoryRepository(path)
    assert {s.id for s in repository.list()} == {"one", "two"}
    assert repository.get("one").messages[0].content == "Previous message"
    assert repository.get("two").task.context.task == "Previous task"
    assert repository.get("two").task.revision == 4
    assert {m.content for m in memory.list_long_term()} == {"SHARED_FIRST", "SHARED_SECOND"}
    assert [m.content for m in memory.list_working("one")] == ["FIRST_ONLY"]
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0] == 2
        assert c.execute("SELECT name FROM user_profiles WHERE id='other'").fetchone()[0] == "ARCHIVED_OTHER"
        assert set(c.execute("SELECT * FROM legacy_profile_scopes")) == {
            ("chat_sessions", "one", "default"), ("chat_sessions", "two", "other"),
            ("long_term_memory", "l1", "default"), ("long_term_memory", "l2", "other"),
        }
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert "profile_id" not in {row[1] for row in c.execute("PRAGMA table_info(chat_sessions)")}
    # Reopening is idempotent, and new writes no longer need former scope records.
    repository = SQLiteChatSessionRepository(path)
    memory = SQLiteMemoryRepository(path)
    assert repository.create().id
    memory.add(layer="long_term", category="knowledge", content="NEW_SHARED", session_id=None)
    assert len(memory.list_long_term()) == 3


def test_archived_personalization_is_not_injected_into_model(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    repository = SQLiteChatSessionRepository(path)
    memory = SQLiteMemoryRepository(path)
    model = Model()
    service = ChatSessionService(repository, Agent(model), memory_repository=memory)
    service.send("one", "Новый вопрос")
    prompt = model.calls[0][0]["content"]
    assert "USER_PROFILE" not in prompt and "ARCHIVED_" not in prompt
    assert "SHARED_FIRST" in prompt and "SHARED_SECOND" in prompt
    assert "FIRST_ONLY" in prompt and "SECOND_ONLY" not in prompt


def test_clean_database_has_no_user_profiles_and_chat_works_immediately(tmp_path):
    path = tmp_path / "chat.sqlite3"
    repository = SQLiteChatSessionRepository(path)
    model = Model()
    service = ChatSessionService(repository, Agent(model))
    session = service.create()
    answer = service.send(session.id, "Первое сообщение")
    assert answer.assistant_message.content == "Готово" and len(model.calls) == 1
    assert len(service.get(session.id).messages) == 2
    with sqlite3.connect(path) as c:
        assert "user_profiles" not in {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_api_has_no_profile_routes_or_payload_fields(tmp_path):
    path = tmp_path / "chat.sqlite3"
    repository = SQLiteChatSessionRepository(path)
    memory = SQLiteMemoryRepository(path)
    service = ChatSessionService(repository, Agent(Model()), memory_repository=memory)
    app.dependency_overrides[get_chat_session_service] = lambda: service
    app.dependency_overrides[get_memory_service] = lambda: MemoryService(memory, repository)
    try:
        with TestClient(app) as client:
            assert client.get("/api/profiles").status_code == 404
            assert client.post("/api/chat/sessions", json={"profile_id": "default"}).status_code == 422
            session = client.post("/api/chat/sessions").json()
            assert "profile_id" not in session
            assert client.post(f"/api/chat/sessions/{session['id']}/task", json={"task": "Задача"}).status_code == 201
            assert not any("/profiles" in path for path in client.get("/openapi.json").json()["paths"])
    finally:
        app.dependency_overrides.clear()


def test_long_term_memory_is_shared_and_survives_clearing_chats(tmp_path):
    path = tmp_path / "chat.sqlite3"
    repository = SQLiteChatSessionRepository(path)
    memory = SQLiteMemoryRepository(path)
    first, second = repository.create(), repository.create()
    memory.add(layer="long_term", category="knowledge", content="Shared", session_id=None)
    memory.add(layer="working", category="goal", content="First", session_id=first.id)
    service = MemoryService(memory, repository)
    assert service.snapshot(first.id).long_term == service.snapshot(second.id).long_term
    assert not service.snapshot(second.id).working
    repository.clear()
    assert len(memory.list_long_term()) == 1 and not memory.list_working(first.id)
