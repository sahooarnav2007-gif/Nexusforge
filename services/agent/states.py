"""Explicit agent state machine with validated transitions."""
from __future__ import annotations

from core.models import TaskState

# legal transitions: state -> set of states it may enter
TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.IDLE: {TaskState.QUEUED, TaskState.CANCELLED},
    TaskState.QUEUED: {TaskState.ANALYZING, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.ANALYZING: {TaskState.PLANNING, TaskState.CANCELLED, TaskState.FAILED,
                          TaskState.TIMEOUT},
    TaskState.PLANNING: {TaskState.IMPLEMENTING, TaskState.CANCELLED, TaskState.FAILED,
                         TaskState.TIMEOUT},
    TaskState.IMPLEMENTING: {TaskState.TESTING, TaskState.CANCELLED, TaskState.FAILED,
                             TaskState.TIMEOUT},
    TaskState.TESTING: {TaskState.SECURITY_ANALYSIS, TaskState.DEBUGGING,
                        TaskState.CANCELLED, TaskState.FAILED, TaskState.TIMEOUT},
    TaskState.DEBUGGING: {TaskState.RETESTING, TaskState.CANCELLED, TaskState.FAILED,
                          TaskState.TIMEOUT},
    TaskState.RETESTING: {TaskState.SECURITY_ANALYSIS, TaskState.DEBUGGING,
                          TaskState.TESTING, TaskState.CANCELLED, TaskState.FAILED,
                          TaskState.TIMEOUT},
    TaskState.SECURITY_ANALYSIS: {TaskState.CODE_REVIEW, TaskState.SECURITY_BLOCKED,
                                  TaskState.CANCELLED, TaskState.FAILED, TaskState.TIMEOUT},
    TaskState.CODE_REVIEW: {TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED,
                            TaskState.TIMEOUT},
    # terminal
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
    TaskState.TIMEOUT: set(),
    TaskState.SECURITY_BLOCKED: set(),
}


class InvalidTransitionError(Exception):
    pass


def can_transition(src: TaskState, dst: TaskState) -> bool:
    return dst in TRANSITIONS.get(src, set())


def transition(src: TaskState, dst: TaskState) -> TaskState:
    if not can_transition(src, dst):
        raise InvalidTransitionError(f"Illegal state transition: {src.value} -> {dst.value}")
    return dst