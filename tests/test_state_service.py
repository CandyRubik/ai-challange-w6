"""The task automaton is usable without a provider or storage."""

import pytest

from app.state.service import (
    StateService, PlanProposed, StepCompleted, ValidationCompleted, TaskCommand,
)
from app.state.task import TaskContext, TaskConflict, TaskState


def test_complete_task_through_state_events():
    service = StateService()
    original = TaskContext(task="Написать инструкцию")
    planned = service.apply(original, PlanProposed(("Написать текст",), ("Текст готов",)))
    approved = service.apply(planned, TaskCommand("approve"))
    completed = service.apply(approved, StepCompleted("Инструкция написана"))
    result = service.apply(completed, ValidationCompleted(True, "Критерии выполнены"))
    assert original.state == TaskState.PLANNING
    assert result.state == TaskState.DONE
    assert result.result == "Критерии выполнены"
    assert result.done[0].output == "Инструкция написана"


def test_step_requires_approval_and_pause_keeps_the_plan():
    service = StateService()
    planned = service.apply(TaskContext(task="Написать инструкцию"), PlanProposed(("Текст",), ("Готов",)))
    with pytest.raises(TaskConflict, match="не утверждён"):
        service.apply(planned, StepCompleted("Текст"))
    paused = service.apply(planned, TaskCommand("pause"))
    assert paused.plan == planned.plan and paused.paused
    with pytest.raises(TaskConflict, match="паузе"):
        service.apply(paused, TaskCommand("approve"))
    assert service.apply(paused, TaskCommand("resume")).allowed_actions == planned.allowed_actions
