from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
import pytest

from app.agents.agent import Agent, AgentOutputError
from app.main import app, get_chat_session_service, get_memory_service, get_goal_coordinator
from app.memory.service import MemoryService, SQLiteMemoryRepository
from app.memory.updates import MemoryUpdateConflict, WorkingMemoryInterpreter
from app.schemas import MemoryCreateRequest
from app.services.chat_sessions import ChatSessionService
from app.storage.chat_sessions import ChatTurnConflict, SQLiteChatSessionRepository
from app.state.task import TaskConflict


class JsonModel:
    def __init__(self, *updates: dict) -> None:
        self.updates = list(updates)
        self.calls: list[dict] = []

    def generate_json(self, *, messages, max_tokens=2000):
        self.calls.append(json.loads(messages[-1]["content"]))
        return json.dumps(self.updates.pop(0) if self.updates else {}, ensure_ascii=False)


class AnswerModel:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[list[dict]] = []

    def generate(self, *, messages, max_tokens=2000):
        self.calls.append(messages)
        answer = self.answers.pop(0) if self.answers else "Готово."
        if isinstance(answer, Exception):
            raise answer
        return answer


def create_services(tmp_path: Path, json_model=None, answers=None):
    database = tmp_path / "chat.sqlite3"
    sessions = SQLiteChatSessionRepository(database)
    memories = SQLiteMemoryRepository(database)
    interpreter = WorkingMemoryInterpreter(json_model or JsonModel())
    service = ChatSessionService(sessions, Agent(answers or AnswerModel()), memories,
                                memory_interpreter=interpreter)
    return service, memories, sessions


def constraint(value: str) -> dict:
    return {"changes": [{"group": "constraint", "action": "set", "key": "length", "value": value}]}


def test_new_goal_branches_without_copying_old_working_memory(tmp_path):
    service, memories, _ = create_services(tmp_path, JsonModel(
        {"goal_action": "set", "goal": "Написать письмо", **constraint("коротко")},
        {"goal_action": "set", "goal": "Подготовить тренировку"},
    ))
    original = service.create()
    service.send(original.id, "Моя цель — написать письмо. Пиши коротко.")
    memories.add(layer="long_term", category="preference", content="Русский язык", session_id=None)
    before = service.get(original.id)
    result = service.send(original.id, "Теперь моя цель — подготовить тренировку.")
    assert result.session.id != original.id
    assert result.session.parent_session_id == original.id
    assert service.get(original.id) == before
    assert {row.content for row in memories.list_working(original.id)} == {"Написать письмо", "length: коротко"}
    assert [row.content for row in memories.list_working(result.session.id)] == ["Подготовить тренировку"]
    assert len(service.list()) == 2
    assert memories.list_long_term()[0].content == "Русский язык"


def test_failed_branch_replays_the_same_operation_after_restart(tmp_path):
    decisions = JsonModel({"goal_action": "set", "goal": "Письмо"},
                          {"goal_action": "set", "goal": "Тренировка"})
    service, _, _ = create_services(tmp_path, decisions, AnswerModel("Письмо", AgentOutputError("temporary")))
    source = service.create()
    service.send(source.id, "Цель — письмо")
    with pytest.raises(AgentOutputError) as failed:
        service.send(source.id, "Новая цель — тренировка", operation_id="branch-1")
    target_id = failed.value.branch_session_id
    message_id = failed.value.branch_message_id
    restarted_decisions = JsonModel()
    restarted, _, _ = create_services(tmp_path, restarted_decisions, AnswerModel("Тренировка готова"))
    recovered = restarted.send(source.id, "Новая цель — тренировка", operation_id="branch-1")
    assert recovered.session.id == target_id and recovered.user_message.id == message_id
    assert recovered.assistant_message.content == "Тренировка готова"
    replayed = restarted.send(source.id, "Новая цель — тренировка", operation_id="branch-1")
    assert replayed == recovered and len(restarted.list()) == 2
    assert restarted_decisions.calls == []
    with pytest.raises(ChatTurnConflict, match="другого сообщения"):
        restarted.send(source.id, "Другая цель", operation_id="branch-1")


def test_new_goal_can_branch_from_an_active_task(tmp_path):
    service, _, _ = create_services(tmp_path, JsonModel({"goal_action": "set", "goal": "Тренировка"}))
    source = service.create()
    before = service.start_task(source.id, "Написать письмо")
    result = service.send(source.id, "Моя новая цель — тренировка")
    assert result.session.parent_session_id == source.id
    assert service.get(source.id) == before
    assert service.get(result.session.id).task is None


def test_manual_goal_api_uses_the_same_branching_and_operation_recovery(tmp_path):
    service, memories, sessions = create_services(tmp_path)
    app.dependency_overrides[get_chat_session_service] = lambda: service
    app.dependency_overrides[get_goal_coordinator] = lambda: lambda: service
    app.dependency_overrides[get_memory_service] = lambda: MemoryService(memories, sessions)
    try:
        with TestClient(app) as client:
            source = client.post("/api/chat/sessions", json={}).json()["id"]
            first = {"layer": "working", "category": "goal", "content": "Письмо", "session_id": source}
            assert client.post("/api/memory", json=first).json()["session_id"] == source
            second = {**first, "content": "Тренировка", "operation_id": "manual-branch"}
            response = client.post("/api/memory", json=second)
            assert response.status_code == 201
            target = response.json()["session_id"]
            assert target != source
            assert client.post("/api/memory", json=second).json() == response.json()
            operation = client.get(f"/api/chat/sessions/{source}/operations/manual-branch").json()
            assert operation["session"]["id"] == target
            assert operation["user_message"]["status"] == "done"
            assert client.get(f"/api/chat/sessions/{target}").json()["parent_session_id"] == source
            assert [entry["content"] for entry in client.get(f"/api/memory?session_id={source}").json()["working"]] == ["Письмо"]
    finally:
        app.dependency_overrides.clear()


def test_concurrent_replay_does_not_create_a_second_branch(tmp_path):
    initial, _, _ = create_services(tmp_path, JsonModel({"goal_action": "set", "goal": "Письмо"}))
    source = initial.create()
    initial.send(source.id, "Моя цель — письмо")
    entered, release = Event(), Event()

    class BlockingAnswer(AnswerModel):
        def generate(self, **kwargs):
            entered.set()
            assert release.wait(5)
            return super().generate(**kwargs)

    decisions = JsonModel({"goal_action": "set", "goal": "Тренировка"})
    answers = BlockingAnswer()
    service, _, _ = create_services(tmp_path, decisions, answers)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.send, source.id, "Новая цель — тренировка", operation_id="one-branch")
        try:
            assert entered.wait(5)
            with pytest.raises(TaskConflict, match="уже выполняется"):
                service.send(source.id, "Новая цель — тренировка", operation_id="one-branch")
        finally:
            release.set()
        completed = pending.result(timeout=5)
    assert service.send(source.id, "Новая цель — тренировка", operation_id="one-branch") == completed
    assert len(service.list()) == 2 and len(answers.calls) == len(decisions.calls) == 1


def test_explicit_task_with_a_new_goal_branches_and_can_replay(tmp_path):
    service, _, _ = create_services(tmp_path)
    source = service.create()
    service.send(source.id, "/goal Написать письмо")
    result = service.start_task(source.id, "Подготовить тренировку", operation_id="task-branch")
    assert result.id != source.id and result.parent_session_id == source.id
    assert result.task.task == "Подготовить тренировку"
    assert service.get(source.id).task is None
    assert service.start_task(source.id, "Подготовить тренировку", operation_id="task-branch").id == result.id
    assert len(service.list()) == 2


def test_failed_branch_http_returns_destination_and_retry_uses_it(tmp_path):
    service, memories, sessions = create_services(tmp_path, JsonModel(
        {"goal_action": "set", "goal": "Письмо"}, {"goal_action": "set", "goal": "Тренировка"},
    ), AnswerModel("Первый ответ", AgentOutputError("temporary"), "Повтор готов"))
    app.dependency_overrides[get_chat_session_service] = lambda: service
    try:
        with TestClient(app) as client:
            source = client.post("/api/chat/sessions", json={}).json()["id"]
            url = f"/api/chat/sessions/{source}"
            assert client.post(url + "/messages", json={"content": "Цель — письмо"}).status_code == 200
            failed = client.post(url + "/messages", json={"content": "Новая цель — тренировка", "operation_id": "failed-branch"})
            assert failed.status_code == 502
            destination = failed.json()["detail"]
            assert destination["session_id"] != source
            state = client.get(url + "/operations/failed-branch").json()
            assert state["session"]["id"] == destination["session_id"]
            retried = client.post(f"/api/chat/sessions/{destination['session_id']}/messages/{destination['message_id']}/retry")
            assert retried.status_code == 200 and retried.json()["assistant_message"]["content"] == "Повтор готов"
            assert len(service.list()) == 2
    finally:
        app.dependency_overrides.clear()


def test_goal_command_does_not_generate_or_extract_memory(tmp_path):
    class NoExtraction:
        def __init__(self):
            self.calls = []
        def extract(self, **kwargs):
            self.calls.append(kwargs)
            return []

    repository = SQLiteChatSessionRepository(tmp_path / "chat.sqlite3")
    memories = SQLiteMemoryRepository(tmp_path / "chat.sqlite3")
    answers = AnswerModel()
    extractor = NoExtraction()
    service = ChatSessionService(repository, Agent(answers), memories, extractor)
    source = service.create()
    reply = service.send(source.id, "/goal " + "Ц" * 4000)
    assert answers.calls == extractor.calls == []
    assert reply.assistant_message.model is None
    assert memories.list_working(source.id)[0].content == "Ц" * 4000


def test_equivalent_goal_keeps_constraints_and_original_goal_anchor(tmp_path):
    service, memories, _ = create_services(tmp_path)
    source = service.create()
    first = service.send(source.id, "/goal Написать письмо")
    memories.add(layer="working", category="constraint", content="Кратко", session_id=source.id)
    repeated = service.send(source.id, "/goal написать   письмо")
    assert repeated.session.id == source.id and len(service.list()) == 1
    rows = memories.list_working(source.id)
    assert {row.content for row in rows} == {"Написать письмо", "Кратко"}
    goal = next(row for row in rows if row.category == "goal")
    assert goal.source_message_id == first.user_message.id


def test_failed_answer_survives_restart_and_retry_interprets_only_once(tmp_path):
    decisions = JsonModel({"goal_action": "set", "goal": "Написать письмо", **constraint("коротко")})
    answers = AnswerModel(AgentOutputError("temporary"))
    service, memories, sessions = create_services(tmp_path, decisions, answers)
    chat = service.create()
    with pytest.raises(AgentOutputError):
        service.send(chat.id, "Моя цель — написать письмо. Отвечай коротко.")
    stored = service.get(chat.id)
    assert len(stored.messages) == 1
    assert stored.messages[0].status == "failed"
    assert memories.list_working(chat.id) == []
    with pytest.raises(ChatTurnConflict):
        service.send(chat.id, "Новый вопрос")

    restarted_model = JsonModel()
    restarted, memories, _ = create_services(tmp_path, restarted_model, AnswerModel("Письмо готово."))
    result = restarted.retry(chat.id, stored.messages[0].id)
    assert result.user_message.id == stored.messages[0].id
    assert result.user_message.status == "done"
    assert len(restarted.get(chat.id).messages) == 2
    assert restarted_model.calls == []
    assert len(decisions.calls) == 1
    facts = memories.list_working(chat.id)
    assert [fact.category for fact in facts] == ["goal", "constraint"]
    assert all(fact.source_message_id == result.user_message.id for fact in facts)
    assert all(fact.source_session_id == chat.id for fact in facts)
    with pytest.raises(ChatTurnConflict):
        restarted.retry(chat.id, result.user_message.id)


def test_correction_replaces_value_and_points_to_correcting_message(tmp_path):
    service, memories, _ = create_services(tmp_path, JsonModel(constraint("коротко"), constraint("подробно")))
    chat = service.create()
    first = service.send(chat.id, "Отвечай коротко")
    corrected = service.send(chat.id, "Нет, теперь подробно")
    facts = memories.list_working(chat.id)
    assert len(facts) == 1
    assert facts[0].content == "length: подробно"
    assert facts[0].source_message_id == corrected.user_message.id
    assert facts[0].source_message_id != first.user_message.id


def test_new_branch_has_fresh_context_and_shared_long_term_memory(tmp_path):
    answers = AnswerModel()
    service, memories, _ = create_services(tmp_path, JsonModel(
        {"goal_action": "set", "goal": "Старая цель", **constraint("коротко")},
        {"goal_action": "set", "goal": "Новая цель"}, {},
    ), answers)
    chat = service.create()
    memories.add(layer="long_term", category="preference", content="Русский язык", session_id=None)
    service.send(chat.id, "Старая цель: короткая статья")
    branch = service.send(chat.id, "Новая цель: подготовить интервью")
    service.send(branch.session.id, "Продолжай")
    assert [(fact.category, fact.content) for fact in memories.list_working(branch.session.id)] == [("goal", "Новая цель")]
    assert len(memories.list_long_term()) == 1
    for call in answers.calls[1:]:
        assert "Старая цель" not in json.dumps(call, ensure_ascii=False)


def test_ambiguous_removal_asks_clarification_without_generation_or_changes(tmp_path):
    answers = AnswerModel()
    service, memories, _ = create_services(tmp_path, JsonModel(
        constraint("коротко"), {"clarification": "Какое ограничение убрать?"},
    ), answers)
    chat = service.create()
    service.send(chat.id, "Отвечай коротко")
    before = memories.list_working(chat.id)
    reply = service.send(chat.id, "Убери это")
    assert reply.assistant_message.content == "Какое ограничение убрать?"
    assert memories.list_working(chat.id) == before
    assert len(answers.calls) == 1


def test_clear_goal_and_unambiguous_removal(tmp_path):
    service, memories, _ = create_services(tmp_path, JsonModel(
        {"goal_action": "set", "goal": "Письмо", **constraint("коротко")},
        {"changes": [{"group": "constraint", "action": "remove", "key": "length"}]},
        {"goal_action": "clear"},
    ))
    chat = service.create()
    service.send(chat.id, "Письмо, коротко")
    service.send(chat.id, "Убери ограничение длины")
    assert [fact.category for fact in memories.list_working(chat.id)] == ["goal"]
    service.send(chat.id, "Отменяю эту цель")
    assert memories.list_working(chat.id) == []


def test_memory_update_and_answer_commit_roll_back_together(tmp_path, monkeypatch):
    service, memories, _ = create_services(tmp_path, JsonModel(constraint("коротко")))
    chat = service.create()
    original = memories.commit_update
    def broken_commit(*args):
        original(*args)
        raise sqlite3.OperationalError("simulated storage failure")
    monkeypatch.setattr(memories, "commit_update", broken_commit)
    with pytest.raises(sqlite3.OperationalError):
        service.send(chat.id, "Отвечай коротко")
    assert memories.list_working(chat.id) == []
    assert len(service.get(chat.id).messages) == 1
    monkeypatch.setattr(memories, "commit_update", original)
    service.retry(chat.id, service.get(chat.id).messages[-1].id)
    assert len(memories.list_working(chat.id)) == 1


def test_task_memory_survives_history_window_and_isolated_from_new_chat(tmp_path):
    answers = AnswerModel()
    service, memories, sessions = create_services(tmp_path, JsonModel(
        {"goal_action": "set", "goal": "Подготовить интервью"}, {}, {},
    ), answers)
    chat = service.create()
    service.send(chat.id, "Моя цель — подготовить интервью")
    for index in range(45):
        sessions.append_exchange(chat.id, f"Уточнение {index}", "Принято")
    service.send(chat.id, "Какова цель?")
    assert "Подготовить интервью" in answers.calls[-1][0]["content"]
    other = service.create()
    service.send(other.id, "Какова цель?")
    assert memories.list_working(other.id) == []
    assert "Подготовить интервью" not in answers.calls[-1][0]["content"]


def test_api_recovery_and_source_links_for_manual_memory(tmp_path):
    service, memories, sessions = create_services(tmp_path, answers=AnswerModel(AgentOutputError("temporary"), "Готово"))
    app.dependency_overrides[get_chat_session_service] = lambda: service
    app.dependency_overrides[get_goal_coordinator] = lambda: lambda: service
    app.dependency_overrides[get_memory_service] = lambda: MemoryService(memories, sessions)
    try:
        client = TestClient(app)
        chat = client.post("/api/chat/sessions", json={}).json()
        url = f"/api/chat/sessions/{chat['id']}"
        assert client.post(url + "/messages", json={"content": "Привет"}).status_code == 502
        message = client.get(url).json()["messages"][-1]
        assert message["status"] == "failed"
        assert client.post(url + "/messages", json={"content": "Дальше"}).status_code == 409
        assert client.post(url + "/messages/other/retry").status_code == 409
        retry_url = url + f"/messages/{message['id']}/retry"
        assert client.post(retry_url).status_code == 200
        assert client.post(retry_url).status_code == 409
        command = client.post("/api/memory", json={
            "layer": "working", "category": "goal", "content": "Подготовить письмо",
            "session_id": chat["id"], "source_session_id": chat["id"], "source_text": "/goal Подготовить письмо",
        })
        assert command.status_code == 201
        source = command.json()
        assert source["source_session_id"] == chat["id"]
        assert any(message["id"] == source["source_message_id"] and message["kind"] == "command"
                   for message in client.get(url).json()["messages"])
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("update", [
    {"goal_action": "set"},
    {"clarification": "Что убрать?", "changes": constraint("коротко")["changes"]},
    {"changes": [{"group": "constraint", "action": "set", "key": "length"}]},
])
def test_invalid_memory_interpretation_does_not_break_answer_or_mutate_memory(tmp_path, update):
    service, memories, _ = create_services(tmp_path, JsonModel(update))
    chat = service.create()
    assert service.send(chat.id, "Вопрос").assistant_message.content == "Готово."
    assert memories.list_working(chat.id) == []


def test_regular_chat_does_not_invoke_document_retrieval(tmp_path, monkeypatch):
    def unexpected():
        raise AssertionError("Regular chat attempted document retrieval")
    monkeypatch.setattr("app.main.get_document_index", unexpected)
    service, _, _ = create_services(tmp_path)
    session = service.create()
    assert service.send(session.id, "Обычный вопрос").assistant_message.content == "Готово."


def test_memory_edit_during_generation_requires_retry_from_new_snapshot(tmp_path):
    service, memories, _ = create_services(tmp_path, JsonModel(constraint("коротко")))
    chat = service.create()
    class EditingModel(AnswerModel):
        def generate(self, **kwargs):
            memories.add(layer="working", category="decision", content="Ручное решение", session_id=chat.id)
            return "Готово."
    service._agent = Agent(EditingModel())
    with pytest.raises(MemoryUpdateConflict):
        service.send(chat.id, "Отвечай коротко")
    assert [fact.content for fact in memories.list_working(chat.id)] == ["Ручное решение"]
    assert service.get(chat.id).messages[-1].status == "failed"
    service._agent = Agent(AnswerModel())
    service.retry(chat.id, service.get(chat.id).messages[-1].id)
    assert [fact.content for fact in memories.list_working(chat.id)] == ["Ручное решение", "length: коротко"]


def test_answer_rejected_by_invariants_does_not_commit_memory(tmp_path):
    from app.invariants import SQLiteInvariantRepository, InvariantUpdateRequest
    service, memories, _ = create_services(tmp_path, JsonModel(constraint("коротко")), AnswerModel("Первое. Второе."))
    rules = SQLiteInvariantRepository(tmp_path / "chat.sqlite3")
    rules.update(InvariantUpdateRequest(revision=rules.get().revision, emoji_enabled=False,
        uppercase_enabled=False, sentence_limit_enabled=True, max_sentences=1))
    service._invariants = rules
    chat = service.create()
    result = service.send(chat.id, "Отвечай коротко")
    assert result.assistant_message.refusal
    assert result.user_message.status == "done"
    assert memories.list_working(chat.id) == []


def test_invalid_json_schema_is_retried_with_validation_feedback(tmp_path):
    model = JsonModel({"changes": [{"group": "goal", "action": "set", "key": "goal", "value": "Новая цель"}]},
                      {"goal_action": "set", "goal": "Новая цель"})
    service, memories, _ = create_services(tmp_path, model)
    chat = service.create()
    service.send(chat.id, "Меняю цель")
    assert len(model.calls) == 2
    assert "validation_feedback" in model.calls[1]
    assert memories.list_working(chat.id)[0].content == "Новая цель"


def test_active_goal_remains_in_prompt_with_many_recent_facts(tmp_path):
    answers = AnswerModel()
    service, memories, _ = create_services(tmp_path, JsonModel({}), answers)
    chat = service.create()
    memories.add(layer="working", category="goal", content="Устойчивая цель", session_id=chat.id)
    for index in range(65):
        memories.add(layer="working", category="constraint", content=f"Ограничение {index}", session_id=chat.id)
    service.send(chat.id, "Что делаем?")
    assert "Устойчивая цель" in answers.calls[-1][0]["content"]
    assert any(fact.category == "goal" for fact in memories.list_working(chat.id))
