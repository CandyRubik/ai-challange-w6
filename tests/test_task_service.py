"""Task actions accept explicit state and memory without a chat repository."""

import pytest
import json

from app.agents.agent import Agent
from app.memory.context import MemoryContext
from app.orchestration.context import OrchestrationContext
from app.services.tasks import TaskService
from app.state.task import StoredTask, TaskConflict, TaskState


def test_task_controls_require_current_revision_without_a_model():
    service = TaskService()
    created = service.start("Написать инструкцию", OrchestrationContext())
    stored = StoredTask(created.context, revision=4, progress_revision=2)
    with pytest.raises(TaskConflict, match="Состояние изменилось"):
        service.act(stored, "pause", revision=3, orchestration=OrchestrationContext(), memory=MemoryContext())
    change = service.act(stored, "pause", revision=4, orchestration=OrchestrationContext(), memory=MemoryContext())
    assert change.context.state == TaskState.PLANNING and change.context.paused
    assert change.pause_only and change.expected_revision == 4
    assert change.expected_progress_revision is None


def test_task_goal_is_taken_from_its_snapshot_not_memory():
    class PlanModel:
        def __init__(self):
            self.messages = None
        def generate(self, *, messages, **kwargs):
            self.messages = messages
            return json.dumps({"summary": "План", "plan": ["Шаг"], "criteria": ["Готово"]})

    model = PlanModel()
    service = TaskService(Agent(model))
    created = service.start("TASK_GOAL", OrchestrationContext())
    change = service.act(StoredTask(created.context, 0, 0), "generate_plan", revision=0,
                         orchestration=OrchestrationContext(), memory=MemoryContext(working=(
                             {"category": "goal", "content": "OTHER_GOAL"},
                             {"category": "constraint", "content": "KEEP_CONSTRAINT"},
                         )))
    assert change.context.task == "TASK_GOAL"
    assert "TASK_GOAL" in json.dumps(model.messages)
    assert "OTHER_GOAL" not in json.dumps(model.messages)
    assert "KEEP_CONSTRAINT" in json.dumps(model.messages)
