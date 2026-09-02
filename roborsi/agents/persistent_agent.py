"""Persistent Claude-session driver for the Planner and Reviewer roles.

Instead of a stateless `_call_vlm_tools` API call, each (role, task) gets ONE
persistent `claude` session — a real Claude Code process resumed every run — so
the Planner/Reviewer accumulate cross-run memory and can innovate, rather than
re-reacting to a curated wiki slice each time. Built on the vendored
`CodexRunner` (roborsi/agents/_codex_autoloop). The Manager periodically rolls
(summarizes + compacts) these sessions to keep context bounded — see
scripts/roll_agent_sessions.py.

The Engineer is deliberately NOT driven this way (it holds the in-process sim).

The (role, task) -> session-id map lives in ~/.roborsi/agent_sessions.json;
each call resumes that session and writes back the latest id.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from roborsi.agents._codex_autoloop.codex_runner import (
    CodexRunner, RunnerOptions,
)
from roborsi.agents._codex_autoloop.runner_backend import BACKEND_CLAUDE

_REPO = Path(__file__).resolve().parents[2]
_SESSIONS = Path.home() / ".roborsi" / "agent_sessions.json"
_MEMORY_DIR = Path.home() / ".roborsi" / "agent_memory"
_DEFAULT_MODEL = "claude-opus-4-8"

# Binding permissions preamble prepended to EVERY role session's system prompt.
# These sessions run headless with bypassPermissions (full tools), so the
# read-only / propose-only contract is enforced here in the prompt. Full detail:
# roborsi/agents/PROCESS_PERMISSIONS.md.
_PERMISSIONS_PREAMBLE = (
    "PERMISSIONS CONTRACT (binding — full detail in "
    "roborsi/agents/PROCESS_PERMISSIONS.md):\n"
    "- You may READ anything (code, wiki, workspaces, frames).\n"
    "- You MUST NOT directly edit/write/delete skills, the task wiki, the "
    "baseline plan, or any repo file. Produce ONLY your role's structured "
    "output (Planner: the plan blocks; Reviewer: the verdict JSON).\n"
    "- To change a skill or the wiki, PROPOSE it via your verdict's "
    "proposal_decision + proposal_payload fields — never by editing a file. "
    "Applying a proposal is the Manager's job (the approver).\n\n"
)


def session_id(role: str, task: str) -> str | None:
    """The persisted session id for this (role, task), or None if never run."""
    return _load().get(f"{role}:{task}")


def memory_file(role: str, task: str) -> Path:
    """Where a rolled-up (compacted) memory summary for this session lives."""
    safe = f"{role}_{task}".replace("/", "_")
    return _MEMORY_DIR / f"{safe}.md"


def clear(role: str, task: str) -> None:
    """Forget the live session id so the next run starts a FRESH session
    (which is then seeded with memory_file if present). Used by the Manager's
    roll/cleanup — see scripts/roll_agent_sessions.py."""
    sessions = _load()
    sessions.pop(f"{role}:{task}", None)
    _SESSIONS.parent.mkdir(parents=True, exist_ok=True)
    _SESSIONS.write_text(json.dumps(sessions, indent=2, ensure_ascii=False),
                         encoding="utf-8")


def run(role: str, task: str, prompt: str, *, system_prompt: str,
        model: str = _DEFAULT_MODEL, json_schema_path: str | None = None,
        backend: str | None = None) -> str:
    """Run one turn of the persistent (role, task) session and return its final
    message text. Resumes the same session each call so memory persists.

    `backend` selects the agent CLI ("claude" default / "codex" / "copilot");
    unset falls back to env ROBORSI_ROLE_BACKEND then claude. `system_prompt`
    is the role's instructions; on claude it is injected via --append-system-prompt
    every turn, on codex/copilot (no such flag) it is prepended into the FRESH
    turn's prompt. `json_schema_path` forces structured output (only honoured on the
    first turn of a fresh session). The subprocess inherits this process's env
    (provider credentials from the environment), so it runs headless inside
    cli_3role."""
    backend = backend or os.environ.get("ROBORSI_ROLE_BACKEND", BACKEND_CLAUDE)
    sys_full = _PERMISSIONS_PREAMBLE + system_prompt
    key = f"{role}:{task}"
    thread_id = _load().get(key)
    if thread_id is None:
        prompt = _seed_fresh(role, task, prompt)
        if backend != BACKEND_CLAUDE:   # codex/copilot have no --append-system-prompt
            prompt = f"=== SYSTEM (your role, binding) ===\n{sys_full}\n\n=== TASK ===\n{prompt}"
    # The claude CLI wants the BARE model id ("claude-opus-4-8"); the API path
    # (_call_vlm_tools fallback) uses the litellm-style "anthropic/claude-opus-4-8".
    # Strip the provider prefix for claude; null claude ids on codex/copilot
    # (invalid there — let those backends use their own default).
    if backend == BACKEND_CLAUDE:
        model = str(model).removeprefix("anthropic/")
    elif str(model).startswith("claude"):
        model = None
    runner = CodexRunner(backend=backend)
    options = RunnerOptions(
        model=model,
        # bypass permission prompts — these sessions run headless with no TTY
        # (the in-process API agents they replace had no permission gating either).
        dangerous_yolo=True,
        working_dir=str(_REPO),   # session keyed to the repo project; can read code
        # --strict-mcp-config: ignore the repo's filesystem MCP config so the
        # session does NOT spin up the Codex MCP server (node→codex, ~minutes of
        # startup) on every fresh planner/reviewer call. Built-in Read/Grep/Bash
        # (what the reviewer needs to inspect skill code) are native, not MCP.
        # These flags are claude-only; codex/copilot get the role prompt prepended.
        extra_args=(["--strict-mcp-config", "--append-system-prompt", sys_full]
                    if backend == BACKEND_CLAUDE else []),
        output_schema_path=json_schema_path,
    )
    result = runner.run_exec(prompt=prompt, resume_thread_id=thread_id,
                             options=options, run_label=key)
    _save(key, result.thread_id)
    if not result.last_agent_message:
        raise RuntimeError(
            f"persistent_agent[{key}] returned no message "
            f"(turn_failed={result.turn_failed}, fatal_error={result.fatal_error})")
    return result.last_agent_message


def run_role(role: str, task: str, user_block: str, *,
             system_prompt: str, model: str) -> str:
    """Role turn text — persistent session by default, or the stateless one-shot
    when ROBORSI_ROLE_SESSION=0 (instant rollback for the live campaign).

    Centralises the gate so Planner and Reviewer share one dispatch. The fallback
    reproduces the pre-session [system, user] → _call_vlm_tools → text path."""
    if os.environ.get("ROBORSI_ROLE_SESSION", "1") != "0":
        return run(role, task, user_block, system_prompt=system_prompt, model=model)
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools
    from roborsi.channels.core.agent import _extract_text_block
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_block}]
    resp = _call_vlm_tools(model, messages, [], thinking_budget=0, tool_choice="none")
    content = getattr(resp, "content", "") or ""
    if isinstance(content, list):
        content = "".join(_extract_text_block(c) for c in content)
    return content


def _seed_fresh(role: str, task: str, prompt: str) -> str:
    """On a fresh session, prepend any rolled-up memory the Manager compacted
    from earlier sessions, so the new session does not start cold."""
    mem = memory_file(role, task)
    if not mem.exists():
        return prompt
    return (f"=== YOUR COMPACTED MEMORY (durable lessons from your earlier "
            f"sessions on this task — you wrote these) ===\n"
            f"{mem.read_text(encoding='utf-8')}\n\n=== CURRENT TASK ===\n{prompt}")


def _load() -> dict[str, str]:
    if not _SESSIONS.exists():
        return {}
    return json.loads(_SESSIONS.read_text(encoding="utf-8"))


def _save(key: str, thread_id: str | None) -> None:
    if not thread_id:
        return
    sessions = _load()
    sessions[key] = thread_id
    _SESSIONS.parent.mkdir(parents=True, exist_ok=True)
    _SESSIONS.write_text(json.dumps(sessions, indent=2, ensure_ascii=False),
                         encoding="utf-8")
