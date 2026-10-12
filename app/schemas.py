from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        strict=True,
    )


class ChatSendRequest(StrictModel):
    content: Annotated[str, Field(min_length=1, max_length=12_000)]
    provider: Literal["ollama", "deepseek"] | None = None
    operation_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class ChatSessionCreateRequest(StrictModel):
    provider: Literal["ollama", "deepseek"] | None = None


class ChatModelUpdateRequest(StrictModel):
    provider: Literal["ollama", "deepseek"]


class ChatMessage(StrictModel):
    id: str
    role: Literal["user", "assistant"]
    kind: Literal["message", "command"] = "message"
    refusal: bool = False
    content: str
    created_at: datetime

    status: Literal["pending", "done", "failed"] = "done"
    error: str | None = None
    provider: Literal["ollama", "deepseek"] | None = None
    model: str | None = None
    project_help: dict | None = None


class TaskStartRequest(StrictModel):
    task: Annotated[str, Field(min_length=1, max_length=12_000)]
    operation_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None


TaskAction = Literal["generate_plan", "execute_step", "validate", "approve", "pause", "resume", "replan"]


class TaskActionRequest(StrictModel):
    action: Literal["advance", "generate_plan", "execute_step", "validate", "approve", "pause", "resume", "replan"]
    revision: Annotated[int, Field(ge=0)]
    content: Annotated[str, Field(max_length=4_000)] = ""

    @model_validator(mode="after")
    def require_replan_note(self) -> "TaskActionRequest":
        if self.action == "replan" and not self.content:
            raise ValueError("Укажите, что изменить в плане")
        if self.action != "replan" and self.content:
            raise ValueError("Комментарий допустим только для пересмотра плана")
        return self


class TaskSummary(StrictModel):
    state: Literal["planning", "awaiting_approval", "execution", "validation", "done"]
    step: int
    total: int
    current: str
    paused: bool
    revision: int


class TaskStepResult(StrictModel):
    title: str
    output: str


class TaskView(TaskSummary):
    task: str
    expected_action: Literal["generate_plan", "approve_plan", "execute_step", "validate", "none"]
    plan: tuple[str, ...]
    done: tuple[TaskStepResult, ...]
    criteria: tuple[str, ...]
    notes: tuple[str, ...]
    previous_results: tuple[TaskStepResult, ...]
    validation_report: str
    result: str
    plan_approved: bool
    validation_passed: bool | None
    allowed_actions: tuple[TaskAction, ...]


class ChatSessionSummary(StrictModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    provider: Literal["ollama", "deepseek"] = "deepseek"
    task: TaskSummary | None = None
    parent_session_id: str | None = None


class ChatSession(ChatSessionSummary):
    messages: list[ChatMessage]
    task: TaskView | None = None


class ChatSendResponse(StrictModel):
    session: ChatSessionSummary
    user_message: ChatMessage
    assistant_message: ChatMessage


class ChatOperationStatus(StrictModel):
    session: ChatSessionSummary
    user_message: ChatMessage


MemoryLayer = Literal["working", "long_term"]
class MemoryCreateRequest(StrictModel):
    layer: MemoryLayer
    category: Annotated[str, Field(min_length=1, max_length=40)]
    content: Annotated[str, Field(min_length=1, max_length=4_000)]
    session_id: str | None = None
    source_session_id: str | None = None
    source_text: Annotated[str, Field(min_length=1, max_length=4_000)] | None = None
    operation_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class MemoryEntry(StrictModel):
    id: str
    layer: MemoryLayer
    category: str
    content: str
    session_id: str | None
    created_at: datetime
    updated_at: datetime
    source_session_id: str | None = None
    source_message_id: str | None = None


class MemorySnapshot(StrictModel):
    working: list[MemoryEntry]
    long_term: list[MemoryEntry]
