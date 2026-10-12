from __future__ import annotations

import re
from uuid import uuid4
from typing import Callable

from ..agents.agent import Agent, AgentInputError, AgentMessage
from ..invariants import InvariantPolicy, InvariantViolation, SQLiteInvariantRepository
from ..memory.extractor import MemoryExtractor
from ..memory.service import MemoryService
from ..storage.turns import ChatTurnWriter
from ..memory.context import MemoryContext
from ..memory.prepared import PreparedMemory
from ..memory.updates import WorkingMemoryInterpreter, WorkingUpdate
from ..orchestration.context import OrchestrationContext
from .tasks import TaskService
from ..project_help.service import ProjectHelpService, help_question
from ..providers.registry import ModelRegistry, ModelSelection
from ..schemas import (
    ChatMessage, ChatSendResponse, ChatSession, ChatSessionSummary, ChatOperationStatus,
    TaskActionRequest, TaskSummary, TaskView, MemoryCreateRequest, MemoryEntry,
)
from ..state.task import TaskConflict, TaskState
from ..memory.service import MemoryRepository
from ..storage.chat_sessions import (
    ChatTurnConflict, ChatSessionRepository, StoredMessage, StoredSession,
)



class ChatSessionService:
    """Coordinate storage, orchestration and memory behind the chat API."""

    def __init__(
        self,
        repository: ChatSessionRepository,
        agent: Agent | None = None,
        memory_repository: MemoryRepository | None = None,
        memory_extractor: MemoryExtractor | None = None,
        *, invariant_repository: SQLiteInvariantRepository | None = None,
        memory_interpreter: WorkingMemoryInterpreter | None = None,
        model_registry: ModelRegistry | None = None,
        model_selection: ModelSelection | None = None,
        project_help: ProjectHelpService | Callable[[], ProjectHelpService] | None = None,
    ) -> None:
        if agent is None and model_registry is None:
            raise ValueError("Укажите агента или реестр моделей")
        self._repository = repository
        self._invariants = invariant_repository
        self._agent = agent
        self._models = model_registry
        self._selection = model_selection
        self._help = project_help
        self._memory = MemoryService(memory_repository, repository, extractor=memory_extractor, interpreter=memory_interpreter)
        self._turn_writer = ChatTurnWriter(repository, memory_repository)
        self._memory_repository = memory_repository
        self._tasks = TaskService(agent)

    def _runtime(self, provider: str, model: str | None = None, *, for_help: bool = False) -> "ChatSessionService":
        if self._models is None:
            return self
        selection = self._models.resolve(provider, model)
        selected = self._models.build(selection, **({"num_ctx": 8192, "max_num_ctx": 32768, "temperature": 0} if for_help else {}))
        return ChatSessionService(
            self._repository, Agent(selected, max_tokens=1000 if for_help else 2000), self._memory_repository,
            MemoryExtractor(selected),
            invariant_repository=self._invariants,
            memory_interpreter=WorkingMemoryInterpreter(self._models.build(selection, thinking_enabled=False)),
            model_selection=selection, project_help=self._help,
        )

    def _model_metadata(self) -> dict:
        return {} if self._selection is None else {
            "provider": self._selection.provider, "model": self._selection.model,
        }

    def set_provider(self, session_id: str, provider: str) -> ChatSession:
        if self._models is not None:
            self._models.resolve(provider)
        self._repository.set_provider(session_id, provider)
        return self.get(session_id)

    def _policy(self) -> InvariantPolicy:
        return InvariantPolicy(self._invariants.get() if self._invariants is not None else None)

    def _refuse(self, session_id: str, content: str, error: InvariantViolation) -> ChatSendResponse:
        return self._response(
            self._repository.append_refusal(session_id, content.strip(), error.refusal()),
        )

    @staticmethod
    def _summary(session: StoredSession) -> ChatSessionSummary:
        return ChatSessionSummary(
            id=session.id,
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
            provider=session.provider, parent_session_id=session.parent_session_id,
            task=None if session.task is None else TaskSummary(
                state=session.task.context.state,
                step=session.task.context.step,
                total=session.task.context.total,
                current=session.task.context.current,
                paused=session.task.context.paused,
                revision=session.task.revision,
            ),
        )

    @staticmethod
    def _message(message: StoredMessage) -> ChatMessage:
        return ChatMessage(
            id=message.id,
            role=message.role,
            kind=message.kind,
            refusal=message.refusal,
            content=message.content,
            created_at=message.created_at,
            status=message.status, error=message.error,
            provider=message.provider, model=message.model, project_help=message.project_help,
        )

    @classmethod
    def _response(cls, updated: StoredSession, message_id: str | None = None) -> ChatSendResponse:
        index = next((i for i, message in enumerate(updated.messages) if message.id == message_id),
                     len(updated.messages) - 2)
        return ChatSendResponse(
            session=cls._summary(updated),
            user_message=cls._message(updated.messages[index]),
            assistant_message=cls._message(updated.messages[index + 1]),
        )

    def create(self, provider: str | None = None) -> ChatSession:
        selected_provider = provider or (self._models.default_provider if self._models else "deepseek")
        if self._models is not None:
            self._models.resolve(selected_provider)
        session = self._repository.create(provider=selected_provider)
        return ChatSession(**self._summary(session).model_dump(), messages=[])

    def list(self) -> list[ChatSessionSummary]:
        return [self._summary(session) for session in self._repository.list()]

    def get(self, session_id: str) -> ChatSession:
        session = self._repository.get(session_id)
        return ChatSession(
            **self._summary(session).model_dump(exclude={"task"}),
            messages=[self._message(message) for message in session.messages],
            task=None if session.task is None else TaskView(
                **session.task.context.to_dict(), revision=session.task.revision,
            ),
        )

    def clear(self) -> None:
        self._repository.clear()

    @staticmethod
    def _history(session: StoredSession) -> list[AgentMessage]:
        return [{"role": message.role, "content": message.content} for message in session.messages
                if message.kind == "message" and not message.refusal and message.status == "done"
                and message.project_help is None]

    def send(
        self, session_id: str, content: str, provider: str | None = None, *, operation_id: str | None = None,
    ) -> ChatSendResponse:
        if not content.strip():
            raise AgentInputError("Сообщение не должно быть пустым")
        operation_id = operation_id or str(uuid4())
        with self._repository.operation_guard(operation_id):
            if operation_id is not None:
                existing = self._repository.operation(session_id, operation_id)
                if existing is not None:
                    target, turn = existing
                    if turn.content != content.strip():
                        raise ChatTurnConflict("Идентификатор операции уже использован для другого сообщения")
                    if turn.status == "done":
                        return self._response(target, turn.id)
                    return self.retry(target.id, turn.id)
            session = self._repository.get(session_id)
            policy = self._policy()
            try:
                return self._runtime(provider or session.provider, for_help=help_question(content) is not None)._send(
                    session_id, content.strip(), policy, operation_id,
                )
            except InvariantViolation as error:
                return self._refuse(session_id, content, error)

    def _send(self, session_id: str, content: str, policy: InvariantPolicy, operation_id: str) -> ChatSendResponse:
        session = self._repository.get(session_id)
        if session.messages and session.messages[-1].status != "done":
            raise ChatTurnConflict("Сначала повторите последний незавершённый ответ")
        policy.check_request(content)
        message_id = str(uuid4())
        question = help_question(content)
        prepared = None
        branch = False
        if question is not None:
            if callable(self._help):
                self._help = self._help()
            if self._help is None:
                raise AgentInputError("Ассистент проекта не настроен")
        else:
            explicit_goal = re.fullmatch(r"/goal\s+(.+)", content, flags=re.DOTALL)
            if content == "/goal" or (explicit_goal and len(explicit_goal[1].strip()) > 4000):
                raise AgentInputError("После /goal нужна цель длиной от 1 до 4000 символов")
            saved = WorkingUpdate(goal_action="set", goal=explicit_goal[1].strip()).model_dump_json() if explicit_goal else None
            current_goal = session.task.context.task if session.task else self._memory.goal(session_id)
            prepared = self._memory.prepare(session_id, message_id, content, self._history(session),
                                             saved_update=saved, active_goal=current_goal)
            update = prepared.update
            branch = bool(update and update.goal_action == "set" and current_goal
                          and " ".join(update.goal.split()).casefold() != " ".join(current_goal.split()).casefold())
            if session.task is not None and session.task.context.state != TaskState.DONE and not branch and not explicit_goal and not (update and update.clarification):
                ctx = session.task.context
                raise TaskConflict(f"Используйте действия задачи. Следующее действие: {ctx.current}",
                                   ctx=ctx, code="task_action_forbidden")
        def begin():
            return self._repository.begin_turn(
                session_id, content, operation_id=operation_id, message_id=message_id,
                branch=branch, memory_update=prepared.update.model_dump_json() if prepared and prepared.update else None,
                project_help={"question": question} if question is not None else None, **self._model_metadata(),
            )
        if branch:
            target, turn = begin()
            prepared = self._memory.prepare(target.id, turn.id, content, [], saved_update=turn.memory_update)
            lock_id = target.id
        else:
            target, turn = None, None
            lock_id = session_id
        with self._repository.task_operation(lock_id):
            if target is None:
                target, turn = begin()
            else:
                target = self._repository.get(target.id)
                turn = next(message for message in target.messages if message.id == turn.id)
            if turn.status == "done":
                return self._response(target, turn.id)
            try:
                return self._respond_turn(target, turn, policy, prepared=prepared)
            except Exception as error:
                if target.parent_session_id is not None:
                    error.branch_session_id = target.id
                    error.branch_message_id = turn.id
                raise

    def set_goal(self, request: MemoryCreateRequest) -> MemoryEntry:
        self._memory.validate(request)
        result = self.send(request.session_id, "/goal " + request.content, operation_id=request.operation_id)
        if result.user_message.refusal:
            from ..memory.service import MemoryValidationError
            raise MemoryValidationError(result.assistant_message.content)
        return next(entry for entry in self._memory.snapshot(result.session.id).working if entry.category == "goal")

    def operation_status(self, session_id: str, operation_id: str) -> ChatOperationStatus:
        self._repository.get(session_id)
        operation = self._repository.operation(session_id, operation_id)
        if operation is None:
            raise ChatTurnConflict("Операция ещё не сохранена")
        target, turn = operation
        return ChatOperationStatus(session=self._summary(target), user_message=self._message(turn))

    def retry(self, session_id: str, message_id: str) -> ChatSendResponse:
        with self._repository.task_operation(session_id):
            session = self._repository.get(session_id)
            if not session.messages or session.messages[-1].id != message_id or session.messages[-1].status == "done":
                raise ChatTurnConflict("Повторить можно только последнее сообщение без ответа")
            turn = session.messages[-1]
            return self._runtime(turn.provider or session.provider, turn.model, for_help=turn.project_help is not None)._respond_turn(
                session, turn, self._policy(),
            )

    def _respond_turn(
        self, session: StoredSession, turn: StoredMessage, policy: InvariantPolicy,
        *, prepared: PreparedMemory | None = None,
    ) -> ChatSendResponse:
        if turn.project_help is not None:
            try:
                policy.check_request(turn.content)
                result = self._help.answer(
                    turn.project_help["question"], self._agent,
                    OrchestrationContext(invariants=policy.settings),
                )
                updated = self._repository.finish_turn(
                    session.id, turn.id, policy.apply(result.answer), project_help=result.details,
                )
            except InvariantViolation as error:
                updated = self._repository.finish_turn(session.id, turn.id, error.refusal(), refusal=True)
            except Exception:
                self._repository.fail_turn(session.id, turn.id)
                raise
            return self._response(updated)
        context: list[AgentMessage] = [
            {"role": message.role, "content": message.content}
            for message in session.messages
            if message.kind == "message" and not message.refusal and message.status == "done" and message.project_help is None
        ]
        try:
            policy.check_request(turn.content)
            prepared = prepared or self._memory.prepare(session.id, turn.id, turn.content, context,
                                                         saved_update=turn.memory_update)
            decision = prepared.update
            memory = prepared.context
            if decision is not None and turn.memory_update is None:
                self._repository.save_memory_update(session.id, turn.id, decision.model_dump_json())
            if prepared.goal_source_message_id:
                anchor = next((i for i, message in enumerate(session.messages)
                               if message.id == prepared.goal_source_message_id), len(session.messages))
                context = [{"role": message.role, "content": message.content}
                           for message in session.messages[anchor:]
                           if message.kind == "message" and not message.refusal and message.status == "done" and message.project_help is None]
            answer = "Цель сохранена" if turn.content.startswith("/goal ") else decision.clarification if decision and decision.clarification else self._agent.respond(
                context, turn.content,
                orchestration=OrchestrationContext(invariants=policy.settings), memory=memory,
            )
            updated = self._turn_writer.finish(session.id, turn.id, policy.apply(answer), prepared=prepared,
                                               generated=not turn.content.startswith("/goal "))
        except InvariantViolation as error:
            updated = self._repository.finish_turn(session.id, turn.id, error.refusal(), refusal=True)
            return self._response(updated)
        except Exception:
            self._repository.fail_turn(session.id, turn.id)
            raise
        if not turn.content.startswith("/goal "):
            self._memory.remember(
                session_id=session.id,
                context=context,
                content=turn.content,
                memory=memory,
                source_message_id=turn.id,
            )
        return self._response(updated)


    def start_task(self, session_id: str, task: str, *, operation_id: str | None = None) -> ChatSession:
        policy = self._policy()
        try:
            return self._start_task(session_id, task, policy, operation_id=operation_id)
        except InvariantViolation as error:
            self._refuse(session_id, "Задача: " + task, error)
            return self.get(session_id)

    def _start_task(self, session_id: str, task: str, policy: InvariantPolicy, *, operation_id: str | None = None) -> ChatSession:
        policy.check_request(task)
        session = self._repository.get(session_id)
        current_goal = session.task.context.task if session.task else self._memory.goal(session_id)
        branch = bool(current_goal and " ".join(current_goal.split()).casefold() != " ".join(task.split()).casefold())
        change = self._tasks.start(task, OrchestrationContext(invariants=policy.settings))
        created = self._repository.create_task(session_id, change.context, assistant_content=change.answer,
                                                branch=branch, operation_id=operation_id or str(uuid4()))
        return self.get(created.id)

    def task_action(self, session_id: str, request: TaskActionRequest) -> ChatSession:
        policy = self._policy()
        try:
            if request.action in {"pause", "resume"}:
                return self._apply_task_action(session_id, request, policy)
            with self._repository.task_operation(session_id):
                return self._apply_task_action(session_id, request, policy)
        except InvariantViolation as error:
            self._refuse(session_id, request.content or "Продолжить задачу", error)
            return self.get(session_id)

    def _apply_task_action(
        self, session_id: str, request: TaskActionRequest, policy: InvariantPolicy,
    ) -> ChatSession:
        session = self._repository.get(session_id)
        stored = session.task
        if stored is None:
            raise TaskConflict("В чате нет задачи")
        action = self._tasks.resolve_action(stored, request.action, request.revision)
        generated = action in {"generate_plan", "execute_step", "validate"}
        runtime = self._runtime(session.provider) if generated else self
        memory = self._memory.context(session_id) if generated else MemoryContext()
        change = runtime._tasks.act(
            stored, action, revision=request.revision, content=request.content,
            orchestration=OrchestrationContext(invariants=policy.settings), memory=memory,
        )
        self._repository.update_task(
            session_id, change.context, change.user_content, change.answer,
            expected_revision=change.expected_revision,
            expected_progress_revision=change.expected_progress_revision,
            pause_only=change.pause_only,
            **(runtime._model_metadata() if change.generated else {}),
        )
        return self.get(session_id)
