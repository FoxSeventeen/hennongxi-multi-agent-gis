"""Task-state vocabulary and the only legal transition table."""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    """Durable workflow states exposed by the public API."""

    PENDING = "PENDING"
    PLANNING = "PLANNING"
    DATA_PREPARING = "DATA_PREPARING"
    ANALYZING = "ANALYZING"
    QUALITY_CHECKING = "QUALITY_CHECKING"
    PUBLISHING = "PUBLISHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


ACTIVE_TASK_STATUSES = frozenset(
    {
        TaskStatus.PENDING,
        TaskStatus.PLANNING,
        TaskStatus.DATA_PREPARING,
        TaskStatus.ANALYZING,
        TaskStatus.QUALITY_CHECKING,
        TaskStatus.PUBLISHING,
    }
)

TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.PLANNING, TaskStatus.FAILED}),
    TaskStatus.PLANNING: frozenset({TaskStatus.DATA_PREPARING, TaskStatus.FAILED}),
    TaskStatus.DATA_PREPARING: frozenset({TaskStatus.ANALYZING, TaskStatus.FAILED}),
    TaskStatus.ANALYZING: frozenset({TaskStatus.QUALITY_CHECKING, TaskStatus.FAILED}),
    TaskStatus.QUALITY_CHECKING: frozenset({TaskStatus.PUBLISHING, TaskStatus.FAILED}),
    TaskStatus.PUBLISHING: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


class InvalidTaskTransition(ValueError):
    """Raised before persistence when a state edge is not in the approved graph."""

    def __init__(self, current: TaskStatus, target: TaskStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Task state cannot transition from {current} to {target}")


def require_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Reject a transition not represented in :data:`TASK_TRANSITIONS`."""

    if target not in TASK_TRANSITIONS[current]:
        raise InvalidTaskTransition(current, target)
