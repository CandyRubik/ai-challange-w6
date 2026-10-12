from __future__ import annotations

from ..agents.agent import Agent
from ..memory.context import MemoryContext
from ..invariants import InvariantPolicy
from dataclasses import dataclass

from ..state.task import TaskContext, StoredTask, TaskConflict
from ..state.service import StateService, PlanProposed, StepCompleted, ValidationCompleted, TaskCommand
from ..orchestration.context import OrchestrationContext


@dataclass(frozen=True)
class TaskChange:
    context: TaskContext
    user_content: str
    answer: str
    expected_revision: int | None = None
    expected_progress_revision: int | None = None
    pause_only: bool = False
    generated: bool = False


class TaskService:
    """Choose the next model stage and apply explicit domain transitions."""

    def __init__(self, agent: Agent | None = None) -> None:
        self._agent = agent
        self._state = StateService()

    def start(self, task: str, orchestration: OrchestrationContext) -> TaskChange:
        policy = InvariantPolicy(orchestration.invariants)
        policy.check_request(task)
        return TaskChange(TaskContext(task=task.strip()), "Задача: " + task.strip(), policy.apply(
            "Задача создана — нажмите «Сформировать план», затем утвердите его",
        ))

    def resolve_action(self, stored: StoredTask, requested: str, revision: int) -> str:
        if revision != stored.revision:
            raise TaskConflict("Состояние изменилось. Обновите чат и повторите действие",
                               ctx=stored.context, code="task_stale_revision")
        return self._state.resolve_action(stored.context, requested)

    def act(
        self, stored: StoredTask, requested: str, *, revision: int,
        orchestration: OrchestrationContext, memory: MemoryContext, content: str = "",
    ) -> TaskChange:
        ctx = stored.context
        action = self.resolve_action(stored, requested, revision)
        policy = InvariantPolicy(orchestration.invariants)
        if action not in {"pause", "resume"}:
            policy.check_request(content if action == "replan" else ctx.task + "\n" + "\n".join(ctx.notes))
        answers = {
            "pause": "Задача на паузе — этап, шаг и результаты сохранены",
            "resume": "Продолжаем с сохранённого состояния — выполните ожидаемое действие",
            "approve": "План утверждён — можно выполнить следующий шаг",
            "replan": "Требования сохранены — сформируйте новый план, готовые результаты доступны агенту",
        }
        generated = action not in answers
        if generated:
            if self._agent is None:
                raise ValueError("Для действия задачи нужен агент")
            task_memory = MemoryContext(
                tuple(item for item in memory.working if item["category"] != "goal"), memory.long_term,
            )
            updated, answer = self._advance(ctx, orchestration=orchestration, memory=task_memory)
        else:
            updated = self._state.apply(ctx, TaskCommand(action, content))
            answer = answers[action]
        user_content = {"approve": "Утвердить план", "pause": "Пауза", "resume": "Продолжить",
                        "replan": "Пересмотреть план: " + content}.get(action, ctx.current)
        return TaskChange(updated, user_content, policy.apply(answer),
                          expected_revision=None if generated else revision,
                          expected_progress_revision=stored.progress_revision if generated else None,
                          pause_only=action in {"pause", "resume"}, generated=generated)

    def _advance(
        self, ctx: TaskContext, *, orchestration: OrchestrationContext,
        memory: MemoryContext,
    ) -> tuple[TaskContext, str]:
        self._state.resolve_action(ctx, ctx.expected_action)
        policy = InvariantPolicy(orchestration.invariants)
        upper = orchestration.invariants is not None and orchestration.invariants.uppercase_enabled
        if ctx.expected_action == "generate_plan":
            output = self._agent.plan_task(ctx, orchestration=orchestration, memory=memory)
            updated = self._state.apply(ctx, PlanProposed(
                tuple(text.upper() if upper else text for text in output.plan),
                tuple(text.upper() if upper else text for text in output.criteria),
            ))
            answer = output.summary + "\n\nПлан:\n" + "\n".join(
                f"{index}. {title}" for index, title in enumerate(output.plan, 1)
            ) + "\n\nКритерии готовности:\n" + "\n".join(
                "• " + criterion for criterion in output.criteria
            ) + "\n\nУтвердите план или укажите изменения."
            return updated, answer
        if ctx.expected_action == "execute_step":
            answer = self._agent.execute_task_step(ctx, orchestration=orchestration, memory=memory)
            answer = policy.apply(answer)
            return self._state.apply(ctx, StepCompleted(answer)), answer
        if ctx.expected_action == "validate":
            output = self._agent.validate_task(ctx, orchestration=orchestration, memory=memory)
            report = policy.apply(output.report)
            updated = self._state.apply(ctx, ValidationCompleted(
                passed=output.passed, report=report,
                repair_steps=tuple(text.upper() if upper else text for text in output.repair_steps),
            ))
            answer = report
            if not output.passed:
                answer += "\n\nШаги исправления:\n" + "\n".join(output.repair_steps)
            return updated, answer
        raise AssertionError("Неизвестный этап генерации")
