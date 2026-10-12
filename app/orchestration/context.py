from dataclasses import dataclass

from ..invariants import InvariantSettings


@dataclass(frozen=True, slots=True)
class OrchestrationContext:
    """Product rules for this call, separate from memory and task state."""

    invariants: InvariantSettings | None = None
