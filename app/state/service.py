"""Pure task transitions through typed domain events."""

from dataclasses import dataclass
from typing import Literal

from .task import (
    TaskContext, approve_plan, pause, resume, replan, require_action,
    propose_plan, complete_step, apply_validation,
)


@dataclass(frozen=True)
class PlanProposed:
    plan: tuple[str, ...]
    criteria: tuple[str, ...]


@dataclass(frozen=True)
class StepCompleted:
    output: str


@dataclass(frozen=True)
class ValidationCompleted:
    passed: bool
    report: str
    repair_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskCommand:
    action: Literal["approve", "pause", "resume", "replan"]
    note: str = ""


TaskEvent = PlanProposed | StepCompleted | ValidationCompleted | TaskCommand


class StateService:
    def resolve_action(self, snapshot: TaskContext, requested: str) -> str:
        return require_action(snapshot, requested)

    def apply(self, snapshot: TaskContext, event: TaskEvent) -> TaskContext:
        if isinstance(event, PlanProposed):
            return propose_plan(snapshot, event.plan, event.criteria)
        if isinstance(event, StepCompleted):
            return complete_step(snapshot, event.output)
        if isinstance(event, ValidationCompleted):
            return apply_validation(snapshot, passed=event.passed, report=event.report,
                                    repair_steps=event.repair_steps)
        if isinstance(event, TaskCommand):
            if event.action == "replan":
                return replan(snapshot, event.note)
            return {"approve": approve_plan, "pause": pause, "resume": resume}[event.action](snapshot)
        raise TypeError("Неизвестное событие задачи")
