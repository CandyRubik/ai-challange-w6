from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Literal, Protocol
from uuid import uuid4

from .updates import MemoryUpdateConflict, WorkingUpdate, apply_working_update
from .messages import StoredMessage
from .context import MemoryContext
from .goals import same_goal
from .prepared import PreparedMemory
from .extractor import MemoryExtractionError, MemoryExtractor
from .updates import WorkingMemoryInterpreter
from ..agents.agent import AgentMessage
import logging

logger = logging.getLogger(__name__)

from ..schemas import MemoryCreateRequest, MemoryEntry, MemorySnapshot
from ..storage.migrations import archive_profile_scope


MemoryLayer = Literal["working", "long_term"]
DEFAULT_MEMORY_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "chat.sqlite3"


class MemoryValidationError(ValueError):
    pass


class MemoryNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class StoredMemory:
    id: str
    layer: MemoryLayer
    category: str
    content: str
    session_id: str | None
    created_at: datetime
    updated_at: datetime
    memory_key: str | None = None
    source_session_id: str | None = None
    source_message_id: str | None = None


class MemoryRepository(Protocol):
    def add(
        self,
        *,
        layer: MemoryLayer,
        category: str,
        content: str,
        session_id: str | None,
        source_session_id: str | None = None,
        source_message_id: str | None = None,
    ) -> StoredMemory: ...

    def list_working(self, session_id: str) -> list[StoredMemory]: ...

    def list_long_term(self) -> list[StoredMemory]: ...

    def delete(self, layer: MemoryLayer, memory_id: str) -> bool: ...

    def working_rows(self, session_id: str) -> list[dict]: ...

    def commit_update(
        self, connection: sqlite3.Connection, session_id: str, message_id: str, update: WorkingUpdate,
        expected_rows: list[dict] | None = None,
    ) -> None: ...


class SessionRecord(Protocol):
    messages: tuple[StoredMessage, ...]


class SessionLookup(Protocol):
    def get(self, session_id: str) -> SessionRecord: ...

    def append_command(self, session_id: str, command_text: str) -> SessionRecord: ...


class SQLiteMemoryRepository:
    """Persist working and long-term memories in physically separate tables."""

    _tables: dict[MemoryLayer, str] = {
        "working": "working_memory",
        "long_term": "long_term_memory",
    }

    def __init__(self, database_path: str | Path = DEFAULT_MEMORY_DB_PATH) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS working_memory (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL
                        REFERENCES chat_sessions(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_working_memory_session
                ON working_memory(session_id, created_at);

                CREATE TABLE IF NOT EXISTS long_term_memory (
                    id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """,
            )
            archive_profile_scope(connection, "long_term_memory")
            for table in self._tables.values():
                present = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
                for column in ("memory_key", "source_session_id", "source_message_id"):
                    if column not in present:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _datetime(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def _stored(self, row: sqlite3.Row, layer: MemoryLayer) -> StoredMemory:
        return StoredMemory(
            id=row["id"],
            layer=layer,
            category=row["category"],
            content=row["content"],
            session_id=row["session_id"] if layer == "working" else None,
            created_at=self._datetime(row["created_at"]),
            updated_at=self._datetime(row["updated_at"]),
            memory_key=row["memory_key"],
            source_session_id=row["source_session_id"],
            source_message_id=row["source_message_id"],
        )

    def add(
        self,
        *,
        layer: MemoryLayer,
        category: str,
        content: str,
        session_id: str | None,
        source_session_id: str | None = None,
        source_message_id: str | None = None,
    ) -> StoredMemory:
        memory_id = str(uuid4())
        now = datetime.now(timezone.utc)
        timestamp = self._timestamp(now)
        table = self._tables[layer]
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if layer == "working":
                if category == "goal":
                    previous = connection.execute(
                        "SELECT * FROM working_memory WHERE session_id = ? AND category = 'goal'",
                        (session_id,),
                    ).fetchone()
                    if previous is not None and same_goal(previous["content"], content):
                        return self._stored(previous, layer)
                    if previous is not None:
                        connection.execute("DELETE FROM working_memory WHERE session_id = ?", (session_id,))
                    else:
                        connection.execute(
                            "DELETE FROM working_memory WHERE session_id = ? AND category = 'goal'", (session_id,),
                        )
                connection.execute(
                    f"""
                    INSERT INTO {table}
                        (id, session_id, category, content, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (memory_id, session_id, category, content, timestamp, timestamp),
                )
            else:
                connection.execute(
                    f"INSERT INTO {table} (id, category, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (memory_id, category, content, timestamp, timestamp),
                )
            connection.execute(
                f"UPDATE {table} SET memory_key = ?, source_session_id = ?, source_message_id = ? WHERE id = ?",
                ("goal" if category == "goal" else None, source_session_id, source_message_id, memory_id),
            )
        return StoredMemory(
            id=memory_id,
            layer=layer,
            category=category,
            content=content,
            session_id=session_id if layer == "working" else None,
            created_at=now,
            updated_at=now,
            memory_key="goal" if category == "goal" else None,
            source_session_id=source_session_id,
            source_message_id=source_message_id,
        )

    def working_rows(self, session_id: str) -> list[dict]:
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM working_memory WHERE session_id = ? ORDER BY created_at, id", (session_id,),
            )]

    def commit_update(
        self, connection: sqlite3.Connection, session_id: str, message_id: str, update: WorkingUpdate,
        expected_rows: list[dict] | None = None,
    ) -> None:
        # Runs in the same transaction as completion of the user's chat message.
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM working_memory WHERE session_id = ? ORDER BY created_at, id", (session_id,),
        )]
        if expected_rows is not None and rows != expected_rows:
            raise MemoryUpdateConflict("Рабочая память изменилась во время ответа. Повторите ответ.")
        updated = apply_working_update(rows, update, session_id, message_id)
        if updated == rows:
            return
        connection.execute("DELETE FROM working_memory WHERE session_id = ?", (session_id,))
        connection.executemany(
            """INSERT INTO working_memory
               (id, session_id, category, memory_key, content, source_session_id, source_message_id, created_at, updated_at)
               VALUES (:id, :session_id, :category, :memory_key, :content, :source_session_id, :source_message_id, :created_at, :updated_at)""",
            updated,
        )

    def _list(
        self,
        layer: MemoryLayer,
        scope_id: str | None = None,
    ) -> list[StoredMemory]:
        table = self._tables[layer]
        with self._connection() as connection:
            if layer == "working":
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE session_id = ? ORDER BY created_at, id",
                    (scope_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT * FROM {table}
                    ORDER BY created_at, id
                    """,
                ).fetchall()
        return [self._stored(row, layer) for row in rows]

    def list_working(self, session_id: str) -> list[StoredMemory]:
        return self._list("working", session_id)

    def list_long_term(self) -> list[StoredMemory]:
        return self._list("long_term")

    def delete(self, layer: MemoryLayer, memory_id: str) -> bool:
        table = self._tables[layer]
        with self._connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE id = ?",
                (memory_id,),
            )
        return cursor.rowcount > 0


class MemoryService:
    """Manual and automatic memory behind one application interface."""

    def __init__(
        self, repository: MemoryRepository | None, sessions: SessionLookup | None = None,
        *, extractor: MemoryExtractor | None = None, interpreter: WorkingMemoryInterpreter | None = None,
    ) -> None:
        self._repository = repository
        self._sessions = sessions
        self._extractor = extractor
        self._interpreter = interpreter

    @staticmethod
    def _entry(memory: StoredMemory) -> MemoryEntry:
        return MemoryEntry(
            id=memory.id, layer=memory.layer, category=memory.category, content=memory.content,
            session_id=memory.session_id, created_at=memory.created_at, updated_at=memory.updated_at,
            source_session_id=memory.source_session_id, source_message_id=memory.source_message_id,
        )

    def validate(self, request: MemoryCreateRequest) -> None:
        if self._repository is None or self._sessions is None:
            raise MemoryValidationError("Память не настроена")
        if request.layer == "working":
            if request.session_id is None:
                raise MemoryValidationError("Для рабочей памяти нужно явно указать session_id")
            self._sessions.get(request.session_id)
        elif request.session_id is not None:
            raise MemoryValidationError("Долговременная память не должна быть привязана к чат-сессии")
        if (request.source_session_id is None) != (request.source_text is None):
            raise MemoryValidationError("source_session_id и source_text нужно передавать вместе")
        if request.source_session_id is not None:
            self._sessions.get(request.source_session_id)
            if request.layer == "working" and request.source_session_id != request.session_id:
                raise MemoryValidationError("Рабочую команду можно сохранить только в текущем чате")
    def create(self, request: MemoryCreateRequest) -> MemoryEntry:
        self.validate(request)
        if request.layer == "working" and request.category == "goal":
            previous = self.goal(request.session_id)
            if previous and not same_goal(previous, request.content):
                raise MemoryValidationError("Новая цель требует отдельного чата")
        source_message_id = None
        if request.source_session_id is not None and request.source_text is not None:
            source_session = self._sessions.append_command(request.source_session_id, request.source_text)
            source_message_id = source_session.messages[-1].id
        stored = self._repository.add(
            layer=request.layer, category=request.category, content=request.content,
            session_id=request.session_id, source_session_id=request.source_session_id,
            source_message_id=source_message_id,
        )
        return self._entry(stored)

    def snapshot(self, session_id: str | None = None) -> MemorySnapshot:
        working = []
        if session_id is not None:
            self._sessions.get(session_id)
            working = self._repository.list_working(session_id)
        return MemorySnapshot(
            working=[self._entry(memory) for memory in working],
            long_term=[self._entry(memory) for memory in self._repository.list_long_term()],
        )

    def delete(self, layer: MemoryLayer, memory_id: str) -> None:
        if not self._repository.delete(layer, memory_id):
            raise MemoryNotFound(memory_id)


    def context(
        self,
        session_id: str,
    ) -> MemoryContext:
        if self._repository is None:
            return MemoryContext()
        return MemoryContext(
            tuple(
                {"category": memory.category, "content": memory.content}
                for memory in self._repository.list_working(session_id)
            ),
            tuple(
                {"category": memory.category, "content": memory.content}
                for memory in self._repository.list_long_term()
            ),
        )


    @staticmethod
    def _memory_key(
        layer: str,
        category: str,
        content: str,
    ) -> tuple[str, str, str]:
        normalized_content = " ".join(content.split()).casefold()
        return layer, category, normalized_content


    def remember(
        self,
        *,
        session_id: str,
        context: list[AgentMessage],
        content: str,
        memory: MemoryContext,
        source_message_id: str | None = None,
        working_enabled: bool | None = None,
    ) -> None:
        if self._repository is None or self._extractor is None:
            return

        if working_enabled is None:
            working_enabled = self._interpreter is None
        try:
            candidates = self._extractor.extract(
                context=context,
                current_message=content,
                working_memory=memory.working,
                long_term_memory=memory.long_term,
            )
            known = {
                self._memory_key(
                    "working",
                    entry["category"],
                    entry["content"],
                )
                for entry in memory.working
            }
            known.update(
                self._memory_key(
                    "long_term",
                    entry["category"],
                    entry["content"],
                )
                for entry in memory.long_term
            )
            for candidate in candidates:
                if candidate.layer == "working" and not working_enabled:
                    continue
                if candidate.layer == "working" and candidate.category == "goal" and any(
                    entry["category"] == "goal" and entry["content"] != candidate.content for entry in memory.working
                ):
                    continue
                key = self._memory_key(
                    candidate.layer,
                    candidate.category,
                    candidate.content,
                )
                if key in known:
                    continue
                self._repository.add(
                    layer=candidate.layer,
                    category=candidate.category,
                    content=candidate.content,
                    session_id=session_id if candidate.layer == "working" else None,
                    source_session_id=session_id,
                    source_message_id=source_message_id,
                )
                known.add(key)
        except MemoryExtractionError:
            logger.warning("Automatic memory extraction failed", exc_info=True)
        except Exception:
            logger.exception("Automatic memory persistence failed")


    def prepare(
        self, session_id: str, message_id: str, content: str, history: list[AgentMessage],
        *, saved_update: str | None = None, active_goal: str | None = None,
    ) -> PreparedMemory:
        memory = self.context(session_id)
        if self._repository is None or (self._interpreter is None and saved_update is None):
            return PreparedMemory(memory)
        rows = self._repository.working_rows(session_id)
        if saved_update is not None:
            update = WorkingUpdate.model_validate_json(saved_update)
        else:
            try:
                interpretation_rows = rows
                if active_goal and not any(row["category"] == "goal" for row in rows):
                    interpretation_rows = [*rows, {"id": "active-task-goal", "memory_key": "goal",
                                                  "category": "goal", "content": active_goal}]
                update = self._interpreter.interpret(content, interpretation_rows, history)
            except Exception:
                logger.warning("Working-memory interpretation failed; answering without changes", exc_info=True)
                update = WorkingUpdate()
        preview = apply_working_update(rows, update, session_id, message_id)
        goal = next((row for row in preview if row["category"] == "goal"), None)
        return PreparedMemory(
            MemoryContext(tuple({"category": row["category"], "content": row["content"]} for row in preview),
                          memory.long_term),
            update, tuple(rows), goal["source_message_id"] if goal else None,
        )

    def goal(self, session_id: str) -> str | None:
        if self._repository is None:
            return None
        return next((row.content for row in self._repository.list_working(session_id)
                     if row.category == "goal"), None)
