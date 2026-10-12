"""A prepared memory change, independent of the storage transaction."""

from dataclasses import dataclass

from .context import MemoryContext
from .updates import WorkingUpdate


@dataclass(frozen=True)
class PreparedMemory:
    context: MemoryContext
    update: WorkingUpdate | None = None
    expected_rows: tuple[dict, ...] = ()
    goal_source_message_id: str | None = None
