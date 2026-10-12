from __future__ import annotations

import logging

from ..agents.agent import Agent, AgentInputError, AgentMessage
from ..invariants import InvariantPolicy, InvariantViolation, SQLiteInvariantRepository
from ..memory.extractor import MemoryExtractor
from ..memory.runtime import MemoryRuntime
from ..memory.context import MemoryContext
from ..memory.updates import WorkingMemoryInterpreter, WorkingUpdate, apply_working_update
from ..orchestration.context import OrchestrationContext
from ..orchestration.tasks import TaskOrchestrator
from ..project_help.service import ProjectHelpService, help_question
from ..providers.registry import ModelRegistry, ModelSelection
from ..schemas import (
    ChatMessage, ChatSendResponse, ChatSession, ChatSessionSummary,
    TaskActionRequest, TaskSummary, TaskView,
)
from ..state.task import TaskContext, TaskConflict, TaskState, approve_plan, pause, replan, require_action, resume
from ..memory.service import MemoryRepository
from ..storage.chat_sessions import (
    ChatSessionNotFound, ChatTurnConflict, ChatSessionRepository, DEFAULT_CHAT_DB_PATH,
    DEFAULT_DB_PATH, SQLiteChatSessionRepository, StoredMessage, StoredSession, StoredTask,
)


logger = logging.getLogger(__name__)


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
        project_help: ProjectHelpService | None = None,
    ) -> None:
        if agent is None and model_registry is None:
            raise ValueError("Укажите агента или реестр моделей")
        self._repository = repository
        self._invariants = invariant_repository
        self._agent = agent
        self._models = model_registry
        self._selection = model_selection
        self._help = project_help
        self._memory = MemoryRuntime(memory_repository, memory_extractor)
        self._memory_repository = memory_repository
        self._memory_interpreter = memory_interpreter
        self._tasks = TaskOrchestrator(agent) if agent is not None else None

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
            provider=session.provider,
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
    def _response(cls, updated: StoredSession) -> ChatSendResponse:
        return ChatSendResponse(
            session=cls._summary(updated),
            user_message=cls._message(updated.messages[-2]),
            assistant_message=cls._message(updated.messages[-1]),
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

    def send(self, session_id: str, content: str, provider: str | None = None) -> ChatSendResponse:
        if not content.strip():
            raise AgentInputError("Сообщение не должно быть пустым")
        policy = self._policy()
        with self._repository.task_operation(session_id):
            try:
                session = self._repository.get(session_id)
                return self._runtime(provider or session.provider, for_help=help_question(content) is not None)._send(session_id, content, policy)
            except InvariantViolation as error:
                return self._refuse(session_id, content, error)

    def _send(self, session_id: str, content: str, policy: InvariantPolicy) -> ChatSendResponse:
        session = self._repository.get(session_id)
        if session.messages and session.messages[-1].status != "done":
            raise ChatTurnConflict("Сначала повторите последний незавершённый ответ")
        question = help_question(content)
        if question is not None:
            policy.check_request(content)
            if self._help is None:
                raise AgentInputError("Ассистент проекта не настроен")
            turn = self._repository.start_turn(session_id, content, **self._model_metadata(),
                                               project_help={"question": question})
            return self._respond_turn(session, turn, policy)
        if session.task is not None and session.task.context.state != TaskState.DONE:
            ctx = session.task.context
            raise TaskConflict(
                f"Используйте действия задачи. Следующее действие: {ctx.current}", ctx=ctx,
                code="task_action_forbidden",
            )
        policy.check_request(content)
        turn = self._repository.start_turn(session_id, content, **self._model_metadata())
        return self._respond_turn(session, turn, policy)

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
        memory = self._memory.snapshot(
            session.id,
        )
        decision = None
        on_commit = None
        try:
            policy.check_request(turn.content)
            if self._memory_interpreter is not None and self._memory_repository is not None:
                rows = self._memory_repository.working_rows(session.id)
                if turn.memory_update is None:
                    try:
                        decision = self._memory_interpreter.interpret(turn.content, rows, context)
                    except Exception:
                        logger.warning("Working-memory interpretation failed; answering without changes", exc_info=True)
                        decision = WorkingUpdate()
                    self._repository.save_memory_update(session.id, turn.id, decision.model_dump_json())
                else:
                    decision = WorkingUpdate.model_validate_json(turn.memory_update)
                preview = apply_working_update(rows, decision, session.id, turn.id)
                memory = MemoryContext(
                    tuple({"category": row["category"], "content": row["content"]} for row in preview),
                    memory.long_term,
                )
                goal = next((row for row in preview if row["category"] == "goal"), None)
                if goal is not None and goal["source_message_id"]:
                    anchor = next((i for i, message in enumerate(session.messages)
                                   if message.id == goal["source_message_id"]), len(session.messages))
                    context = [{"role": message.role, "content": message.content}
                               for message in session.messages[anchor:]
                               if message.kind == "message" and not message.refusal and message.status == "done" and message.project_help is None]
                on_commit = lambda connection: self._memory_repository.commit_update(connection, session.id, turn.id, decision, rows)
            answer = decision.clarification if decision and decision.clarification else self._agent.respond(
                context, turn.content,
                orchestration=OrchestrationContext(invariants=policy.settings), memory=memory,
            )
            updated = self._repository.finish_turn(session.id, turn.id, policy.apply(answer), on_commit=on_commit)
        except InvariantViolation as error:
            updated = self._repository.finish_turn(session.id, turn.id, error.refusal(), refusal=True)
            return self._response(updated)
        except Exception:
            self._repository.fail_turn(session.id, turn.id)
            raise
        self._memory.remember(
            session_id=session.id,
            context=context,
            content=turn.content,
            memory=memory,
            source_message_id=turn.id,
            working_enabled=self._memory_interpreter is None,
        )
        return self._response(updated)


    def start_task(self, session_id: str, task: str) -> ChatSession:
        policy = self._policy()
        try:
            return self._start_task(session_id, task, policy)
        except InvariantViolation as error:
            self._refuse(session_id, "Задача: " + task, error)
            return self.get(session_id)

    def _start_task(self, session_id: str, task: str, policy: InvariantPolicy) -> ChatSession:
        session = self._repository.get(session_id)
        policy.check_request(task)
        self._repository.create_task(
            session_id, TaskContext(task=task.strip()),
            assistant_content=policy.apply(
                "Задача создана — нажмите «Сформировать план», затем утвердите его",
            ),
        )
        return self.get(session_id)

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
        if request.revision != stored.revision:
            raise TaskConflict(
                "Состояние изменилось. Обновите чат и повторите действие",
                ctx=stored.context, code="task_stale_revision",
            )
        ctx = stored.context
        action = require_action(ctx, request.action)
        if action not in {"pause", "resume"}:
            policy.check_request(request.content if action == "replan" else ctx.task + "\n" + "\n".join(ctx.notes))
        if action == "pause":
            updated, answer = pause(ctx), "Задача на паузе — этап, шаг и результаты сохранены"
        elif action == "resume":
            updated = resume(ctx)
            answer = "Продолжаем с сохранённого состояния — выполните ожидаемое действие"
        elif action == "approve":
            updated = approve_plan(ctx)
            answer = "План утверждён — можно выполнить следующий шаг"
        elif action == "replan":
            updated = replan(ctx, request.content)
            answer = "Требования сохранены — сформируйте новый план, готовые результаты доступны агенту"
        else:
            memory = self._memory.snapshot(session_id)
            runtime = self._runtime(session.provider)
            assert runtime._tasks is not None
            updated, answer = runtime._tasks.advance(
                ctx, orchestration=OrchestrationContext(invariants=policy.settings), memory=memory,
            )
        is_generation = action in {"generate_plan", "execute_step", "validate"}
        self._repository.update_task(
            session_id, updated,
            {"generate_plan": ctx.current, "execute_step": ctx.current, "validate": ctx.current,
             "approve": "Утвердить план", "pause": "Пауза",
             "resume": "Продолжить", "replan": "Пересмотреть план: " + request.content}[action],
            policy.apply(answer),
            expected_revision=None if is_generation else request.revision,
            expected_progress_revision=stored.progress_revision if is_generation else None,
            pause_only=action in {"pause", "resume"},
            **(runtime._model_metadata() if is_generation else {}),
        )
        return self.get(session_id)
