"""Commit an answer and its prepared memory in one storage transaction."""

from ..memory.prepared import PreparedMemory
from ..memory.service import MemoryRepository
from .chat_sessions import ChatSessionRepository, StoredSession


class ChatTurnWriter:
    def __init__(self, chats: ChatSessionRepository, memories: MemoryRepository | None):
        self._chats = chats
        self._memories = memories

    def finish(
        self, session_id: str, message_id: str, answer: str, *,
        prepared: PreparedMemory | None = None, refusal: bool = False,
        generated: bool = True,
        project_help: dict | None = None,
    ) -> StoredSession:
        commit_memory = None
        if prepared is not None and prepared.update is not None and self._memories is not None:
            def commit_memory(connection):
                self._memories.commit_update(connection, session_id, message_id,
                                             prepared.update, list(prepared.expected_rows))
        return self._chats.finish_turn(
            session_id, message_id, answer, refusal=refusal,
            on_commit=commit_memory, project_help=project_help, generated=generated,
        )
