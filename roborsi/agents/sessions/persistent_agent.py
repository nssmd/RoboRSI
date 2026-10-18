"""Persistent Claude-session driver for the Planner and Reviewer roles.

Instead of a stateless `_call_vlm_tools` API call, each (role, task) gets ONE
persistent `claude` session — a real Claude Code process resumed every run — so
the Planner/Reviewer accumulate cross-run memory and can innovate, rather than
re-reacting to a curated wiki slice each time. Built on the vendored
`CodexRunner` (roborsi/agents/sessions/_codex_autoloop). The Manager periodically rolls
(summarizes + compacts) these sessions to keep context bounded — see
roborsi/agents/sessions/roll.py.

The Engineer is deliberately NOT driven this way (it holds the in-process sim).

The (role, task) -> session-id map lives in ~/.roborsi/agent_sessions.json;
each call resumes that session and writes back the latest id.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from roborsi.agents.sessions._codex_autoloop.codex_runner import (
    CodexRunner, RunnerOptions,
)
from roborsi.agents.sessions._codex_autoloop.runner_backend import BACKEND_CLAUDE

_REPO = Path(__file__).resolve().parents[3]
_SESSIONS = __import__("roborsi.embodied.paths", fromlist=["home"]).home() / "agent_sessions.json"
_MEMORY_DIR = __import__("roborsi.embodied.paths", fromlist=["home"]).home() / "agent_memory"
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


def _backend_for(model: str) -> str:
    """codex for OpenAI models, claude otherwise; ROBORSI_ROLE_BACKEND overrides."""
    return os.environ.get("ROBORSI_ROLE_BACKEND") or (
        "codex" if str(model).startswith(("gpt-", "o3", "o4", "openai/"))
        else BACKEND_CLAUDE)


def _session_task(task: str) -> str:
    """The concrete task a session belongs to: the simulator task key when
    the atomic is shared by many tasks, else the atomic itself."""
    from roborsi.agents.memory.task_memory_identity import key as memory_key
    return memory_key(task) or task


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
    roll/cleanup — see roborsi/agents/sessions/roll.py."""
    with _state_lock():
        sessions = _read_json(_SESSIONS)
        sessions.pop(f"{role}:{task}", None)
        _write_json(_SESSIONS, sessions)


def run(role: str, task: str, prompt: str, *, system_prompt: str,
        model: str = _DEFAULT_MODEL, json_schema_path: str | None = None,
        backend: str | None = None, images: list[str] | None = None) -> str:
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
    backend = backend or _backend_for(model)
    if backend == "codex":
        model = str(model).removeprefix("openai/")
    sys_full = _PERMISSIONS_PREAMBLE + system_prompt
    # One session per role and concrete simulator task (e.g.
    # planner:libero_spatial/0). It is compacted into a memory summary once
    # its context grows past ROBORSI_SESSION_ROLL_CHARS.
    task = _session_task(task)
    key = f"{role}:{task}"
    thread_id = _load().get(key)
    if thread_id is None:
        prompt = _seed_fresh(role, task, prompt)
    if backend != BACKEND_CLAUDE:
        # codex/copilot have no --append-system-prompt; the per-call
        # instructions (mode, decision type, role skills) go with every turn.
        prompt = f"=== SYSTEM (your role, binding) ===\n{sys_full}\n\n=== TASK ===\n{prompt}"
    if images and backend == BACKEND_CLAUDE:
        prompt += ("\n\nOpen and look at these camera images with your Read tool "
                   "before deciding:\n" + "\n".join(images))
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
        images=images if backend == "codex" else None,
    )
    # Parallel episodes share one session per role; serialize its turns.
    import fcntl
    _SESSIONS.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _SESSIONS.with_name("agent_session_" + key.replace("/", "_").replace(":", "_") + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        thread_id = _load().get(key) or thread_id
        result = runner.run_exec(prompt=prompt, resume_thread_id=thread_id,
                                 options=options, run_label=key)
        _save(key, result.thread_id)
    if not result.last_agent_message or result.turn_failed:
        # A failed turn's last message is interim commentary, not an answer.
        raise RuntimeError(
            f"persistent_agent[{key}] returned no message "
            f"(turn_failed={result.turn_failed}, fatal_error={result.fatal_error})")
    if _grow(key, len(prompt) + len(result.last_agent_message)) and not getattr(_ROLL_STATE, 'active', False):
        _roll(role, task, model=model, backend=backend)
    return result.last_agent_message


_USAGE = _SESSIONS.with_name("agent_session_usage.json")
_ROLL_PROMPT = (
    "Compact your own memory. Summarize the DURABLE lessons you have learned "
    "across ALL your prior turns: what reliably works, what fails and why, "
    "validated parameters and pipelines, how tasks relate, and open problems, "
    "as a tight bulleted list (at most 25 bullets). A fresh copy of you will "
    "be seeded with ONLY this summary. Output ONLY the bullets."
)
import threading as _threading

_ROLL_STATE = _threading.local()


def _grow(key: str, chars: int) -> bool:
    """Add this turn's size to the session total; True once it exceeds the
    roll threshold."""
    with _state_lock():
        usage = _read_json(_USAGE)
        usage[key] = int(usage.get(key, 0)) + int(chars)
        _write_json(_USAGE, usage)
    limit = int(os.environ.get("ROBORSI_SESSION_ROLL_CHARS", "600000"))
    return usage[key] >= limit


def _roll(role: str, task: str, *, model: str, backend: str) -> None:
    """Summarize the live session into its memory file and start fresh."""
    _ROLL_STATE.active = True
    try:
        summary = run(role, task, _ROLL_PROMPT,
                      system_prompt="Compact your session memory for a successor.",
                      model=model, backend=backend)
        mem = memory_file(role, task)
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text(summary, encoding="utf-8")
        clear(role, task)
    except Exception as exc:  # noqa: BLE001
        print(f"[session] roll of {role}:{task} failed: {exc}", flush=True)
    finally:
        # Reset the counter either way so a failed roll is not retried on
        # every later turn.
        with _state_lock():
            usage = _read_json(_USAGE)
            usage.pop(f"{role}:{task}", None)
            _write_json(_USAGE, usage)
        _ROLL_STATE.active = False


def run_role(role: str, task: str, user_block: str, *,
             system_prompt: str, model: str,
             images: list[str] | None = None) -> str:
    """Role turn text — persistent session by default, or the stateless one-shot
    when ROBORSI_ROLE_SESSION=0 (instant rollback for the live campaign).

    Centralises the gate so Planner and Reviewer share one dispatch. The fallback
    reproduces the pre-session [system, user] → _call_vlm_tools → text path."""
    from roborsi.agents.roles.role_skills import ROLES, with_role_skills
    from roborsi.runtime_mode import evaluation_prompt, is_eval_mode
    if role in ROLES:
        system_prompt = with_role_skills(role, system_prompt)
    frozen_eval = is_eval_mode()
    if frozen_eval:
        system_prompt = system_prompt + "\n\n" + evaluation_prompt()
    if os.environ.get("ROBORSI_ROLE_SESSION", "1") != "0":
        return run(role, task, user_block, system_prompt=system_prompt,
                   model=model, images=images)
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools
    from roborsi.channels.core.agent import _extract_text_block
    if role == "planner" and os.environ.get("ROBORSI_AUDIT_PLANNER_INPUT") == "1":
        import time
        from roborsi.embodied.paths import home as evidence_home
        try:
            audit_dir = evidence_home() / "planner_inputs"
            audit_dir.mkdir(parents=True, exist_ok=True)
            (audit_dir / (str(time.time_ns()) + ".json")).write_text(
                json.dumps({"role": role, "task": task, "model": model,
                            "system": system_prompt, "user": user_block,
                            "capture": "actual_model_request",
                            "episode_key": os.environ.get("ROBORSI_EVIDENCE_EPISODE_KEY")}, ensure_ascii=True),
                encoding="utf-8")
        except OSError as audit_error:
            print("[planner-input-capture] " + type(audit_error).__name__)
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


def _state_lock():
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def held():
        _SESSIONS.parent.mkdir(parents=True, exist_ok=True)
        with _SESSIONS.with_name("agent_sessions.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
    return held()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _load() -> dict[str, str]:
    return _read_json(_SESSIONS)


def _save(key: str, thread_id: str | None) -> None:
    if not thread_id:
        return
    with _state_lock():
        sessions = _read_json(_SESSIONS)
        sessions[key] = thread_id
        _write_json(_SESSIONS, sessions)
