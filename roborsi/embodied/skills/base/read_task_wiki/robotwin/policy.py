"""base.robotwin.read_task_wiki — read per-task accumulated wiki."""
from __future__ import annotations

from typing import Any


def dispatch_runtime(state, args: dict[str, Any]):
    from roborsi.embodied.agent_loop.rollout import _snapshot
    from roborsi.agents.task_wiki import read_wiki, wiki_path

    task = args.get("task")
    if not task:
        return ({"ok": False, "reason": "task name required"},
                _snapshot(state.env))
    md = read_wiki(task)
    return ({"ok": True, "task": task, "wiki_md": md,
             "path": str(wiki_path(task))},
            _snapshot(state.env))
