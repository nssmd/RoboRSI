"""Turn a raw event stream into a progress card a chat surface can show.

A run emits dozens of events; a chat window can hold one useful summary. Posting
each `tool_call` as its own message would both bury the conversation and hit
Telegram's per-chat rate limit within a single rollout.

So this keeps a rolling summary — the current phase, the last few tools, elapsed
time — and the adapter edits one message in place. Which events matter and how
they read is a display concern shared by every platform, so it lives here rather
than inside the Telegram adapter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# The milestones worth a line of their own. Everything else folds into the
# rolling tool list; nothing is dropped silently, it is just summarised.
PHASES = {
    "user_message": "收到",
    "opus_call": "Manager 思考中",
    "lh3role_start": "三角色启动",
    "lh3role_planned": "Planner 出计划",
    "lh3role_executed": "Engineer 执行完",
    "lh3role_reviewed": "Reviewer 复核完",
    "task_result": "任务结束",
    "done": "完成",
    "opus_hop_exhausted": "达到步数上限",
}

TERMINAL = {"done", "task_result", "opus_hop_exhausted"}
RECENT_TOOLS = 5


@dataclass
class RunProgress:
    """Rolling state for one conversation's current run."""

    started: float = field(default_factory=time.time)
    phase: str = "收到"
    tools: list[str] = field(default_factory=list)
    n_events: int = 0
    finished: bool = False
    note: str = ""

    def feed(self, event: dict[str, Any]) -> bool:
        """Absorb one event. Returns True when the display should be refreshed.

        Not every event is worth a network round trip: `opus_thinking` fires
        constantly and says nothing a user can act on.
        """
        kind = str(event.get("kind") or "")
        self.n_events += 1

        if kind in PHASES:
            self.phase = PHASES[kind]
            if kind in TERMINAL:
                self.finished = True
            if kind == "lh3role_reviewed":
                verdict = event.get("verdict") or event.get("outcome")
                if verdict:
                    self.note = str(verdict)
            return True

        if kind == "tool_call":
            tool = str(event.get("tool") or event.get("name") or "?")
            self.tools.append(tool)
            del self.tools[:-RECENT_TOOLS]
            return True

        if kind == "sim_progress":
            step = event.get("step")
            if step is not None:
                self.note = f"仿真第 {step} 步"
                return True

        return False

    def render(self) -> str:
        """One compact block. Kept short — it sits inline in a chat."""
        secs = int(time.time() - self.started)
        mark = "✅" if self.finished else "⏳"
        lines = [f"{mark} {self.phase}   {secs}s"]
        if self.tools:
            lines.append("· " + " → ".join(self.tools))
        if self.note:
            lines.append(f"· {self.note}")
        return "\n".join(lines)
