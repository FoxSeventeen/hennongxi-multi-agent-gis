from __future__ import annotations

from itertools import pairwise

import pytest
from hennongxi_contracts.state import (
    ACTIVE_TASK_STATUSES,
    TASK_TRANSITIONS,
    InvalidTaskTransition,
    TaskStatus,
    require_task_transition,
)


def test_task_state_graph_matches_the_approved_forward_sequence() -> None:
    happy_path = (
        TaskStatus.PENDING,
        TaskStatus.PLANNING,
        TaskStatus.DATA_PREPARING,
        TaskStatus.ANALYZING,
        TaskStatus.QUALITY_CHECKING,
        TaskStatus.PUBLISHING,
        TaskStatus.COMPLETED,
    )

    for current, target in pairwise(happy_path):
        assert TASK_TRANSITIONS[current] == frozenset({target, TaskStatus.FAILED})


@pytest.mark.parametrize("current", sorted(ACTIVE_TASK_STATUSES))
def test_every_active_state_can_fail(current: TaskStatus) -> None:
    assert TaskStatus.FAILED in TASK_TRANSITIONS[current]


@pytest.mark.parametrize("terminal", [TaskStatus.COMPLETED, TaskStatus.FAILED])
def test_terminal_states_have_no_outgoing_transition(terminal: TaskStatus) -> None:
    assert TASK_TRANSITIONS[terminal] == frozenset()


def test_skipping_a_state_raises_a_structured_transition_error() -> None:
    with pytest.raises(InvalidTaskTransition) as error:
        require_task_transition(TaskStatus.PENDING, TaskStatus.ANALYZING)

    assert error.value.current is TaskStatus.PENDING
    assert error.value.target is TaskStatus.ANALYZING
