"""Process-local RoboRSI run mode.

``evolve`` is the normal self-evolution runtime. ``eval`` freezes the released
capability set: execution artifacts are still recorded, but no skill, task
memory, proposal, persistent role session, or training dataset may be updated.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Iterator


class RunMode(str, Enum):
    EVOLVE = "evolve"
    EVAL = "eval"


class EvolutionDisabledError(RuntimeError):
    """Raised when a mutation is attempted during a frozen evaluation."""


_ACTIVE_MODE: ContextVar[RunMode | None] = ContextVar(
    "roborsi_active_run_mode", default=None
)


def parse_mode(value: str | RunMode | None) -> RunMode:
    if isinstance(value, RunMode):
        return value
    raw = str(value or RunMode.EVOLVE.value).strip().lower()
    try:
        return RunMode(raw)
    except ValueError as exc:
        choices = ", ".join(mode.value for mode in RunMode)
        raise ValueError(f"run mode must be one of: {choices}; got {value!r}") from exc


def current_mode() -> RunMode:
    active = _ACTIVE_MODE.get()
    if active is not None:
        return active
    return parse_mode(os.environ.get("ROBORSI_RUN_MODE"))


def is_eval_mode() -> bool:
    return current_mode() is RunMode.EVAL


def evolution_enabled() -> bool:
    return current_mode() is RunMode.EVOLVE


def require_evolution(action: str) -> None:
    if not evolution_enabled():
        raise EvolutionDisabledError(
            f"{action} is disabled in eval mode; the released capability set is frozen"
        )


def evaluation_prompt() -> str:
    return (
        "EVALUATION MODE (binding): the released skills and persistent memory are "
        "frozen. Use only existing published capabilities. You may replan within "
        "this episode, but do not create, register, propose, promote, train, or "
        "persist any capability. If the frozen system cannot complete the task, "
        "report failure honestly."
    )


@contextmanager
def use_run_mode(mode: str | RunMode) -> Iterator[RunMode]:
    parsed = parse_mode(mode)
    token = _ACTIVE_MODE.set(parsed)
    try:
        yield parsed
    finally:
        _ACTIVE_MODE.reset(token)
