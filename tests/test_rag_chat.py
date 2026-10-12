"""User-visible behavior of the dedicated RAG chat."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.agents.agent import Agent
from app.indexing.rag import DocumentRag, RetrievalSettings
from app.indexing.store import SearchHit
from app.main import app, get_rag_chat_service
from app.rag_chat.models import RagTaskState
from app.rag_chat.service import RagChatService
from app.rag_chat.state import TurnInterpreter
from app.rag_chat.store import SQLiteRagChatRepository


def hit(text: str = "ChatSessionService coordinates chat sessions.") -> SearchHit:
    return SearchHit({
        "chunk_id": "structure-0001",
        "source": "README.md",
        "title": "Test project docs",
        "section": "Architecture → ChatSessionService",
        "line_start": 6,
        "line_end": 6,
        "text": text,
    }, 0.9)


class Index:
    def __init__(self) -> None:
        self.searches: list[str] = []

    def ensure_built(self) -> dict:
        return {"version": "test"}

    def search(self, query: str, strategy: str, k: int) -> list[SearchHit]:
        self.searches.append(query)
        return [hit()]


class Reranker:
    def score(self, question: str, hits: list[SearchHit]) -> list[float]:
        return [0.9 for _ in hits]


class InterpretationModel:
    def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
        return json.dumps({
            "kind": "question",
            "question_scope": "document",
            "search_question": "What does ChatSessionService do?",
            "goal_action": "keep",
            "goal": "",
            "changes": [],
            "clarification": "",
        })


class QueueInterpretationModel:
    def __init__(self, *decisions: dict) -> None:
        self.decisions = list(decisions)
        self.calls = 0

    def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
        self.calls += 1
        return json.dumps(self.decisions.pop(0))


def decision(
    *, kind: str = "question", scope: str = "document",
    goal_action: str = "keep", goal: str = "", changes: list[dict] | None = None,
    clarification: str = "",
) -> dict:
    return {
        "kind": kind, "question_scope": scope,
        "search_question": "What does ChatSessionService do?" if kind == "question" else "",
        "goal_action": goal_action, "goal": goal,
        "changes": changes or [], "clarification": clarification,
    }


class AnswerModel:
    def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
        return json.dumps({"status": "answered", "claims": [{
            "text": "ChatSessionService выполняет отправленные задачи.",
            "evidence": [{
                "chunk_id": "structure-0001",
                "quote": "ChatSessionService coordinates chat sessions.",
            }],
        }]})


def test_question_searches_and_persists_verified_source(tmp_path: Path) -> None:
    database = tmp_path / "rag.sqlite3"
    index = Index()
    rag = DocumentRag(
        index, InterpretationModel(), reranker=Reranker(),
        settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1),
    )
    chat = RagChatService(
        SQLiteRagChatRepository(database), TurnInterpreter(InterpretationModel()),
        rag, Agent(AnswerModel()),
    )
    session = chat.create()

    turn = chat.send(session.id, "Что делает ChatSessionService?")

    assert index.searches == ["What does ChatSessionService do?"]
    assert turn.status == "done"
    assert turn.grounding_status == "answered"
    assert turn.sources == [{
        "kind": "document", "chunk_id": "structure-0001",
        "source": "README.md", "title": "Test project docs",
        "section": "Architecture → ChatSessionService", "line_start": 6, "line_end": 6, "sha256": None,
    }]
    assert turn.citations == [{
        "chunk_id": "structure-0001", "quote": "ChatSessionService coordinates chat sessions.",
    }]
    assert SQLiteRagChatRepository(database).get(session.id).turns[0] == turn


def test_goal_survives_short_history_and_correction_replaces_constraint(tmp_path: Path) -> None:
    database = tmp_path / "rag.sqlite3"
    interpreter_model = QueueInterpretationModel(
        decision(kind="statement", goal_action="set", goal="Объяснить ChatSessionService новичку"),
        decision(kind="statement", changes=[{
            "group": "constraint", "action": "set", "key": "length", "value": "Кратко",
        }]),
        decision(kind="statement", changes=[{
            "group": "constraint", "action": "set", "key": "length", "value": "Подробно",
        }]),
        *[decision() for _ in range(10)],
        decision(scope="goal"),
    )
    index = Index()
    rag = DocumentRag(index, InterpretationModel(), reranker=Reranker(),
                      settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1))
    chat = RagChatService(
        SQLiteRagChatRepository(database), TurnInterpreter(interpreter_model),
        rag, Agent(AnswerModel()), history_turns=2,
    )
    session = chat.create()

    goal_turn = chat.send(session.id, "Моя цель — объяснить ChatSessionService новичку.")
    chat.send(session.id, "Пиши кратко.")
    correction = chat.send(session.id, "Нет, пиши подробно.")
    for _ in range(10):
        chat.send(session.id, "Что делает ChatSessionService?")
    reminder = chat.send(session.id, "Какую цель мы зафиксировали?")

    persisted = SQLiteRagChatRepository(database).get(session.id)
    assert persisted.state.goal.value == "Объяснить ChatSessionService новичку"
    assert persisted.state.goal.source_message_id == goal_turn.id
    assert [(item.key, item.value, item.source_message_id)
            for item in persisted.state.constraints] == [("length", "Подробно", correction.id)]
    assert reminder.answer == "Цель: Объяснить ChatSessionService новичку"
    assert reminder.sources == [{
        "kind": "message", "message_id": goal_turn.id,
        "title": "Сообщение пользователя · goal",
    }]
    assert len(index.searches) == 11  # ten document questions plus the memory question


def test_failed_answer_can_retry_without_reapplying_state(tmp_path: Path) -> None:
    class FailingOnceAnswerModel(AnswerModel):
        def __init__(self) -> None:
            self.calls = 0

        def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary provider error")
            return super().generate_json(messages=messages, max_tokens=max_tokens)

    interpreter_model = QueueInterpretationModel(decision(
        goal_action="set", goal="Объяснить ChatSessionService новичку",
    ))
    rag = DocumentRag(
        Index(), InterpretationModel(), reranker=Reranker(),
        settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1),
    )
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(interpreter_model), rag, Agent(FailingOnceAnswerModel()),
    )
    session = chat.create()

    failed = chat.send(session.id, "Хочу объяснить ChatSessionService новичку. Что это?")
    assert failed.status == "failed"
    assert chat.get(session.id).state.revision == 1
    assert len(chat.get(session.id).turns) == 1
    try:
        chat.send(session.id, "Следующий вопрос")
    except ValueError as error:
        assert "Повторите" in str(error)
    else:
        raise AssertionError("A new turn must not make failed retry inaccessible")

    completed = chat.retry(session.id, failed.id)
    assert completed.status == "done"
    assert completed.sources[0]["kind"] == "document"
    assert chat.get(session.id).state.revision == 1
    assert len(chat.get(session.id).turns) == 1
    assert interpreter_model.calls == 1


def test_new_chat_has_no_other_chats_goal_and_ambiguous_change_is_ignored(tmp_path: Path) -> None:
    interpreter_model = QueueInterpretationModel(
        decision(kind="statement", goal_action="set", goal="Изучить ChatSessionService"),
        decision(kind="clarification_needed", clarification="Что именно больше не нужно?"),
    )
    rag = DocumentRag(
        Index(), InterpretationModel(), reranker=Reranker(),
        settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1),
    )
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(interpreter_model), rag, Agent(AnswerModel()),
    )
    first = chat.create()
    chat.send(first.id, "Моя цель — изучить ChatSessionService.")
    second = chat.create()
    clarification = chat.send(first.id, "Это больше не нужно.")

    assert chat.get(second.id).state.goal is None
    assert chat.get(first.id).state.goal.value == "Изучить ChatSessionService"
    assert clarification.answer == "Что именно больше не нужно?"
    assert chat.get(first.id).state.revision == 1


def test_dedicated_rag_chat_screen_and_api(tmp_path: Path) -> None:
    rag = DocumentRag(
        Index(), InterpretationModel(), reranker=Reranker(),
        settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1),
    )
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(InterpretationModel()), rag, Agent(AnswerModel()),
    )
    app.dependency_overrides[get_rag_chat_service] = lambda: chat
    try:
        client = TestClient(app)
        page = client.get("/rag-chat/")
        assert page.status_code == 200
        assert "RAG-чат" in page.text

        created = client.post("/api/rag-chat/sessions").json()
        session_id = created["id"]
        turn = client.post(
            f"/api/rag-chat/sessions/{session_id}/turns",
            json={"content": "Что делает ChatSessionService?"},
        ).json()
        assert turn["sources"][0]["kind"] == "document"
        assert client.get(f"/api/rag-chat/sessions/{session_id}").json()["turns"] == [turn]
        assert len(client.get("/api/rag-chat/sessions").json()) == 1
    finally:
        app.dependency_overrides.clear()


def test_no_relevant_excerpt_abstains_and_still_displays_source_status(tmp_path: Path) -> None:
    class EmptyIndex(Index):
        def search(self, query: str, strategy: str, k: int) -> list[SearchHit]:
            self.searches.append(query)
            return []

    class NoAnswerModel:
        def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
            raise AssertionError("No answer should be generated without excerpts")

    index = EmptyIndex()
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(InterpretationModel()),
        DocumentRag(index, InterpretationModel(), reranker=Reranker(),
                    settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
        Agent(NoAnswerModel()),
    )
    session = chat.create()

    turn = chat.send(session.id, "Что делает ChatSessionService?")

    assert turn.status == "done"
    assert turn.grounding_status == "insufficient_context"
    assert turn.sources == turn.citations == []
    assert "Не знаю" in turn.answer
    assert index.searches == ["What does ChatSessionService do?"]


def test_explicit_new_goal_clears_previous_task_scope(tmp_path: Path) -> None:
    interpreter_model = QueueInterpretationModel(
        decision(kind="statement", goal_action="set", goal="Объяснить ChatSessionService",
                 changes=[{"group": "constraint", "action": "set",
                           "key": "audience", "value": "Новички"}]),
        decision(kind="statement", goal_action="set", goal="Изучить TurnInterpreter"),
    )
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(interpreter_model),
        DocumentRag(Index(), InterpretationModel(), reranker=Reranker(),
                    settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
        Agent(AnswerModel()),
    )
    session = chat.create()
    chat.send(session.id, "Хочу объяснить ChatSessionService новичкам.")
    changed = chat.send(session.id, "Теперь вместо этого изучаю TurnInterpreter.")

    assert chat.get(session.id).state.goal.value == "Изучить TurnInterpreter"
    assert chat.get(session.id).state.goal.source_message_id == changed.id
    assert chat.get(session.id).state.constraints == []


def test_statement_acknowledges_only_actual_state_change(tmp_path: Path) -> None:
    interpreter_model = QueueInterpretationModel(
        decision(kind="statement", goal_action="set", goal="Объяснить ChatSessionService"),
        decision(kind="statement"),
    )
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(interpreter_model),
        DocumentRag(Index(), InterpretationModel(), reranker=Reranker(),
                    settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
        Agent(AnswerModel()),
    )
    session = chat.create()
    chat.send(session.id, "Моя цель — объяснить ChatSessionService.")

    acknowledgement = chat.send(session.id, "Понял.")

    assert acknowledgement.answer == "Принято."


def test_comparison_uses_separate_retrieval_when_joint_terms_filter_out(tmp_path: Path) -> None:
    class SplitIndex(Index):
        def search(self, query: str, strategy: str, k: int) -> list[SearchHit]:
            self.searches.append(query)
            if query == "ProfileOnboarding MemoryRuntime comparison":
                return []
            if query in {"ProfileOnboarding", "MemoryRuntime"}:
                return [hit("ChatSessionService coordinates chat sessions. ProfileOnboarding MemoryRuntime")]
            return []

    class ComparisonModel(InterpretationModel):
        def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
            payload = decision()
            payload["search_question"] = "ProfileOnboarding MemoryRuntime comparison"
            return json.dumps(payload)

    index = SplitIndex()
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(ComparisonModel()),
        DocumentRag(index, InterpretationModel(), reranker=Reranker(),
                    settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
        Agent(AnswerModel()),
    )

    turn = chat.send(chat.create().id, "Сравни ProfileOnboarding и MemoryRuntime")

    assert turn.grounding_status == "answered"
    assert index.searches == ["ProfileOnboarding MemoryRuntime comparison",
                              "ProfileOnboarding", "MemoryRuntime"]


def test_mixed_question_identifies_unsupported_topic(tmp_path: Path) -> None:
    class MixedIndex(Index):
        def search(self, query: str, strategy: str, k: int) -> list[SearchHit]:
            self.searches.append(query)
            return [hit()] if query == "ChatSessionService" else []

    class MixedModel(InterpretationModel):
        def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
            payload = decision()
            payload["search_question"] = "ChatSessionService Tokio comparison"
            return json.dumps(payload)

    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(MixedModel()),
        DocumentRag(MixedIndex(), InterpretationModel(), reranker=Reranker(),
                    settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
        Agent(AnswerModel()),
    )

    turn = chat.send(chat.create().id, "Что документация говорит об ChatSessionService и Tokio?")

    assert turn.grounding_status == "answered"
    assert turn.sources[0]["kind"] == "document"
    assert "Tokio" in turn.answer
    assert "нет подтверждения" in turn.answer


def test_question_without_model_search_query_uses_user_question() -> None:
    payload = decision(scope="state")
    payload["search_question"] = ""
    interpreter = TurnInterpreter(QueueInterpretationModel(payload))

    result = interpreter.interpret("Какова моя цель?", RagTaskState(), [])

    assert result.kind == "question"
    assert result.question_scope == "state"
    assert result.search_question == "Какова моя цель?"


def test_cost_question_expands_search_after_unsupported_first_excerpts(tmp_path: Path) -> None:
    class CostIndex(Index):
        def search(self, query: str, strategy: str, k: int) -> list[SearchHit]:
            self.searches.append(query)
            if "resource management" in query:
                result = hit("Creating threads has resource overhead.")
                result.metadata["chunk_id"] = "structure-0002"
                return [result]
            return [hit("ChatSessionService creates a thread for each task.")]

    class CostInterpreter(InterpretationModel):
        def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
            payload = decision()
            payload["search_question"] = "What are costs in ChatSessionService?"
            return json.dumps(payload)

    class CostAnswerModel:
        def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
            if "structure-0002" not in messages[0]["content"]:
                return json.dumps({"status": "unknown", "claims": []})
            return json.dumps({"status": "answered", "claims": [{
                "text": "Создание потоков расходует ресурсы.",
                "evidence": [{"chunk_id": "structure-0002",
                              "quote": "Creating threads has resource overhead."}],
            }]})

    index = CostIndex()
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(CostInterpreter()),
        DocumentRag(index, InterpretationModel(), reranker=Reranker(),
                    settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
        Agent(CostAnswerModel()),
    )

    turn = chat.send(chat.create().id, "Каковы издержки ChatSessionService?")

    assert turn.grounding_status == "answered"
    assert turn.sources[0]["chunk_id"] == "structure-0002"
    assert len(index.searches) == 2
    assert "resource management" in index.searches[1]


def test_document_answer_uses_standalone_question_without_prior_assistant_text(
    tmp_path: Path,
) -> None:
    class RecordingAnswerModel(AnswerModel):
        def __init__(self) -> None:
            self.messages: list[dict] = []

        def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
            self.messages = messages
            return super().generate_json(messages=messages, max_tokens=max_tokens)

    model = RecordingAnswerModel()
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(QueueInterpretationModel(
            decision(kind="statement", goal_action="set", goal="Изучить ChatSessionService"),
            decision(),
        )),
        DocumentRag(Index(), InterpretationModel(), reranker=Reranker(),
                    settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
        Agent(model),
    )
    session = chat.create()
    chat.send(session.id, "Хочу изучить ChatSessionService.")

    answered = chat.send(session.id, "Как он работает?")

    assert answered.grounding_status == "answered"
    assert [message["role"] for message in model.messages] == ["system", "user"]
    assert json.loads(model.messages[1]["content"]) == {
        "user_question": "Как он работает?",
        "standalone_search_question": "What does ChatSessionService do?",
    }
    assert "Изучить ChatSessionService" in model.messages[0]["content"]


def test_invalid_quote_retry_receives_validation_feedback(tmp_path: Path) -> None:
    class RepairingAnswerModel:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def generate_json(self, *, messages: list[dict], max_tokens: int = 2000, schema: dict | None = None) -> str:
            self.calls.append(messages[0]["content"])
            quote = ("Invented excerpt without source support."
                     if len(self.calls) < 4 else "ChatSessionService coordinates chat sessions.")
            return json.dumps({"status": "answered", "claims": [{
                "text": "ChatSessionService выполняет задачи.",
                "evidence": [{"chunk_id": "structure-0001", "quote": quote}],
            }]})

    model = RepairingAnswerModel()
    chat = RagChatService(
        SQLiteRagChatRepository(tmp_path / "rag.sqlite3"),
        TurnInterpreter(InterpretationModel()),
        DocumentRag(Index(), InterpretationModel(), reranker=Reranker(),
                    settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
        Agent(model),
    )

    turn = chat.send(chat.create().id, "Что делает ChatSessionService?")

    assert turn.grounding_status == "answered"
    assert len(model.calls) == 4
    assert "Цитата отсутствует" in model.calls[1]


def test_retry_pins_local_generation_profile_across_restart(tmp_path: Path):
    from app.providers.errors import LlmRequestError
    from app.providers.registry import ModelRegistry
    from app.rag_chat.profiles import PROFILES

    class RecordingRegistry(ModelRegistry):
        def __init__(self, fail=False):
            super().__init__()
            self.fail = fail
            self.configurations = []
        def build(self, selection, **kwargs):
            self.configurations.append(kwargs)
            fail = self.fail
            class CombinedModel:
                def __init__(self):
                    self.answer_calls = 0
                def generate_json(self, **call):
                    if call["max_tokens"] == 700:
                        return InterpretationModel().generate_json(**call)
                    if fail:
                        self.answer_calls += 1
                        if self.answer_calls < 4:
                            return "{}"
                        raise LlmRequestError("temporary")
                    return AnswerModel().generate_json(**call)
            return CombinedModel()

    def service(registry, profile):
        return RagChatService(
            SQLiteRagChatRepository(tmp_path / "pinned.sqlite3"),
            TurnInterpreter(InterpretationModel()),
            DocumentRag(Index(), InterpretationModel(), reranker=Reranker(),
                        settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
            Agent(AnswerModel()), model_registry=registry, generation_profile=profile,
        )

    original = service(RecordingRegistry(fail=True), PROFILES["optimized"])
    session = original.create()
    failed = original.send(session.id, "Что делает ChatSessionService?")
    assert failed.status == "failed"
    assert failed.metrics["generation_profile"]["name"] == "optimized"
    assert failed.metrics["validation_errors"]

    registry = RecordingRegistry()
    restarted = service(registry, PROFILES["baseline"])
    result = restarted.retry(session.id, failed.id)
    assert result.status == "done"
    assert registry.configurations[-1]["num_ctx"] == 8192
    assert result.metrics["generation_profile"] == failed.metrics["generation_profile"]
    assert "validation_errors" not in result.metrics


def test_legacy_pending_turn_uses_baseline_after_optimized_profile_enabled(tmp_path: Path):
    from app.providers.registry import ModelRegistry
    from app.rag_chat.profiles import PROFILES

    class LegacyModel(InterpretationModel):
        def generate_json(self, **kwargs):
            if kwargs["max_tokens"] == 700:
                return super().generate_json(**kwargs)
            assert kwargs["max_tokens"] == 3000
            assert "You are a developer assistant" not in kwargs["messages"][0]["content"]
            return AnswerModel().generate_json(**kwargs)
    class Registry(ModelRegistry):
        def build(self, selection, **kwargs):
            assert "num_ctx" not in kwargs
            return LegacyModel()

    repository = SQLiteRagChatRepository(tmp_path / "legacy-profile.sqlite3")
    session = repository.create()
    pending = repository.start_turn(session.id, "Что делает ChatSessionService?", model="local")
    chat = RagChatService(repository, TurnInterpreter(InterpretationModel()),
                          DocumentRag(Index(), InterpretationModel(), reranker=Reranker(),
                                      settings=RetrievalSettings(rewrite=False, candidate_k=1, final_k=1)),
                          Agent(AnswerModel()), model_registry=Registry(), generation_profile=PROFILES["optimized"])
    assert chat.retry(session.id, pending.id).status == "done"
