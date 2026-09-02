"""Feishu bot = Claude Opus agent + dynamic skill registry.

roborsi IS a skill runtime — the bot exposes the WHOLE registry, not
just a few hardcoded tools. Opus discovers skills, reads their SKILL.md,
invokes them. Same model as inside the sim's rollout agent, but here the
"action surface" is every skill in the repo.

Tools given to Opus:
  - list_skills(category?)  → catalog (name, kind, category, description)
  - read_skill(name)         → full SKILL.md text
  - run_skill(name, args)    → invoke skill.run(**args), return result
  - recent_runs()            → recent task runs from runs/ dir
  - render_demo(run_id)      → produce mp4 from frames + upload to chat
  - get_status(run_id)       → live status of a specific run
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = str(Path(__file__).resolve().parents[3])

_TOOLS = [
    {"type": "function", "function": {
        "name": "list_skills",
        "description": "List all roborsi skills. Filter by kind/category/name_contains.",
        "parameters": {"type": "object", "properties": {
            "category": {"type": "string"},
            "kind": {"type": "string"},
            "name_contains": {"type": "string"},
        }},
    }},
    {"type": "function", "function": {
        "name": "read_skill",
        "description": "Read a skill's full SKILL.md.",
        "parameters": {"type": "object",
            "properties": {"name": {"type": "string"}}, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "run_skill",
        "description": "Invoke a skill INLINE. Atomic rollouts block ~30-90s. Demo auto-pushes when done.",
        "parameters": {"type": "object",
            "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
            "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "recent_runs",
        "description": "List recent task runs (top 10).",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_status",
        "description": "Get status of a run_id.",
        "parameters": {"type": "object",
            "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    }},
    {"type": "function", "function": {
        "name": "render_demo",
        "description": "Render finished run frames into mp4.",
        "parameters": {"type": "object",
            "properties": {"run_id": {"type": "string"}, "camera": {"type": "string"}},
            "required": ["run_id"]},
    }},
    {"type": "function", "function": {
        "name": "read_skill_code",
        "description": "Return policy.py source of a skill.",
        "parameters": {"type": "object",
            "properties": {"name": {"type": "string"}}, "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "propose_new_skill",
        "description": ("Propose creating a new base skill (goes to human review "
                         "queue). For category='base/robotwin/*' the skill_md MUST "
                         "include a `harness:` block in YAML frontmatter per "
                         "harness_standard SKILL.md — without it the apply gate "
                         "treats the proposal as 'unvalidated' and REJECTS. The "
                         "harness block at minimum needs: sim_task, args (list of "
                         "≥1 invocation dict), pass_criteria (kind + min_seeds_passing). "
                         "See roborsi/embodied/skills/base/robotwin/"
                         "pick_actor_by_contact_point/SKILL.md for a worked example."),
        "parameters": {"type": "object",
            "properties": {"name": {"type": "string"}, "category": {"type": "string"},
                            "description": {"type": "string"}, "code": {"type": "string"},
                            "skill_md": {"type": "string"}, "rationale": {"type": "string"}},
            "required": ["name", "description", "code", "skill_md", "rationale"]},
    }},
    {"type": "function", "function": {
        "name": "propose_skill_update",
        "description": ("Propose editing an existing skill (goes to human review "
                         "queue). If the target is a base/robotwin skill, the apply "
                         "gate will re-run that skill's harness — make sure the "
                         "existing harness: block still describes a valid test for "
                         "the new code (or include an updated skill_md if you need "
                         "to change the harness block too)."),
        "parameters": {"type": "object",
            "properties": {"name": {"type": "string"}, "new_code": {"type": "string"},
                            "skill_md": {"type": "string",
                                          "description": "Optional new SKILL.md content if harness block needs updating"},
                            "rationale": {"type": "string"}},
            "required": ["name", "new_code", "rationale"]},
    }},
    {"type": "function", "function": {
        "name": "list_pending_proposals",
        "description": "List pending skill proposals awaiting review.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_lh_report",
        "description": ("Read a long-horizon eval report: plan, per-atomic outcomes, "
                         "judge reasons, pre/post frame paths, wall times. Use this "
                         "BEFORE proposing base-skill fixes — it tells you WHICH atomic "
                         "failed and WHY (judge reason). Defaults to latest run."),
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string",
                       "description": "LH task name, e.g. 'handover_block_bicoord'"},
            "latest": {"type": "boolean",
                         "description": "True (default) = latest report; False = list all"},
        }, "required": ["task"]},
    }},
    {"type": "function", "function": {
        "name": "list_atomic_frames",
        "description": ("List recent JPG frames captured in an atomic skill's workdir "
                         "under /tmp/roborsi-zeroshot/<atomic>/. Returns paths you "
                         "can then pass to view_frame to see the actual scene."),
        "parameters": {"type": "object", "properties": {
            "atomic": {"type": "string",
                         "description": "Atomic name prefix, e.g. 'pick_bowl_bicoord'"},
            "n": {"type": "integer", "description": "Max frames (default 8)"},
        }, "required": ["atomic"]},
    }},
    {"type": "function", "function": {
        "name": "read_file",
        "description": ("Read any file in the project or visible run "
                         "artefact paths. Use this to inspect: base-skill source "
                         "(_lib/orchestrate, sim/robotwin/adapter.py, "
                         "robotwin_agent.py), shared planning libs, and raw run "
                         "logs. Simulator task definitions and hidden success "
                         "criteria are intentionally outside this tool. "
                         "read_skill_code only covers SKILL "
                         "policy.py files — this covers everything else."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string",
                       "description": ("Absolute path under the RoboRSI repository, "
                                       "/tmp/agent_loop, /tmp/roborsi-zeroshot, "
                                       "/tmp/roborsi-long-horizon, ~/.roborsi")},
            "offset": {"type": "integer", "description": "Line offset (default 0)"},
            "limit": {"type": "integer", "description": "Max lines (default 200)"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List a directory (one level). Same path safelist as read_file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "grep_repo",
        "description": ("Ripgrep-style search across the RoboRSI repository. "
                         "Use to find where a primitive is implemented, where a "
                         "tool is dispatched, how a base skill that you want to "
                         "model your new one after is structured."),
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Regex"},
            "glob": {"type": "string",
                       "description": ("Path glob; default '**/*.py'. Examples: "
                                       "'**/*.md', 'embodied/skills/base/**/policy.py'")},
            "root": {"type": "string",
                       "description": "'repo' (the RoboRSI checkout)"},
        }, "required": ["pattern"]},
    }},
    {"type": "function", "function": {
        "name": "run_python",
        "description": ("Execute a short Python snippet for ad-hoc analysis. "
                         "Repo root is on sys.path. Use this when no existing "
                         "tool fits the question — e.g. cross-aggregate sqlite "
                         "trace_db, parse a JSON report, compute statistics, "
                         "inspect a runtime helper's actual behaviour on a "
                         "synthetic input. Use print() to surface results. "
                         "Output capped at 6 KB. Timeout 25 s."),
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "Python source"},
        }, "required": ["code"]},
    }},
    {"type": "function", "function": {
        "name": "git_log",
        "description": ("Show recent git commits on the repo. Use this to see "
                         "what fixes / proposals have actually landed — "
                         "especially after the queue archive change, this is "
                         "the source of record for 'was my prior fix applied?'."),
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "Default 10"},
            "path": {"type": "string", "description": "Optional path filter"},
        }},
    }},
    {"type": "function", "function": {
        "name": "git_show",
        "description": ("Show a commit's diff (or a specific file in it). Use "
                         "after git_log to see exactly what a prior fix changed."),
        "parameters": {"type": "object", "properties": {
            "sha": {"type": "string"},
            "path": {"type": "string", "description": "Optional path inside commit"},
        }, "required": ["sha"]},
    }},
    {"type": "function", "function": {
        "name": "get_inner_trace",
        "description": ("Return the inner sim-VLM's per-step tool calls for a "
                         "run_id: [(idx, tool, args, ok, preview), ...]. THE CHECK "
                         "TO DO when judge reason and outcome disagree: e.g. "
                         "outcome=never_attempted_grasp but judge says 'gripper "
                         "appears grasping bowl' — that contradiction means the "
                         "structural gate (in _lib/evaluation/trace_inspect.py) "
                         "is mis-firing. Read the inner trace, see what tools VLM "
                         "actually called, then read_file the gate logic to find "
                         "the bug. Don't anchor on outcome alone."),
        "parameters": {"type": "object", "properties": {
            "run_id": {"type": "string",
                         "description": ("For LH atomics use "
                                         "get_lh_report.trace[i].atomic_result."
                                         "inner_run_id (the trace-db id). For "
                                         "stand-alone atomic runs use the run_id "
                                         "from recent_runs. Returns inner-layer "
                                         "steps tagged with this id.")},
            "tool_filter": {"type": "string",
                              "description": ("Optional substring to filter tool "
                                              "names (e.g. 'pick_bowl' or 'verify')")},
            "limit": {"type": "integer", "description": "Max steps (default 60)"},
        }, "required": ["run_id"]},
    }},
    {"type": "function", "function": {
        "name": "get_sim_debug",
        "description": ("PHYSICAL diagnostics: tail sim/grasp DBG lines from the "
                         "current run log. These show the REAL motion-planning "
                         "failure mode (e.g. 'IK-fail(grasp): cuRobo plan status = "
                         "Fail', 'GraspGen returned N candidates, all rejected') "
                         "that judge reasons hide. USE THIS before deciding the "
                         "failure is a VLM-prompt issue vs a base-skill / geometry "
                         "issue. If you see repeated IK-fail or GraspGen-reject, "
                         "the fix is a new base skill, not a prompt tweak."),
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string",
                          "description": ("Regex to grep (default 'DBG|IK-fail|"
                                          "GraspGen|approach_used|cuRobo')")},
            "last_n": {"type": "integer", "description": "Max lines (default 80)"},
            "log_path": {"type": "string",
                           "description": ("Path to log; default scans "
                                           "/tmp/agent_loop/*.log newest-first")},
        }},
    }},
    {"type": "function", "function": {
        "name": "get_failure_patterns",
        "description": ("GLOBAL view: aggregate the last N long-horizon reports "
                         "(across all seeds, across all LH tasks if task omitted) "
                         "for a given atomic, returning frequency of outcomes and "
                         "judge reasons. Use this to PROVE a failure is SYSTEMATIC "
                         "(same reason 5/10 times → base-skill bug) vs STOCHASTIC "
                         "(varied reasons → seed noise). Required justification "
                         "before propose_new_skill for a motion primitive."),
        "parameters": {"type": "object", "properties": {
            "atomic": {"type": "string",
                         "description": "Atomic skill name, e.g. 'pick_bowl_bicoord'"},
            "task": {"type": "string",
                       "description": ("Optional LH task filter; omit to scan ALL "
                                       "LH tasks containing this atomic")},
            "last_n": {"type": "integer",
                         "description": "Reports to scan (default 20)"},
        }, "required": ["atomic"]},
    }},
    {"type": "function", "function": {
        "name": "view_frame",
        "description": ("Send a sim frame to a vision model and get back a natural-language "
                         "description grounded by your question. Use this to verify scene "
                         "state, diagnose geometry failures (e.g. 'is the gripper inside the "
                         "bowl?'), or confirm a judge's reasoning. Frame paths come from "
                         "list_atomic_frames or get_lh_report."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Absolute path to JPG/PNG"},
            "question": {"type": "string",
                          "description": ("Specific question, e.g. 'Is the right gripper "
                                          "holding the silver bowl? Describe finger position "
                                          "relative to bowl rim.'")},
        }, "required": ["path", "question"]},
    }},
    {"type": "function", "function": {
        "name": "read_recent_reflections",
        "description": ("Read the last N harness-generated reflections from "
                         "~/.roborsi/reflections.jsonl. Each reflection is "
                         "a JSON object with fields: wasted_hops, what_worked, "
                         "next_turn, missing — produced by the harness AFTER "
                         "each prior turn (not by you). Call this at the START "
                         "of every turn to avoid repeating mistakes (e.g. "
                         "burning hops on truncated reads, redundant trace.db "
                         "queries, dead-end paths). Default n=5."),
        "parameters": {"type": "object",
            "properties": {"n": {"type": "integer", "default": 5,
                                   "description": "How many recent reflections (1-20)."}},
            "required": []},
    }},
]
def _send_to_chat(target_chat_id: str | None, card_or_text,
                    channel=None, ctx=None) -> None:
    """Channel-agnostic outbound helper.

    Prefer ``channel`` if given (cli, web, future channels); otherwise
    fall back to direct Feishu API call so the existing WebSocket bot
    keeps working unchanged."""
    if channel is not None and ctx is not None:
        if isinstance(card_or_text, str):
            channel.send_text(ctx, card_or_text)
        else:
            channel.send_card(ctx, card_or_text)
        return
    if not target_chat_id:
        return
    from roborsi.channels.agent.feishu.bot_server import _send_card, _send_text
    if isinstance(card_or_text, str):
        _send_text(target_chat_id, card_or_text)
    else:
        _send_card(target_chat_id, card_or_text)


def _exec_tool(name: str, args: dict, target_chat_id: str | None,
                channel=None, ctx=None) -> str:
    from roborsi.embodied.skills import discover, get
    from roborsi.channels.agent.feishu.task_runner import render_demo_video
    from roborsi.store import trace_db as _td
    from roborsi.channels.agent.feishu.feishu_upload import _public_base_url
    if name == "list_skills":
        cat = (args.get("category") or "").strip().lower()
        kind = (args.get("kind") or "").strip().lower()
        nc = (args.get("name_contains") or "").strip().lower()
        items = []
        for sk in discover():
            fm = sk.frontmatter or {}
            sk_kind = str(fm.get("kind") or "").lower()
            sk_cat = str(fm.get("category") or "").lower()
            sk_name = sk.name.lower()
            if cat and cat not in sk_cat: continue
            if kind and kind != sk_kind: continue
            if nc and nc not in sk_name: continue
            runnable = (sk.path.parent / "policy.py").exists()
            items.append({
                "name": sk.name,
                "kind": fm.get("kind", "?"),
                "category": fm.get("category", "?"),
                "runnable": runnable,
                "description": (str(fm.get("description") or "")
                                .replace("\n", " ").strip()[:200]),
            })
        return json.dumps({
            "count": len(items),
            "note": ("`runnable: false` = directory-level SKILL.md doc only. "
                      "To execute, look for a child like `<name>.zeroshot` or "
                      "`<name>.execute`."),
            "skills": items[:80],
        }, ensure_ascii=False)
    if name == "read_skill":
        nm = (args.get("name") or "").strip()
        sk = get(nm)
        if sk is None:
            return f"ERROR: skill '{nm}' not found"
        return sk.path.read_text(encoding="utf-8")[:8000]
    if name == "run_skill":
        skill_name = (args.get("name") or "").strip()
        # Anti-cheat (no_sim_cheating): an agent must SOLVE the task via the VLM
        # rollout (<task>.zeroshot / .execute), never bypass it by invoking the
        # scripted expert or a lifecycle/collection pipeline skill. expert_replay
        # plays the canned expert and reports success=True — that is sim cheating,
        # not an agent solve (caught red-handed on stack_bowls_bicoord 2026-06-24:
        # zeroshot was unavailable, the agent run_skill'd expert_replay and the
        # loop scored a fake GENUINELY COMPLETE). Refuse them.
        _CHEAT_SKILLS = {"expert_replay", "success_rate", "rollout_vlm", "skill_mint"}
        if skill_name in _CHEAT_SKILLS or skill_name.rsplit(".", 1)[-1] in _CHEAT_SKILLS:
            return (f"REFUSED: '{skill_name}' replays the scripted expert or runs a "
                    f"lifecycle-eval pipeline. Invoking it to claim success bypasses "
                    f"the VLM and is sim cheating. Solve the task yourself via "
                    f"<task>.zeroshot (or fix the zeroshot recipe) — never expert_replay "
                    f"your way to a green check.")
        sk_args = args.get("args") or {}
        from roborsi.embodied.skills import run as run_skill_fn
        # Atomic task = a runnable .zeroshot/.execute child of an atomic.
        # We detect by the SKILL.md frontmatter kind="atomic_subskill" + suffix.
        is_atomic = (skill_name.endswith(".zeroshot")
                       or skill_name.endswith(".execute"))
        if is_atomic:
            from roborsi.channels.agent.feishu.task_runner import run_task_sync, render_demo_video
            from roborsi.channels.agent.feishu.feishu_upload import _public_base_url
            task = skill_name.rsplit(".", 1)[0]
            # Push live-monitor card NOW — user can watch/interrupt the task.
            if target_chat_id:
                live_url = f"{_public_base_url()}/live/{target_chat_id}"
                _send_to_chat(target_chat_id, {
                    "header": {"title": {"tag": "plain_text",
                        "content": f"🚀 开始执行: {task}"}, "template": "blue"},
                    "elements": [
                        {"tag": "div", "text": {"tag": "lark_md", "content": (
                            f"**正在跑** `{skill_name}` (~30-90 秒)\n"
                            f"实时监控 + 中断按钮: [{live_url}]({live_url})\n"
                            f"完成后会自动推送 demo 视频。")}},
                        {"tag": "action", "actions": [{
                            "tag": "button",
                            "text": {"tag": "plain_text",
                                      "content": "🌐 打开监控页"},
                            "type": "primary", "url": live_url}]},
                    ]}, channel=channel, ctx=ctx)
            st = run_task_sync(task=task,
                                seed=int(sk_args.get("seed_start", 0)),
                                episodes=int(sk_args.get("episodes", 1)),
                                tool_budget=int(sk_args.get("tool_budget", 12)),
                                skill_name=skill_name,
                                chat_id=target_chat_id)
            if target_chat_id:
                video = render_demo_video(st.get("run_id"), camera="head_camera")
                # Channel-aware result push.
                if channel is not None and ctx is not None:
                    status_sym = "✓" if st.get("status") == "success" else "✗"
                    _send_to_chat(target_chat_id,
                        f"{status_sym} {task}: {st.get('summary','?')}",
                        channel=channel, ctx=ctx)
                    if video and video.exists():
                        channel.upload_file(ctx, video)
                else:
                    from roborsi.channels.agent.feishu.feishu_upload import (push_task_result_to_chat,
                                                  push_failure_alert)
                    push_task_result_to_chat(target_chat_id, st, video_path=video)
                    if st.get("status") in ("failed", "error"):
                        push_failure_alert(target_chat_id, st)
                # Also surface result in the live monitor (same chat session).
                from roborsi.channels.agent.feishu.live_trace import get_session
                sess2 = get_session(target_chat_id)
                sess2.append("task_result",
                              status=st.get("status"),
                              outcome=st.get("outcome"),
                              summary=st.get("summary"),
                              run_id=st.get("run_id"),
                              video_path=str(video) if video else None,
                              task=task,
                              skill=skill_name)
            ret = {
                "run_id": st.get("run_id"), "status": st.get("status"),
                "outcome": st.get("outcome"), "summary": st.get("summary"),
                "episode_summary": st.get("episode_summary"),
                "note": "Result card + demo already pushed to chat & monitor.",
            }
            return json.dumps(ret, ensure_ascii=False, default=str)[:6000]
        try:
            result = run_skill_fn(skill_name, **sk_args)
            return json.dumps(result, ensure_ascii=False, default=str)[:4000]
        except Exception as e:
            return f"ERROR running {skill_name}: {type(e).__name__}: {e}"
    if name == "recent_runs":
        runs = _td.list_runs(limit=10)
        return json.dumps([
            {"run_id": r.get("id"), "task": r.get("task"),
              "status": r.get("status"), "outcome": r.get("outcome"),
              "started": r.get("started_at"), "summary": r.get("summary")}
            for r in runs], ensure_ascii=False)
    if name == "get_status":
        rid = (args.get("run_id") or "").strip()
        st = _td.get_run(rid)
        if not st: return f"run {rid!r} not found"
        return json.dumps(st, ensure_ascii=False, default=str)[:3000]
    if name == "render_demo":
        rid = (args.get("run_id") or "").strip()
        cam = args.get("camera", "head_camera")
        mp4 = render_demo_video(rid, camera=cam)
        if mp4 and mp4.exists():
            return json.dumps({"mp4_path": str(mp4),
                                "size_kb": mp4.stat().st_size // 1024}, ensure_ascii=False)
        return f"ERROR: could not render demo for {rid}"
    # ── self-improvement tools ──
    if name == "read_skill_code":
        nm = (args.get("name") or "").strip()
        sk = get(nm)
        if sk is None:
            return f"ERROR: skill '{nm}' not found"
        py = sk.path.parent / "policy.py"
        if not py.exists():
            return f"NOTE: '{nm}' has no policy.py (SKILL.md-only skill)"
        return py.read_text(encoding="utf-8")[:8000]
    if name == "propose_new_skill":
        return _enqueue_proposal(kind="new", **{
            k: args.get(k) for k in
            ("name", "category", "description", "code", "skill_md", "rationale")})
    if name == "propose_skill_update":
        return _enqueue_proposal(kind="update", **{
            k: args.get(k) for k in ("name", "new_code", "rationale")})
    if name == "get_lh_report":
        return _get_lh_report(args.get("task"), bool(args.get("latest", True)))
    if name == "list_atomic_frames":
        return _list_atomic_frames(args.get("atomic"), int(args.get("n") or 8))
    if name == "view_frame":
        return _view_frame(args.get("path"), args.get("question"))
    if name == "read_recent_reflections":
        return _read_recent_reflections(int(args.get("n") or 5))
    if name == "get_failure_patterns":
        return _get_failure_patterns(args.get("atomic"), args.get("task"),
                                       int(args.get("last_n") or 20))
    if name == "get_sim_debug":
        return _get_sim_debug(args.get("pattern"), args.get("log_path"),
                                int(args.get("last_n") or 80))
    if name == "get_inner_trace":
        return _get_inner_trace(args.get("run_id"),
                                  args.get("tool_filter"),
                                  int(args.get("limit") or 60))
    if name == "run_python":
        return _run_python(args.get("code"))
    if name == "git_log":
        return _git_log(int(args.get("limit") or 10), args.get("path"))
    if name == "git_show":
        return _git_show(args.get("sha"), args.get("path"))
    if name == "read_file":
        return _read_file(args.get("path"),
                            int(args.get("offset") or 0),
                            int(args.get("limit") or 200))
    if name == "list_dir":
        return _list_dir(args.get("path"))
    if name == "grep_repo":
        return _grep_repo(args.get("pattern"), args.get("glob") or "**/*.py",
                            args.get("root") or "repo")
    if name == "list_pending_proposals":
        from roborsi.embodied.skills._lib.human_review.skill_review import list_pending  # type: ignore
        try:
            pend = list_pending()
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"
        return json.dumps([
            {"id": p.get("id"), "name": p.get("name"),
              "task_name": p.get("task_name"),
              "submitted_at": p.get("submitted_at"),
              "docstring": (p.get("docstring") or "")[:120]}
            for p in pend], ensure_ascii=False)
    return f"ERROR: unknown tool {name}"


def _enqueue_proposal(kind: str, **fields) -> str:
    """Write proposal to ~/.roborsi/skill_review/<id>.json for HTML review,
    AND mirror into the sqlite proposals table."""
    import time as _t
    import uuid as _u
    from pathlib import Path as _P
    queue = _P.home() / ".roborsi" / "skill_review"
    queue.mkdir(parents=True, exist_ok=True)
    pid = f"{int(_t.time())}-{kind}-{(fields.get('name') or 'unnamed')}-{_u.uuid4().hex[:6]}"
    data = {
        "id": pid, "kind": kind,
        "name": fields.get("name"),
        "submitted_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "pending",
        "submitted_by": "feishu_bot",
        **fields,
    }
    (queue / f"{pid}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from roborsi.store import trace_db as _td
        from roborsi.channels.agent.feishu.live_trace import get_inner_run_id as _gri
        _td.record_proposal(
            skill=fields.get("name") or "unnamed",
            kind=kind,
            diff=fields.get("patch") or fields.get("diff"),
            rationale=fields.get("rationale") or fields.get("docstring"),
            file_path=fields.get("file_path"),
            run_id=_gri())
    except Exception:
        pass
    return json.dumps({
        "proposal_id": pid,
        "queue": str(queue / f"{pid}.json"),
        "review_url": (f"{os.environ.get('ROBORSI_MONITOR_URL', 'http://localhost:8770')}"
                        f"/skills"),
        "note": ("Proposal queued. NOT applied yet — a human must approve "
                  "via the HTML review UI or /approve command."),
    }, ensure_ascii=False)


def _get_lh_report(task: str | None, latest: bool) -> str:
    """Return the most recent (or list of) long-horizon eval report(s)
    for a task. Reports live at ~/.roborsi/long_horizon_evals/<task>/."""
    from pathlib import Path as _P
    if not task:
        return "ERROR: task required"
    root = _P.home() / ".roborsi" / "long_horizon_evals" / task
    if not root.exists():
        return f"NOTE: no reports for {task} (dir {root} missing)"
    reports = sorted(root.glob("*.json"))
    if not reports:
        return f"NOTE: no reports for {task}"
    if not latest:
        return json.dumps([p.name for p in reports[-20:]], ensure_ascii=False)
    rep = json.loads(reports[-1].read_text(encoding="utf-8"))
    digest = {
        "report": reports[-1].name,
        "task": rep.get("long_horizon_task"),
        "seed": rep.get("seed"),
        "success": rep.get("success"),
        "outcome": rep.get("outcome"),
        "plan": [{"i": i, "atomic": s.get("skill"), "why": s.get("why")}
                  for i, s in enumerate(rep.get("plan", {}).get("steps", []))],
        "atomics": [],
    }
    for t in rep.get("trace", []):
        digest["atomics"].append({
            "i": t.get("index"), "atomic": t.get("atomic"),
            "success": t.get("atomic_success"),
            "outcome": (t.get("atomic_result") or {}).get("outcome"),
            "tool_calls": (t.get("atomic_result") or {}).get("tool_calls"),
            "wall_s": t.get("wall_time_s"),
            "atomic_judge": t.get("atomic_judge", {}).get("reason"),
            "progress_judge": (t.get("judge") or {}).get("reason"),
            "pre_image": t.get("pre_image"),
            "post_image": t.get("post_image"),
        })
    return json.dumps(digest, ensure_ascii=False, default=str)[:8000]


def _list_atomic_frames(atomic: str | None, n: int) -> str:
    """Return recent JPG frames from /tmp/roborsi-zeroshot/<atomic>* dirs.
    These are the per-tick or per-tool-call captures the skill recorded."""
    from pathlib import Path as _P
    if not atomic:
        return "ERROR: atomic required"
    root = _P("/tmp/roborsi-zeroshot")
    if not root.exists():
        return "NOTE: no zeroshot workdir on this host"
    dirs = sorted(d for d in root.iterdir()
                    if d.is_dir() and d.name.startswith(atomic))
    if not dirs:
        return f"NOTE: no workdirs match {atomic}*"
    out = []
    for d in dirs[-3:]:
        frames = sorted(d.glob("*.jpg"),
                          key=lambda p: p.stat().st_mtime)[-n:]
        out.append({
            "workdir": str(d),
            "frame_count": len(list(d.glob('*.jpg'))),
            "recent_frames": [str(f) for f in frames],
        })
    return json.dumps(out, ensure_ascii=False)


    return json.dumps({"frame": str(p), "answer": text}, ensure_ascii=False)[:4000]


def _get_failure_patterns(atomic: str | None, task: str | None, last_n: int) -> str:
    """Aggregate judge reasons + outcomes for a given atomic across recent
    LH reports. The agent uses this to prove a failure is SYSTEMATIC."""
    from pathlib import Path as _P
    from collections import Counter
    if not atomic:
        return "ERROR: atomic required"
    root = _P.home() / ".roborsi" / "long_horizon_evals"
    if not root.exists():
        return "NOTE: no LH eval dir"
    task_dirs = ([root / task] if task
                   else [d for d in root.iterdir() if d.is_dir()])
    reports = []
    for td in task_dirs:
        if td.exists():
            reports.extend(sorted(td.glob("*.json"))[-last_n:])
    reports = sorted(reports, key=lambda p: p.stat().st_mtime)[-last_n:]
    outcomes = Counter()
    judge_reasons = Counter()
    total_attempts = 0
    successes = 0
    examples: list[dict] = []
    for rp in reports:
        try:
            rep = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for t in rep.get("trace", []):
            if t.get("atomic") != atomic:
                continue
            total_attempts += 1
            ok = t.get("atomic_success")
            if ok:
                successes += 1
                continue
            out = (t.get("atomic_result") or {}).get("outcome") or "unknown"
            reason = (t.get("atomic_judge") or {}).get("reason") or "(no judge reason)"
            outcomes[out] += 1
            judge_reasons[reason[:140]] += 1
            if len(examples) < 3:
                examples.append({
                    "report": rp.name, "seed": rep.get("seed"),
                    "outcome": out, "judge_reason": reason,
                    "post_image": t.get("post_image"),
                })
    if total_attempts == 0:
        return f"NOTE: no traces for atomic={atomic} in scanned reports"
    summary = {
        "atomic": atomic, "task_filter": task or "(all LH tasks)",
        "reports_scanned": len(reports),
        "total_attempts": total_attempts, "successes": successes,
        "success_rate": round(successes / total_attempts, 3),
        "outcome_freq": outcomes.most_common(),
        "judge_reason_freq": judge_reasons.most_common(5),
        "verdict": ("SYSTEMATIC" if judge_reasons
                     and judge_reasons.most_common(1)[0][1] / (total_attempts - successes or 1) >= 0.5
                     else "MIXED/STOCHASTIC"),
        "examples": examples,
    }
    return json.dumps(summary, ensure_ascii=False, default=str)[:6000]


_FS_ALLOWED_ROOTS = (
    _REPO_ROOT,
    "/tmp/agent_loop",
    "/tmp/roborsi-zeroshot",
    "/tmp/roborsi-long-horizon",
    str(__import__("pathlib").Path.home() / ".roborsi"),
)


def _fs_allowed(path: str) -> bool:
    from pathlib import Path as _P
    p = str(_P(path).expanduser().resolve())
    return any(p == r or p.startswith(r + "/") for r in _FS_ALLOWED_ROOTS)


def _read_file(path: str | None, offset: int, limit: int) -> str:
    from pathlib import Path as _P
    if not path:
        return "ERROR: path required"
    if not _fs_allowed(path):
        return f"ERROR: path outside allowed roots: {_FS_ALLOWED_ROOTS}"
    p = _P(path).expanduser()
    if not p.exists():
        return f"ERROR: not found: {path}"
    if p.is_dir():
        return f"ERROR: is directory (use list_dir): {path}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    chunk = lines[offset: offset + limit]
    numbered = "\n".join(f"{offset+i+1:6}  {l}" for i, l in enumerate(chunk))
    return json.dumps({
        "path": str(p), "total_lines": len(lines),
        "offset": offset, "shown": len(chunk),
        "content": numbered[:7500],
    }, ensure_ascii=False)


def _list_dir(path: str | None) -> str:
    from pathlib import Path as _P
    if not path:
        return "ERROR: path required"
    if not _fs_allowed(path):
        return "ERROR: path outside allowed roots"
    p = _P(path).expanduser()
    if not p.exists() or not p.is_dir():
        return f"ERROR: not a directory: {path}"
    entries = []
    for child in sorted(p.iterdir()):
        entries.append(("dir" if child.is_dir() else "file",
                          child.name,
                          child.stat().st_size if child.is_file() else None))
    return json.dumps({"path": str(p), "entries": entries[:200]},
                        ensure_ascii=False)


def _grep_repo(pattern: str | None, glob: str, root: str) -> str:
    import subprocess
    if not pattern:
        return "ERROR: pattern required"
    roots = {"repo": _REPO_ROOT}
    base = roots.get(root)
    if not base:
        return f"ERROR: root must be one of {list(roots)}"
    cmd = ["grep", "-rnE", "--include", glob.split("/")[-1],
           "--max-count=5", pattern, base]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                            encoding="utf-8", errors="replace")
    out = (res.stdout or "").splitlines()[:100]
    return json.dumps({"pattern": pattern, "include": glob.split("/")[-1],
                        "root": base, "matches": len(out), "lines": out},
                        ensure_ascii=False)[:6500]




def _get_inner_trace(run_id: str | None, tool_filter: str | None,
                       limit: int) -> str:
    """Read per-step inner-sim VLM tool calls from the sqlite trace_db.
    Pair adjacent (call, result) rows — schema writes them as two rows
    with the same idx (args set on the call row, result_ok set on the
    result row)."""
    import sqlite3
    from pathlib import Path as _P
    if not run_id:
        return "ERROR: run_id required"
    db_path = _P.home() / ".roborsi" / "trace.db"
    if not db_path.exists():
        return "ERROR: trace.db not found"
    db = sqlite3.connect(str(db_path))
    rows = db.execute(
        "SELECT idx, tool, args_json, result_ok, result_preview FROM steps "
        "WHERE run_id=? AND layer='inner' ORDER BY id", (run_id,)).fetchall()
    pairs: dict[tuple[int, str], dict] = {}
    for idx, tool, args_json, ok, prev in rows:
        key = (idx, tool)
        slot = pairs.setdefault(key, {"idx": idx, "tool": tool,
                                          "args": None, "ok": None, "preview": None})
        if args_json is not None:
            slot["args"] = args_json[:140]
        if ok is not None:
            slot["ok"] = ok
        if prev is not None:
            slot["preview"] = prev[:180]
    items = sorted(pairs.values(), key=lambda d: (d["idx"], d["tool"]))
    if tool_filter:
        items = [it for it in items if tool_filter in (it["tool"] or "")]
    return json.dumps({
        "run_id": run_id, "total_steps": len(items),
        "filter": tool_filter, "steps": items[:limit],
    }, ensure_ascii=False, default=str)[:7500]




def _run_python(code: str | None) -> str:
    """Ad-hoc python evaluator. Repo on sys.path, captures stdout."""
    import io
    import contextlib
    import subprocess
    if not code:
        return "ERROR: code required"
    # Subprocess for isolation + clean timeout + no namespace pollution.
    cmd = ["python3", "-c",
           f"import sys; sys.path.insert(0, {_REPO_ROOT!r})\n" + code]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=25,
                            cwd=_REPO_ROOT, encoding="utf-8",
                            errors="replace")
    return json.dumps({
        "returncode": res.returncode,
        "stdout": (res.stdout or "")[:5500],
        "stderr": (res.stderr or "")[:1500],
    }, ensure_ascii=False)


def _git_log(limit: int, path: str | None) -> str:
    import subprocess
    cmd = ["git", "log", f"-n{limit}", "--oneline",
           "--date=short", "--pretty=format:%h | %ad | %s"]
    if path:
        cmd += ["--", path]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                            cwd=_REPO_ROOT, encoding="utf-8",
                            errors="replace")
    return json.dumps({"limit": limit, "path": path,
                        "lines": (res.stdout or res.stderr).splitlines()[:60]},
                        ensure_ascii=False)[:6000]


def _git_show(sha: str | None, path: str | None) -> str:
    import subprocess
    if not sha:
        return "ERROR: sha required"
    cmd = ["git", "show", "--stat", "--patch", sha]
    if path:
        cmd += ["--", path]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                            cwd=_REPO_ROOT, encoding="utf-8",
                            errors="replace")
    out = (res.stdout or res.stderr)[:6500]
    return json.dumps({"sha": sha, "path": path, "diff": out},
                        ensure_ascii=False)[:7000]


def _get_sim_debug(pattern: str | None, log_path: str | None, last_n: int) -> str:
    """Grep DBG / IK-fail / GraspGen lines from the current run log.
    Surfaces the physical failure mode (motion planning, grasp candidates)
    that judge reasons hide. Defaults: newest /tmp/agent_loop/*.log."""
    import re
    from pathlib import Path as _P
    pat = re.compile(pattern or r"DBG|IK-fail|GraspGen|approach_used|cuRobo")
    if log_path:
        candidates = [_P(log_path)]
    else:
        candidates = sorted(_P("/tmp/agent_loop").glob("*.log"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
    for lp in candidates:
        if not lp.exists():
            continue
        lines = lp.read_text(encoding="utf-8", errors="replace").splitlines()
        hits = [l for l in lines if pat.search(l)]
        if hits:
            return json.dumps({
                "log": str(lp), "total_lines": len(lines),
                "matches": len(hits), "tail": hits[-last_n:],
            }, ensure_ascii=False)[:6000]
    return json.dumps({"note": "no DBG lines matched in available logs",
                        "scanned": [str(p) for p in candidates]},
                        ensure_ascii=False)


def _view_frame(path: str | None, question: str | None) -> str:
    """VLM perception query on a single frame. Routes through the same
    _call_vlm_image used by find_pixel / verify_holding_visual so the
    description format is consistent with what the in-sim agent sees."""
    from pathlib import Path as _P
    if not path or not question:
        return "ERROR: path and question required"
    p = _P(path)
    if not p.exists():
        return f"ERROR: frame not found: {path}"
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_image
    sysprompt = ("You are inspecting a single frame from a robot manipulation "
                  "scene. Answer the user's question with concrete spatial detail: "
                  "object positions, gripper-object relationships, what is held / "
                  "lifted / blocked. Be specific. 3-6 sentences.")
    text = _call_vlm_image(DEFAULT_MODEL, sysprompt, question, p)
    return json.dumps({"frame": str(p), "answer": text}, ensure_ascii=False)[:4000]


def _build_skill_index() -> str:
    """Build a <skill_index> block listing every runnable skill organized
    by kind/category. The prompt is assembled from the active skill registry:
    show the agent EVERYTHING upfront so it doesn't waste tool calls
    discovering, and it learns the naming convention by seeing real names."""
    from roborsi.embodied.skills import discover
    by_kind: dict[str, list[tuple[str, str, bool]]] = {}
    for sk in discover():
        fm = sk.frontmatter or {}
        kind = str(fm.get("kind") or "other").lower()
        desc = (str(fm.get("description") or "").replace("\n", " ").strip()[:140])
        runnable = (sk.path.parent / "policy.py").exists()
        by_kind.setdefault(kind, []).append((sk.name, desc, runnable))
    lines = []
    for kind in sorted(by_kind):
        lines.append(f"  ### {kind}")
        for name, desc, runnable in sorted(by_kind[kind]):
            tag = "🚀" if runnable else "📄"   # 🚀 = directly invocable, 📄 = doc-only
            lines.append(f"  {tag} `{name}` — {desc}")
    return ("<skill_index>\n"
            "🚀 = directly invocable via run_skill(name)\n"
            "📄 = SKILL.md doc only — its children (e.g. `<name>.zeroshot`) are runnable\n\n"
            + "\n".join(lines) + "\n</skill_index>")


_CACHED_SKILL_INDEX: str | None = None


def _get_skill_index() -> str:
    """Cached — rebuilt only if skill registry changes (manual restart)."""
    global _CACHED_SKILL_INDEX
    if _CACHED_SKILL_INDEX is None:
        _CACHED_SKILL_INDEX = _build_skill_index()
    return _CACHED_SKILL_INDEX


# ────────────────────────────────────────────────────────────────────────
# 3-role atomic fast path (Planner → Engineer → Reviewer)
# ────────────────────────────────────────────────────────────────────────


def _detect_atomic_intent(text: str) -> tuple[str, int] | None:
    """Parse a user message; if it names a single registered atomic task,
    return (task_name, seed). Otherwise None — caller falls through to
    the legacy outer-Opus tool loop.

    Long-horizon tasks (.execute / known LH names) are intentionally NOT
    matched here. Phase 1 of the 3-role refactor is atomic-only."""
    import re
    from roborsi.embodied.skills import discover
    text_low = text.lower()
    # Long-horizon names: blocklist
    LH_BLOCK = {"handover_block_bicoord", "clean_table_bicoord",
                "collect_pens_bicoord", "match_blocks_bicoord",
                "stack_bowls_demo"}
    for name in LH_BLOCK:
        if name in text_low:
            return None
    if ".execute" in text_low or "long_horizon" in text_low:
        return None
    # Match against registered atomic .zeroshot names. We collect the
    # parent atomic name (e.g. "click_bell" from "click_bell.zeroshot").
    atomics: set[str] = set()
    for sk in discover():
        if sk.name.endswith(".zeroshot"):
            atomics.add(sk.name.removesuffix(".zeroshot"))
    hits = [name for name in atomics if name in text_low]
    if len(hits) != 1:
        return None
    atomic = hits[0]
    # Optional seed parse: "seed=N" or "seed N"
    m = re.search(r"seed[=\s]+(\d+)", text_low)
    seed = int(m.group(1)) if m else 0
    return atomic, seed


def _detect_lh_intent(text: str) -> tuple[str, int] | None:
    """Long-horizon counterpart of _detect_atomic_intent. Matches against
    registered .execute skills (long_horizon/<task>/execute/SKILL.md)
    or the static blocklist used above."""
    import re
    from roborsi.embodied.skills import discover
    text_low = text.lower()
    lh_tasks: set[str] = set()
    for sk in discover():
        if sk.name.endswith(".execute"):
            lh_tasks.add(sk.name.removesuffix(".execute"))
    # Also include the well-known set even if discover misses any.
    lh_tasks.update({"handover_block_bicoord", "clean_table_bicoord",
                      "collect_pens_bicoord", "match_blocks_bicoord",
                      "stack_bowls_demo"})
    hits = [name for name in lh_tasks if name in text_low]
    if len(hits) != 1:
        return None
    task = hits[0]
    m = re.search(r"seed[=\s]+(\d+)", text_low)
    seed = int(m.group(1)) if m else 0
    return task, seed


def _run_atomic_3role(*, text: str, atomic: str, seed: int,
                       sess, target_chat_id: str | None,
                       channel, ctx) -> str:
    """Run Planner → Engineer → Reviewer pipeline for one atomic.
    Returns the user-facing reply text."""
    from roborsi.agents import (
        Planner, Engineer, Reviewer, new_workspace,
    )
    from roborsi.channels.agent.feishu.task_runner import RUNS_DIR  # noqa: F401 — ensures dirs exist
    import time as _t

    sess.append("3role_start", atomic=atomic, seed=seed)
    workspace = new_workspace(atomic)
    sess.append("3role_workspace", path=str(workspace.root))

    # Resolve backend + sim task from the atomic's SKILL.md up front, so its
    # skill namespace can steer BOTH the Planner's skill catalog and the
    # Reviewer's proposal namespace. RoboTwin atomics → ("robotwin", <atomic>);
    # LIBERO atomics → ("libero-pro", <suite>/<id>).
    from roborsi.agents.atomic_backend import resolve as _resolve_atomic
    from roborsi.embodied.agent_loop.config import _skill_namespace
    ab = _resolve_atomic(atomic)
    ns = _skill_namespace(ab.backend_name)

    # 1. Planner writes plan.md
    reflections_text = _read_recent_reflections(n=5)
    planner = Planner()
    t0 = _t.time()
    print(f"[3role] 🧠 Planner planning {atomic} (ns={ns}) ...", flush=True)
    mission_spec = planner.plan(
        task=atomic, user_msg=text,
        recent_reflections=reflections_text, workspace=workspace, ns=ns,
    )
    print(f"[3role] 🧠 Planner goal: {str(mission_spec.get('goal',''))[:200]}",
          flush=True)
    for _i, _sg in enumerate(mission_spec.get("sub_goals", [])[:8]):
        print(f"[3role]      {_i+1}. {str(_sg)[:150]}", flush=True)
    sess.append("3role_planned", t=_t.time() - t0,
                 sub_goals=mission_spec.get("sub_goals", [])[:3])

    # 2. Engineer drives sim (owns env lifecycle) on the resolved backend/task.
    print(f"[3role] 🎛️  backend={ab.backend_name} sim_task={ab.sim_task}",
          flush=True)
    engineer = Engineer()
    t0 = _t.time()
    eng_result = engineer.execute(
        mission_spec=mission_spec, workspace=workspace,
        # 40 (was 24): multi-step precise atomics (adjust_bottle: place-down→
        # regrasp-from-top→stand→verify) spend ~half the budget on perception/IK
        # probing and never reach the action chain → budget_exceeded. Matches the
        # LH sub-atomic budget. NOTE: raises the ceiling, doesn't cure the churn.
        seed=seed, tool_budget=40,
        backend_name=ab.backend_name, sim_task=ab.sim_task,
    )
    sess.append("3role_executed", t=_t.time() - t0,
                 success=eng_result["success"],
                 outcome=eng_result["outcome"])

    # 2b. Persist a trace.db run row — the 3-role atomic path previously wrote
    # NOTHING to `runs` (only the old feishu run_task_sync + LH paths did), so
    # campaign success detection and the success-demo video had no record.
    # Success here is the SIM predicate (engineer runs with use_sim_predicate=True);
    # the demo mp4 is kept only on sim success by _finalize_demo_video.
    from roborsi.store import trace_db as _td
    _meta = eng_result.get("rollout_meta") or {}
    _td.insert_run(workspace.run_id, task=atomic, seed=seed,
                    model=_meta.get("model"))
    _td.update_run(
        workspace.run_id,
        status="success" if eng_result["success"] else "failed",
        outcome=eng_result["outcome"],
        video_path=_meta.get("demo_video"),
        finished_at=_t.strftime("%Y-%m-%d %H:%M:%S"),
        episode_summary={
            "vlm_declared": _meta.get("vlm_declared"),
            "predicate_check": _meta.get("predicate_check"),
            "tool_calls": eng_result.get("tool_calls"),
        },
    )

    # 3. Reviewer reads everything, writes review.md (+ optional proposal)
    reviewer = Reviewer()
    t0 = _t.time()
    print("[3role] 🔍 Reviewer reviewing the attempt ...", flush=True)
    review = reviewer.review(workspace=workspace,
                              engineer_result=eng_result,
                              run_id=None, ns=ns)
    sess.append("3role_reviewed", t=_t.time() - t0,
                 verdict=review.get("verdict"),
                 proposal=review.get("proposal_decision"))

    # 3b. Persist this attempt to the task wiki so the NEXT round's Planner
    # learns from it (closes the self-evo loop for atomics — previously only
    # the LH path recorded traces, so auto-authored atomic skills never matured
    # on strategy failures: the Reviewer's root_cause/next_action evaporated in
    # the per-run review.md and the Planner re-planned blind every round).
    from roborsi.agents.task_wiki import (
        append_success_trace, append_failure_trace, _enqueue_plan_promotion,
    )
    trace_events = [
        {"tool": e.get("tool", "?"), "args": e.get("args") or {}}
        for e in (eng_result.get("trace") or [])
    ]
    _tc_total = eng_result.get("tool_calls", len(trace_events))
    if eng_result["success"]:
        append_success_trace(
            task=atomic, atomic=atomic, seed=seed, run_id=workspace.run_id,
            tool_events=trace_events, tool_calls_total=_tc_total,
        )
        # Propose promoting this successful run's workspace plan into the
        # persistent (read-only) seed. Manager-gated via resolve_plan_promotion;
        # a no-op when the plan is identical to the seed. engineer_replanned
        # flags a mid-run plan() revision so the Manager can weigh whether the
        # promoted plan reflects the Planner's design or the Engineer's divergence.
        from roborsi.embodied.skills.base.plan.robotwin.policy import (
            get_active_plan,
        )
        _ap = get_active_plan()
        _enqueue_plan_promotion(
            task=atomic, run_id=workspace.run_id,
            workspace_plan_md=workspace.read_plan(),
            rationale=review.get("root_cause", "") or eng_result.get("outcome", ""),
            engineer_replanned=bool(_ap.get("is_revision")),
            reason_for_revision=_ap.get("reason_for_revision", "") or "",
        )
    else:
        append_failure_trace(
            task=atomic, atomic=atomic, seed=seed, run_id=workspace.run_id,
            tool_events=trace_events, tool_calls_total=_tc_total,
            reviewer_root_cause=review.get("root_cause", ""),
            reviewer_next_action=review.get("next_action", ""),
        )

    # 4. Compose user-facing reply
    badge = "✓" if eng_result["success"] else "✗"
    lines = [
        f"{badge} **{atomic}** seed={seed} · {eng_result['outcome']} "
        f"({eng_result['tool_calls']} tool calls)",
        "",
        f"Workspace: `{workspace.root}`",
        f"  plan.md · summary.md · review.md",
        "",
        f"Reviewer verdict: `{review.get('verdict')}` · "
        f"proposal: `{review.get('proposal_decision')}`",
    ]
    if review.get("proposal_id"):
        lines.append(f"Proposal: `{review['proposal_id']}`")
        if review.get("html_review_path"):
            lines.append(f"HTML review: `{review['html_review_path']}`")
    if review.get("next_action"):
        lines.append("")
        lines.append(f"Next: {review['next_action']}")
    return "\n".join(lines)


def _post_3role(sess, final_text: str, target_chat_id: str | None) -> None:
    """Run the standard finally-block work after the 3-role path:
    set_busy(False), persist final_text, run _harness_reflect so the
    next turn's Planner sees this reflection."""
    sess.set_busy(False)
    sess.append("done", final_text=final_text)
    try:
        # Reuse the existing harness reflection so reflections.jsonl
        # keeps gaining one row per turn regardless of path.
        from roborsi.embodied.agent_loop.config import DEFAULT_MODEL  # noqa
        # Minimal messages list — reflection only needs final_text + ctx
        fake_msgs = [
            {"role": "user", "content": sess.last_user_message or ""},
            {"role": "assistant", "content": final_text},
        ]
        r = _harness_reflect(fake_msgs, final_text, target_chat_id)
        sess.append("harness_reflection", text=r[:500])
    except Exception as e:
        sess.append("harness_reflection_error", text=str(e))


def _run_lh_3role(*, text: str, lh_task: str, seed: int,
                   sess, target_chat_id: str | None,
                   channel, ctx) -> str:
    """Run Planner.decompose → LHExecutor (sustained Engineer+Reviewer) →
    Reviewer.review_lh for one long-horizon task. Returns the user-facing
    reply. LH reuses the SAME Planner/Reviewer classes as the atomic path —
    there are no separate LHPlanner/LHReviewer classes."""
    from roborsi.agents import (
        Planner, Reviewer, LHExecutor, new_workspace,
    )
    import time as _t

    sess.append("lh3role_start", lh_task=lh_task, seed=seed)
    workspace = new_workspace(lh_task)
    sess.append("lh3role_workspace", path=str(workspace.root))

    # 1. Planner.decompose — decompose the LH into ordered atomics
    reflections_text = _read_recent_reflections(n=5)
    planner = Planner()
    t0 = _t.time()
    mission_spec = planner.decompose(
        lh_task=lh_task, user_msg=text,
        recent_reflections=reflections_text, workspace=workspace,
    )
    sess.append("lh3role_planned", t=_t.time() - t0,
                 atomics=[a.get("atomic")
                           for a in mission_spec.get("ordered_atomics", [])])

    # 2. LHExecutor — sustained Engineer + per-atomic Reviewer
    executor = LHExecutor()
    t0 = _t.time()
    lh_result = executor.execute(
        mission_spec=mission_spec, workspace=workspace, seed=seed,
    )
    sess.append("lh3role_executed", t=_t.time() - t0,
                 success=lh_result.success,
                 completed=f"{lh_result.completed_atomics}/{lh_result.total_atomics}")

    # 3. Reviewer.review_lh — overall verdict + optional propose
    final_reviewer = Reviewer()
    t0 = _t.time()
    review = final_reviewer.review_lh(workspace=workspace, lh_result=lh_result)
    sess.append("lh3role_reviewed", t=_t.time() - t0,
                 verdict=review.get("lh_verdict"),
                 proposal=review.get("proposal_decision"))

    # User-facing reply
    badge = "✓" if lh_result.success else "✗"
    lines = [
        f"{badge} **{lh_task}** seed={seed} · "
        f"{lh_result.completed_atomics}/{lh_result.total_atomics} atomics complete",
        "",
        f"Workspace: `{workspace.root}`",
        f"  lh_plan.md · lh_summary.md · lh_review.md",
        "",
        f"LH review verdict: `{review.get('lh_verdict')}` · "
        f"proposal: `{review.get('proposal_decision')}`",
    ]
    if lh_result.notes:
        lines.append(f"Notes: {lh_result.notes}")
    if review.get("proposal_id"):
        lines.append(f"Proposal: `{review['proposal_id']}`")
        if review.get("html_review_path"):
            lines.append(f"HTML review: `{review['html_review_path']}`")
        if review.get("auto_apply_status"):
            lines.append(f"Auto-apply: {review['auto_apply_status']}")
    if review.get("next_action"):
        lines.append("")
        lines.append(f"Next: {review['next_action']}")
    return "\n".join(lines)


def handle_user_message(text: str, target_chat_id: str | None = None,
                          channel: "Channel | None" = None,
                          ctx: "ChannelCtx | None" = None,
                          history: list | None = None,
                          max_hops: int = 15) -> str:
    """Run an Opus agent loop on the user's message. Returns the final
    text reply.

    Three back-ends, in priority order:
      1. ``channel`` + ``ctx`` (preferred)  — channel-agnostic. The agent
         relays cards/uploads via channel methods; the channel decides
         platform rendering.
      2. ``target_chat_id`` only  — legacy direct-feishu path used by the
         existing WebSocket bot.
      3. Neither  — bare execution (cli demos without channel binding).

    ``history``: if provided, the agent loop appends to this list rather
    than starting from a fresh [system, user] pair. The caller owns the
    list and can persist it across messages for stateful sessions (cli,
    selfevo). On return, ``history`` reflects the conversation including
    this turn's tool calls and the assistant's final reply.
    """
    from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
    from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools
    from roborsi.channels.agent.feishu.live_trace import get_session, AgentInterrupted

    if ctx is not None and target_chat_id is None:
        target_chat_id = ctx.chat_id
    sess = get_session(target_chat_id or "default")
    sess.last_user_message = text
    sess.set_busy(True)
    sess.append("user_message", text=text)
    # Manager-first (operator spec 2026-06-29): a Feishu message IS a turn with
    # the persistent RoboRSI Manager — it reads the user, drives the triangle
    # + approves itself, replies. ROBORSI_DIRECT_3ROLE=1 bypasses to the legacy
    # in-channel triangle/Opus loop below.
    if os.environ.get("ROBORSI_DIRECT_3ROLE", "0") == "0":
        from roborsi.agents import manager_chat
        return manager_chat.reply(text)
    monitor = os.environ.get("ROBORSI_MONITOR_URL", "http://localhost:8770")
    sess.append("monitor_link", url=f"{monitor}/live/{target_chat_id or 'default'}")

    # ── 3-role fast path (Planner → Engineer → Reviewer) ──
    # On user request that names an atomic .zeroshot OR a long-horizon
    # .execute we don't go through the outer Opus tool loop at all.
    # _run_atomic_3role / _run_lh_3role drives the new path and returns
    # the user-facing reply directly.
    if os.environ.get("ROBORSI_3ROLE", "1") != "0":
        # LH detection runs first since it short-circuits before atomic
        # detection would (LH names like handover_block_bicoord are in
        # _detect_atomic_intent's blocklist).
        lh_hit = _detect_lh_intent(text)
        if lh_hit is not None:
            lh_task, seed_hint = lh_hit
            try:
                reply = _run_lh_3role(text=text, lh_task=lh_task,
                                        seed=seed_hint, sess=sess,
                                        target_chat_id=target_chat_id,
                                        channel=channel, ctx=ctx)
                final_text_3role = reply
                _post_3role(sess, final_text_3role, target_chat_id)
                return reply
            except Exception as exc:
                import traceback as _tb
                sess.append("lh3role_exception", text=str(exc))
                # Make the fallback LOUD: a 3-role crash silently dropping to
                # the legacy compound-skill loop can mask a reviewer failure as
                # a fake 2/2 (overclaim). Surface it in the run log.
                print(f"[3role] LH path CRASHED → falling back to legacy "
                      f"(success here is NOT reviewer-verified): {exc}",
                      file=sys.stderr)
                _tb.print_exc()
                # Fall through to legacy.
        atomic_hit = _detect_atomic_intent(text)
        if atomic_hit is not None:
            atomic_name, seed_hint = atomic_hit
            try:
                reply = _run_atomic_3role(text=text, atomic=atomic_name,
                                            seed=seed_hint, sess=sess,
                                            target_chat_id=target_chat_id,
                                            channel=channel, ctx=ctx)
                # Reflection finally still runs below, via the same finally
                # block — we set final_text and skip the tool loop.
                final_text_3role = reply
                _post_3role(sess, final_text_3role, target_chat_id)
                return reply
            except Exception as exc:
                import traceback as _tb
                sess.append("3role_exception", text=str(exc))
                print(f"[3role] atomic path CRASHED → falling back to legacy "
                      f"(success here is NOT reviewer-verified): {exc}",
                      file=sys.stderr)
                _tb.print_exc()
                # Don't kill the path — fall through to legacy loop.

    system = (
        "You are the roborsi operator — a robotics chat interface backed "
        "by a sapien-based skill runtime. Users may ask in Chinese or English "
        "to discover, run, inspect, debug, or extend roborsi skills.\n\n"
        "ROBORSI MENTAL MODEL:\n"
        "  • A 'skill' = a directory with SKILL.md (and usually policy.py).\n"
        "  • Atomic tasks NEST: parent like `click_bell` is doc-only (📄 in "
        "the index); the runnable child is `click_bell.zeroshot` (🚀) which "
        "is the VLM rollout, or `click_bell.execute` for scripted expert.\n"
        "  • To RUN: call run_skill(name=<exact registry name from index>). "
        "Use the FULL name including the `.zeroshot` / `.execute` suffix. "
        "Do NOT add an `atomic.` prefix — registry names are flat.\n"
        "  • Atomic .zeroshot tasks block ~30-90s; demo video auto-pushes.\n"
        "  • To INSPECT: read_skill (SKILL.md) or read_skill_code (policy.py).\n"
        "  • To EXTEND: propose_new_skill / propose_skill_update → human review queue.\n\n"
        "MANDATORY WORKFLOW for user requests:\n"
        "  0. FIRST tool call of every turn: read_recent_reflections(n=5). "
        "Skim what the harness flagged about your last 5 turns — which tool "
        "calls wasted hops, which paths led to dead ends, what the harness "
        "told you to try next. Do NOT repeat patterns flagged as "
        "wasted_hops. Treat the prior 'next_turn' suggestion as the strong "
        "default plan unless evidence has changed.\n"
        "  1. The skill index below is COMPLETE — scan for matching 🚀 skills.\n"
        "  2. Pick the runnable skill, copy its name VERBATIM.\n"
        "  3. Call run_skill(name=...). If registry rejects an invented name, "
        "read the error, find the right name, retry.\n"
        "  4. After completion, give a short summary in user's language.\n\n"
        "STATUS CHECK after every tool result (rollout-style — reply with one "
        "of these categories as your FIRST WORD then act):\n"
        "  PROCEED — observation matches expectation, advance to next substep.\n"
        "  RETRY  — recoverable; try DIFFERENT args (seed, budget, params).\n"
        "             NEVER retry with identical args.\n"
        "  REPLAN — current approach is wrong. Switch to a different runnable "
        "             skill that achieves the user's goal, or read_skill_code "
        "             to understand why current is failing and propose_skill_update.\n"
        "  RESET  — environment in a bad state; call reset_success / reset_failure "
        "             skill if available, then retry.\n"
        "  DONE   — goal achieved (success) OR genuinely unreachable. Tell user.\n\n"
        "ON ANY run_skill FAILURE (status != 'success'):\n"
        "  Before choosing RETRY / REPLAN / DONE, you MUST in this order:\n"
        "    1. Reflect in thinking: which step in vlm_trace was the last "
        "       successful one, what outcome did judge report, what exactly "
        "       went wrong.\n"
        "    2. read_skill_code(name='<skill>') to inspect policy.py — the "
        "       VLM prompt, the done-criteria, the judge logic.\n"
        "    3. Classify based on (1) + (2):\n"
        "       • STOCHASTIC = could plausibly work next time with different "
        "         seed (e.g., one grasp slipped, one localization was noisy). "
        "         → RETRY with new seed.\n"
        "       • SYSTEMATIC = the skill's logic itself is wrong; retry won't "
        "         help (e.g., vlm_overclaimed = done-prompt is too lax and "
        "         WILL overclaim again with any seed; judge formula is wrong; "
        "         missing a substep). → do NOT retry; go to REPLAN.\n"
        "    4. Act on (3):\n"
        "       • If STOCHASTIC: RETRY with new seed.\n"
        "       • If SYSTEMATIC: REPLAN — either switch to a different skill "
        "         that achieves the goal, or give up the task and report.\n"
        "  Never RETRY blindly with `{{seed: N+1}}` without doing (1)(2)(3). "
        "  Specifically: vlm_overclaimed should ALMOST NEVER be retried with "
        "  a new seed — the VLM prompt is the bug, not the seed.\n\n"
        "DIAGNOSTIC DATA (USE THESE BEFORE PROPOSING ANY FIX):\n"
        "  vlm_trace alone is not enough to design fixes. Before proposing "
        "  EITHER propose_skill_update OR propose_new_skill, ground your "
        "  analysis in single-run evidence, global pattern, AND physical "
        "  diagnostics:\n"
        "    GLOBAL (proves it's worth fixing, not a one-off):\n"
        "      0) get_failure_patterns(atomic=...) FIRST. Read verdict + "
        "         judge_reason_freq. If verdict='SYSTEMATIC', justified.\n"
        "         If 'MIXED/STOCHASTIC', it's tuning/retry — do NOT propose.\n"
        "    PHYSICAL (proves whether it's a PROMPT bug or a BASE-SKILL bug):\n"
        "      1) get_sim_debug() — tail IK-fail / GraspGen / cuRobo lines. "
        "         If you see REPEATED 'IK-fail(grasp): cuRobo plan status = "
        "         Fail' or 'GraspGen returned N candidates, all rejected', "
        "         the failure is MOTION-PLANNING / GEOMETRY — a prompt tweak "
        "         CANNOT fix it. The right move is propose_new_skill for a "
        "         base/robotwin primitive (e.g. grasp_inside_bowl_lateral, "
        "         pick_with_arm_aware_approach). If sim_debug is silent or "
        "         only shows successful grasps, then the VLM is the bug.\n"
        "    LOCAL (proves you understand the scene):\n"
        "      2) get_lh_report(task=...) → judge reasons, frame paths.\n"
        "      3) list_atomic_frames(atomic=...) → recent capture paths.\n"
        "      4) view_frame(path=..., question=...) — REQUIRED before any "
        "         propose. Ask specific physical questions ('how far is the "
        "         gripper from the bowl rim? Are fingers inside or outside?').\n"
        "  A proposal that doesn't cite (0) SYSTEMATIC verdict + (1) sim_debug "
        "  observation + (4) view_frame observation will be rejected. The "
        "  reviewer needs proof you saw the pattern, ruled out / confirmed the "
        "  geometry failure, AND understood the scene — not just the VLM trace.\n"
        "  ALSO: every propose_skill_update / propose_new_skill MUST include a "
        "  non-empty rationale AND new_code. Empty proposals are auto-rejected.\n\n"
        "DEEPER INVESTIGATION (when above isn't enough):\n"
        "  CHECKLIST FIRST. Before improvising diagnosis, read the procedure:\n"
        "    read_skill('diagnose_atomic_failure') — 6-section ordered checklist "
        "    (A: contradiction signal | B: gate/judge bug | C: VLM-vs-recipe | "
        "    D: motion-planning bug | E: base-skill choice | F: post-fix). Follow "
        "    sections top-to-bottom, STOP at the first match. Each step specifies "
        "    which tool to call and how to interpret the reading. The checklist "
        "    encodes the proven procedure — don't reinvent it per case.\n"
        "  VISIBLE-EVIDENCE PROTOCOL — section E of the checklist. Trigger when "
        "    (i) get_failure_patterns(atomic) shows ≥2 SYSTEMATIC rounds,\n"
        "    (ii) get_inner_trace shows VLM IS calling the recipe's primitives,\n"
        "    (iii) those primitives return ok=True success=False holding_visual=False\n"
          "          (motion completes, sim disagrees — not an IK / planning bug).\n"
        "  In that case the BASE SKILL choice is wrong, not the prompt. Do:\n"
        "    1) inspect the retained camera frames and tool results to identify "
        "       whether localization, reachability, grasp closure, transport, or "
        "       placement failed first.\n"
        "    2) read the existing RoboRSI perception and base-skill implementations "
        "       that operate on the same visible cue.\n"
        "    3) grep_repo for reusable geometric or visual primitives before "
        "       proposing a new one.\n"
        "    4) If no reusable primitive exists, propose a camera/depth/proprioception-"
        "       based base skill with a focused harness.\n"
        "    5) Promote it in the atomic recipe only after the harness validates it.\n"
        "\n"
        "  CONTRADICTION SIGNAL (lighter weight) — if get_lh_report shows a clash "
        "  judge.reason and outcome (e.g. judge: 'gripper appears grasping "
        "  bowl' but outcome=never_attempted_grasp), DO NOT trust the "
        "  outcome string. The structural gate or the helper that produced "
        "  it may be buggy. Trace the contradiction:\n"
        "    A) get_inner_trace(run_id) — list the inner sim VLM's actual "
        "       tool calls. See what primitives it really invoked.\n"
        "    B) read_file('roborsi/embodied/skills/_lib/evaluation/"
        "       trace_inspect.py') — read the gate-helper source.\n"
        "    C) read_skill_code(<atomic>.zeroshot) — see how the gate is "
        "       wired into that atomic.\n"
        "  If trace shows the canonical primitive WAS called but gate fired "
        "  anyway → gate has a shape-matching bug (the bug we hit on "
        "  2026-05-30: rollout trace stored step.tool_call.tool but the "
        "  helper looked for step.tool_calls / step.tool). Propose a fix to "
        "  trace_inspect.py via propose_skill_update on the atomic that "
        "  consumes it (or call out the lib bug in your reply).\n"
        "  You have the SAME read-access the human reviewer uses. Use it:\n"
        "    • read_file(path) — any file under RoboRSI, "
        "      ~/.roborsi, /tmp/agent_loop, /tmp/roborsi-*. Use this "
        "      to study how an EXISTING base skill "
        "      or grasp_then_lift_graspgen is implemented before designing "
        "      a new sibling primitive. Diagnose only from observations, traces, "
        "      public skill contracts, and the final post-hoc simulator verdict.\n"
        "    • list_dir(path) — navigate when you don't know the file name.\n"
        "    • grep_repo(pattern, glob, root) — find where something is "
        "      called from, who registers a tool, and what other skills handle "
        "      similar geometry. Search root='repo'.\n"
        "  These are the same tools the human uses to diagnose. If you would "
        "  ask 'how is X implemented' / 'where does Y get called', call these "
        "  instead of guessing.\n\n"
        "ON TASK GIVE-UP (you've decided the user's goal is unreachable):\n"
        "  Reply to user in this structure (Chinese if user wrote Chinese):\n"
        "    ❌ 任务失败：<one-line outcome>\n"
        "    原因分析：<which step / why it broke, based on vlm_trace + code>\n"
        "    代码层是否需要改进：<是/否，理由>\n"
        "    [如果是] 建议改动：<file path>\n"
        "    ```diff\n"
        "    <concrete diff or pseudo-diff>\n"
        "    ```\n"
        "  The analysis + suggested code change appears in your reply (chat AND "
        "  HTML monitor) — no separate proposal queue.\n\n"
        "MULTI-PROPOSAL POLICY:\n"
        "  When you call propose_new_skill / propose_skill_update, you are "
        "  NOT limited to one per attempt. If the failure surfaces multiple "
        "  distinct improvements — e.g. (a) a new reusable base skill "
        "  `press_at_pixel`, (b) a tightening of click_bell's done-criteria, "
        "  (c) a missing `verify_visual_change` base skill — emit ALL of them "
        "  as separate tool calls in the same turn or across turns. The human "
        "  reviewer prefers many small focused proposals over one mega-proposal. "
        "  Each proposal must be independently applyable (each its own file(s)).\n\n"
        "HOW TO PROPOSE A `base` SKILL THAT BECOMES AN ACTUAL VLM TOOL:\n"
        "  Base skills are AUTO-WIRED into the rollout tool list iff they live\n"
        "  at `roborsi/embodied/skills/base/robotwin/<name>/policy.py`\n"
        "  AND their policy exposes a function:\n"
        "      def dispatch_runtime(state, args: dict) -> tuple[dict, Observation]:\n"
        "  (NOT a plain `run()` calling backend methods — that path cannot\n"
        "  access the sim env / scene / contacts.)\n"
        "  Before proposing ANY new base skill that needs sim state (contacts,\n"
        "  scene actors, env._impl, perception output), FIRST call\n"
        "  `read_skill_code(name='list_contacts')` (or any other base skill\n"
        "  under base/robotwin/) and copy that dispatch_runtime template.\n"
        "  In your propose_new_skill call use category='base/robotwin' (the\n"
        "  applier will write under skills/base/robotwin/<name>/).\n"
        "  Self-check before proposing:\n"
        "    • Does my code define dispatch_runtime(state, args)?  (yes/no)\n"
        "    • Does it access state.env._impl / state.env._impl.scene?  (yes/no)\n"
        "    • Did I copy the snapshot-return pattern (return ({...}, _snapshot(state.env)))?\n"
        "  If any answer is 'no', read_skill_code('list_contacts') first.\n\n"
        "Each run_skill result includes status, outcome, summary, and a full "
        "episode_summary with the vlm_trace. USE those signals to pick the next "
        "category — don't loop blindly. Self-improvement loop is available: "
        "read_skill_code + propose_skill_update / propose_new_skill go to the "
        "human review queue.\n\n"
        "DO NOT:\n"
        "  - Invent skill names not in the index (no `atomic.` prefix exists).\n"
        "  - Ask the user 'which task' — pick the most fitting one, execute, report.\n"
        "  - Reply '(no reply)' or empty. Always say something concrete.\n\n"
        "★ MANDATORY FINAL REPLY (never silent — agent silence = task failure):\n"
        "  After your tool chain finishes, you MUST emit a text response in one\n"
        "  of these formats. Empty / null text after tool calls is a BUG, not\n"
        "  a valid output. (V13 round 3 burned 14 min of forensic + 11 tool\n"
        "  calls then returned None — that wasted everyone's time. Never again.)\n"
        "\n"
        "  Format A — task succeeded:\n"
        "    ✓ <task>: <one-line outcome>. <key sim/judge evidence>.\n"
        "\n"
        "  Format B — proposal submitted this turn:\n"
        "    ✏️ Proposed <kind> for <skill>: <one-line rationale summary>.\n"
        "    Reasoning: <2-3 line summary of evidence chain>.\n"
        "\n"
        "  Format C — failure with no actionable proposal (RARE — see ★ below):\n"
        "    ❌ <task> failed: <stage>, <symptom>.\n"
        "    Diagnosed: <root cause in one line>.\n"
        "    Why no proposal:\n"
        "      • <one of: 'requires infra-level base skill (ESCALATE to user)' /\n"
        "                 'already proposed in <pid> awaiting review' /\n"
        "                 'duplicate of recent applied commit <sha>' /\n"
        "                 'sim-level limitation, no software fix possible'>\n"
        "    Recommended next: <concrete suggestion for user — e.g.\n"
        "       'add bi_arm_move_to_pose base skill' / 'rerun with different seed' /\n"
        "       'accept partial as acceptable for this task'>.\n"
        "\n"
        "  Format D — explicit ESCALATE (you've diagnosed a base-skill or\n"
        "    motion-primitive issue and per protocol can't propose it yourself):\n"
        "    🚨 ESCALATE: <one-line problem>.\n"
        "    Evidence: <data points from get_inner_trace / view_frame / gate_log>.\n"
        "    Suggested infra fix: <what user should write>.\n"
        "\n"
        "★ THE PROPOSE-OR-JUSTIFY RULE (the hard one — read twice):\n"
        "  If your final reply names ANY concrete fix candidate — e.g.\n"
        "  'add trace_invoked gate', 'wrap impl.grasp_actor', 'tweak descend_z',\n"
        "  'modify pick_bowl_bicoord.zeroshot prompt' — then in the SAME turn\n"
        "  you MUST have called propose_skill_update or propose_new_skill\n"
        "  (Format B). Verbal diagnosis without a propose_* tool call is the\n"
        "  #1 failure mode of this loop. 17 LH rounds were burned on agents\n"
        "  that diagnosed correctly then went home without submitting.\n"
        "\n"
        "  Format C is ONLY valid when the fix is genuinely outside your\n"
        "  authoring scope (infra base skill, sim limitation, already-pending\n"
        "  duplicate). If you can name the file + line + diff in prose, you\n"
        "  CAN call propose_skill_update — DO IT, then write Format B.\n"
        "\n"
        "  Self-check before emitting your final text:\n"
        "    1. Did I diagnose a failure? (Y/N)\n"
        "    2. Do I have a concrete file + change in mind? (Y/N)\n"
        "    3. Did I call propose_* this turn?  (Y/N)\n"
        "  If 1=Y AND 2=Y AND 3=N → STOP, call propose_* FIRST, then reply.\n\n"
        + _get_skill_index() + "\n\n"
        "EXAMPLE WORKFLOW:\n"
        "  User: \"做一下 click bell\"\n"
        "  You: [call run_skill name='click_bell.zeroshot']\n"
        "       [task runs 30-90s, demo auto-pushes]\n"
        "       Reply: \"完成了，click_bell 成功 ✓\"\n"
    )
    if history is None:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ]
    else:
        # Stateful session: seed system prompt the first time, then just
        # append the new user turn to the running conversation.
        messages = history
        if not messages:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": text})
    final_text = ""
    try:
        for hop in range(max_hops):
            sess.check_interrupt()
            sess.append("opus_call", hop=hop)
            msg = _call_vlm_tools(DEFAULT_MODEL, messages, _TOOLS,
                                    thinking_budget=4000)
            tool_calls = getattr(msg, "tool_calls", None) or []
            content = getattr(msg, "content", "") or ""
            if isinstance(content, list):
                content = "".join(_extract_text_block(c) for c in content)
            if content:
                final_text = content
                sess.append("opus_thinking", text=content)
            if not tool_calls:
                # Final reply with no further tool calls. Append it to
                # history so subsequent stateful turns can see it.
                if content:
                    messages.append({"role": "assistant", "content": content})
                    if _needs_force_propose(content, messages):
                        sess.append("opus_force_propose",
                                      text="diagnosed failure but no propose_* call")
                        messages.append({"role": "user", "content": (
                            "You wrote a Format C/D reply that names a concrete "
                            "fix candidate (file/function/diff in prose) but "
                            "did NOT call propose_skill_update or "
                            "propose_new_skill this turn. Per the "
                            "PROPOSE-OR-JUSTIFY RULE this is forbidden — "
                            "verbal diagnosis without a propose_* tool call is "
                            "the #1 failure mode of this loop. EITHER call "
                            "propose_skill_update / propose_new_skill RIGHT "
                            "NOW with the exact code change you described, "
                            "OR rewrite your reply as Format C with one of "
                            "the four valid 'why no proposal' reasons "
                            "(infra-level / already-pending / duplicate-of-"
                            "commit / sim-limitation). No third option.")})
                        forced = _call_vlm_tools(DEFAULT_MODEL, messages,
                                                    _TOOLS, thinking_budget=2000)
                        f_tool_calls = getattr(forced, "tool_calls", None) or []
                        fc = getattr(forced, "content", "") or ""
                        if isinstance(fc, list):
                            fc = "".join(_extract_text_block(c) for c in fc)
                        if f_tool_calls:
                            messages.append({
                                "role": "assistant", "content": fc or None,
                                "tool_calls": [{"id": tc.id, "type": "function",
                                                  "function": {"name": tc.function.name,
                                                                "arguments": tc.function.arguments}}
                                                 for tc in f_tool_calls],
                            })
                            for tc in f_tool_calls:
                                try:
                                    args = json.loads(tc.function.arguments or "{}")
                                except json.JSONDecodeError:
                                    args = {}
                                sess.append("tool_call", name=tc.function.name,
                                              args=args, forced=True)
                                print(f"  [tool-forced] {tc.function.name}("
                                      f"{json.dumps(args)[:120]})", flush=True)
                                result = _exec_tool(tc.function.name, args,
                                                      target_chat_id,
                                                      channel=channel, ctx=ctx)
                                sess.append("tool_result", name=tc.function.name,
                                              result_preview=result[:300],
                                              length=len(result))
                                messages.append({
                                    "role": "tool", "tool_call_id": tc.id,
                                    "content": result,
                                })
                            # After forced propose, do ONE more synthesis turn
                            # to get Format B text.
                            synth = _call_vlm_tools(DEFAULT_MODEL, messages, [],
                                                        thinking_budget=0,
                                                        tool_choice="none")
                            sc = getattr(synth, "content", "") or ""
                            if isinstance(sc, list):
                                sc = "".join(_extract_text_block(c) for c in sc)
                            if sc:
                                final_text = sc
                                messages.append({"role": "assistant", "content": sc})
                                sess.append("opus_thinking", text=sc)
                        elif fc:
                            final_text = fc
                            messages.append({"role": "assistant", "content": fc})
                            sess.append("opus_thinking", text=fc)
                    break
                # Empty content + no tool_calls = LLM went silent. Force one
                # final synthesis turn with an explicit user prompt telling
                # the LLM to produce the A/B/C/D format reply NOW.
                messages.append({"role": "user", "content": (
                    "You stopped without writing a final reply. Per the "
                    "MANDATORY FINAL REPLY rule, you MUST emit one of "
                    "format A (✓ success), B (✏️ proposed), C (❌ failed + "
                    "why no proposal + recommended next), or D (🚨 ESCALATE). "
                    "Do NOT call any more tools — synthesize from what you "
                    "already gathered. If you can't decide, write format C "
                    "with 'no clear next step' as the recommended next. "
                    "Empty response is forbidden — write at least 'Format C: "
                    "no progress' if absolutely nothing else.")})
                sess.append("opus_force_synth", text="injected synthesis demand")
                forced = _call_vlm_tools(DEFAULT_MODEL, messages, [],
                                            thinking_budget=0,
                                            tool_choice="none")
                fc = getattr(forced, "content", "") or ""
                if isinstance(fc, list):
                    fc = "".join(_extract_text_block(c) for c in fc)
                if fc:
                    final_text = fc
                    messages.append({"role": "assistant", "content": fc})
                    sess.append("opus_thinking", text=fc)
                break
            messages.append({
                "role": "assistant", "content": content or None,
                "tool_calls": [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.function.name,
                                                "arguments": tc.function.arguments}}
                                 for tc in tool_calls],
            })
            for tc in tool_calls:
                sess.check_interrupt()
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                sess.append("tool_call", name=tc.function.name, args=args)
                print(f"  [tool] {tc.function.name}({json.dumps(args)[:120]})", flush=True)
                result = _exec_tool(tc.function.name, args, target_chat_id,
                                      channel=channel, ctx=ctx)
                preview = result[:300]
                sess.append("tool_result", name=tc.function.name,
                              result_preview=preview, length=len(result))
                print(f"  [result] {result[:200]!r}", flush=True)
                messages.append({
                    "role": "tool", "tool_call_id": tc.id, "content": result,
                })
        else:
            # for-loop completed without break — hop limit exhausted.
            # Force a final synthesis turn so we don't return empty.
            if not final_text:
                sess.append("opus_hop_exhausted", text=f"max_hops={max_hops} reached")
                messages.append({"role": "user", "content": (
                    "You've hit the hop limit. Per the MANDATORY FINAL "
                    "REPLY rule, emit format A/B/C/D NOW. Synthesize from "
                    "all data you gathered — no more tools allowed. "
                    "Empty response is forbidden — write at least "
                    "'Format C: hop limit reached, no synthesis attempted'.")})
                forced = _call_vlm_tools(DEFAULT_MODEL, messages, [],
                                            thinking_budget=0,
                                            tool_choice="none")
                fc = getattr(forced, "content", "") or ""
                if isinstance(fc, list):
                    fc = "".join(_extract_text_block(c) for c in fc)
                if fc:
                    final_text = fc
                    sess.append("opus_thinking", text=final_text)
        # Post-loop force-propose check: catches the hop-exhausted exit
        # (V23 R1 case — 21 tools > max_hops=15, agent emitted ESCALATE
        # text but never reached the in-loop 'if not tool_calls' branch
        # where force_propose used to live). Idempotent: returns False
        # if a propose_* call already happened this turn.
        if final_text and _needs_force_propose(final_text, messages):
            sess.append("opus_force_propose_post_loop",
                          text="post-loop: diagnosed without propose_*")
            messages.append({"role": "user", "content": (
                "You wrapped up without calling propose_skill_update or "
                "propose_new_skill, but your final reply names a concrete "
                "fix (file/line/diff in prose). Per the PROPOSE-OR-JUSTIFY "
                "RULE this is forbidden. Call propose_* RIGHT NOW with the "
                "exact code change you described — no narration, just the "
                "tool_use. If you genuinely cannot, rewrite as Format C "
                "with one of 4 valid reasons.")})
            forced = _call_vlm_tools(DEFAULT_MODEL, messages, _TOOLS,
                                        thinking_budget=2000)
            f_tool_calls = getattr(forced, "tool_calls", None) or []
            fc = getattr(forced, "content", "") or ""
            if isinstance(fc, list):
                fc = "".join(_extract_text_block(c) for c in fc)
            if f_tool_calls:
                messages.append({
                    "role": "assistant", "content": fc or None,
                    "tool_calls": [{"id": tc.id, "type": "function",
                                      "function": {"name": tc.function.name,
                                                    "arguments": tc.function.arguments}}
                                     for tc in f_tool_calls],
                })
                for tc in f_tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    sess.append("tool_call", name=tc.function.name,
                                  args=args, forced=True)
                    print(f"  [tool-forced-post] {tc.function.name}("
                          f"{json.dumps(args)[:120]})", flush=True)
                    result = _exec_tool(tc.function.name, args,
                                          target_chat_id, channel=channel,
                                          ctx=ctx)
                    messages.append({"role": "tool",
                                      "tool_call_id": tc.id,
                                      "content": result})
                synth = _call_vlm_tools(DEFAULT_MODEL, messages, [],
                                            thinking_budget=0,
                                            tool_choice="none")
                sc = getattr(synth, "content", "") or ""
                if isinstance(sc, list):
                    sc = "".join(_extract_text_block(c) for c in sc)
                if sc:
                    final_text = sc
                    sess.append("opus_thinking", text=sc)
            elif fc:
                final_text = fc
                sess.append("opus_thinking", text=fc)
    except AgentInterrupted as e:
        sess.append("interrupted", reason=str(e))
        final_text = f"⏸ {e}"
    finally:
        sess.set_busy(False)
        sess.append("done", final_text=final_text)
        # Harness-driven reflection — runs unconditionally, decoupled from
        # whatever the agent did or didn't say. Persists to
        # ~/.roborsi/reflections.jsonl for next turn's tool to read.
        try:
            r = _harness_reflect(messages, final_text, target_chat_id)
            sess.append("harness_reflection", text=r[:500])
        except Exception as e:
            sess.append("harness_reflection_error", text=str(e))
    if final_text:
        return final_text
    # Empty-reply fallback: agent went silent after tool chain (V13 round 3
    # bug). Synthesize a placeholder so downstream callers + run logs
    # show something actionable instead of "(no reply)".
    tool_summary = _summarize_recent_tools(messages)
    return ("⚠️ AGENT SILENCE (no text after tool chain). Did "
             f"{tool_summary['n_calls']} tool calls then returned None. "
             f"Last tools: {', '.join(tool_summary['last_tools'])}. "
             "Treat as no-progress this turn; consider escalating manually.")


def _needs_force_propose(content: str, messages: list[dict]) -> bool:
    """Return True iff the final reply describes a concrete fix but the
    agent did NOT call propose_skill_update / propose_new_skill in this
    turn — the #1 failure mode of the self-evo loop (17 LH rounds wasted
    on agents that diagnosed correctly then went home empty-handed).

    Three independent trigger paths:
      (1) failure markers + fix markers (original V21 case)
      (2) self-claimed submission without an actual propose_* call
          (V22 R1 case: 'Both proposals submitted below — please apply'
          while queue was empty)
      (3) self-escalation language without ESCALATE format compliance
          (V22 R2 case: 'ESCALATE then' buried in prose)"""
    if not content:
        return False
    # Walk back through THIS turn (since last 'user' message) and check
    # whether any assistant message called a propose_* tool.
    last_user_idx = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    proposed_this_turn = False
    for m in messages[last_user_idx + 1:]:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            name = (tc.get("function") or {}).get("name", "")
            if name.startswith("propose_"):
                proposed_this_turn = True
                break
    if proposed_this_turn:
        return False
    lower = content.lower()
    # Path 2: agent CLAIMS submission/escalation without calling propose_*
    fake_submit_markers = (
        "proposals submitted", "proposal submitted",
        "submitted below", "submitted above",
        "✏️ proposed", "format b",
        "已提交", "已提案",
    )
    if any(m in lower for m in fake_submit_markers):
        return True
    # Path 1+3: failure/escalate language + concrete fix candidate
    failure_markers = ("❌", "🚨", "format c", "format d", "failed",
                        "未成功", "诊断", "escalate")
    if not any(m in lower for m in failure_markers):
        return False
    fix_markers = (
        "propose_skill_update", "propose_new_skill",
        "trace_invoked", "add gate", "wrap impl.",
        "would propose", "should propose", "fix candidate",
        "建议", "应该提案", "可以提案", "wrapper",
        "modify", "tweak", "patch ",
        "runtime-level gate", "force at least one",
        "rerun seed", "outside atomic scope",
    )
    return any(m in lower for m in fix_markers)


def _needs_reflection(content: str) -> bool:
    """DEPRECATED — kept for tests. Reflection is now harness-generated
    (see _harness_reflect), not policed via prompt."""
    if not content:
        return False
    return not any(m in content for m in ("Reflection:", "reflection:",
                                              "反思:", "反思：", "REFLECTION:"))


def _summarize_turn_for_reflection(messages: list[dict],
                                       final_text: str) -> str:
    """Build a compact transcript of THIS turn (since last user message)
    to feed the reflection model. Captures: tool names + arg sketches,
    one-line tool result previews, and the final reply."""
    last_user_idx = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    lines: list[str] = []
    user_msg = messages[last_user_idx].get("content", "")
    if isinstance(user_msg, list):
        user_msg = " ".join(_extract_text_block(c) for c in user_msg)
    lines.append(f"USER: {str(user_msg)[:300]}")
    for m in messages[last_user_idx + 1:]:
        role = m.get("role")
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                name = (tc.get("function") or {}).get("name", "?")
                args = (tc.get("function") or {}).get("arguments", "")
                lines.append(f"TOOL_CALL: {name}({str(args)[:120]})")
        elif role == "tool":
            content = m.get("content", "")
            lines.append(f"TOOL_RESULT: {str(content)[:200]}")
    lines.append(f"FINAL: {str(final_text)[:400]}")
    return "\n".join(lines)


def _read_recent_reflections(n: int = 5) -> str:
    """Return the last `n` reflection records from
    ~/.roborsi/reflections.jsonl. Each one is a JSON object the
    harness wrote AFTER the prior turn. Agent calls this at turn start
    to avoid re-burning hops on patterns it's already seen fail."""
    n = max(1, min(int(n or 5), 20))
    path = os.path.expanduser("~/.roborsi/reflections.jsonl")
    if not os.path.exists(path):
        return json.dumps({"reflections": [], "note": "no prior reflections"})
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    recent = lines[-n:]
    out = []
    for line in recent:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"raw": line[:500]})
    return json.dumps({"count": len(out), "reflections": out},
                      ensure_ascii=False, indent=2)[:6000]


def _harness_reflect(messages: list[dict], final_text: str,
                       chat_id: str | None = None) -> str:
    """Harness-driven reflection: independent LLM call that reads the
    turn's tool trace + final reply, produces a structured reflection,
    persists it to ~/.roborsi/reflections.jsonl. Does NOT rely on the
    agent voluntarily writing 'Reflection:' in its reply."""
    import time
    transcript = _summarize_turn_for_reflection(messages, final_text)
    reflect_msgs = [
        {"role": "system", "content": (
            "You are a reflection module — NOT the acting agent. Given a "
            "transcript of one turn (tool calls + results + final reply), "
            "produce a tight JSON object with 4 fields:\n"
            "  wasted_hops:   which tool calls produced nothing useful, "
            "                 with the one-word reason (truncated_output / "
            "                 dead_end_query / redundant_read / wrong_path).\n"
            "  what_worked:   one line on the most informative tool call.\n"
            "  next_turn:     one concrete action for the next turn — a "
            "                 specific tool name + args sketch, NOT prose.\n"
            "  missing:       one line on what evidence is still absent that "
            "                 would unblock progress.\n"
            "Reply with ONLY the JSON object, no prose, no code fence.")},
        {"role": "user", "content": transcript},
    ]
    try:
        from roborsi.embodied.agent_loop.config import DEFAULT_MODEL
        from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools
        resp = _call_vlm_tools(DEFAULT_MODEL, reflect_msgs, [],
                                  thinking_budget=0, tool_choice="none")
    except Exception as e:
        body = json.dumps({"error": f"reflect_call_failed: {e}"})
    else:
        content = getattr(resp, "content", "") or ""
        if isinstance(content, list):
            content = "".join(_extract_text_block(c) for c in content)
        body = content.strip() or json.dumps({"error": "empty_reflection"})
    out_dir = os.path.expanduser("~/.roborsi")
    os.makedirs(out_dir, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "chat_id": chat_id or "",
            "transcript_chars": len(transcript),
            "reflection": body[:4000]}
    with open(os.path.join(out_dir, "reflections.jsonl"), "a",
              encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return body


def _persist_reflection(content: str, chat_id: str | None = None) -> None:
    """Append the reflection block to ~/.roborsi/reflections.jsonl so
    future rounds can read them via a tool (one line per turn)."""
    import re, time
    if not content:
        return
    m = re.search(r"(?:Reflection|reflection|REFLECTION|反思)[:：]\s*"
                  r"(.+?)(?:\n\n|\Z)", content, flags=re.S)
    if not m:
        return
    body = m.group(1).strip()
    if not body:
        return
    out_dir = os.path.expanduser("~/.roborsi")
    os.makedirs(out_dir, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "chat_id": chat_id or "",
            "reflection": body[:2000]}
    with open(os.path.join(out_dir, "reflections.jsonl"), "a",
              encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _extract_text_block(c: Any) -> str:
    """Extract text from a content block — handles all 3 shapes the
    anthropic SDK returns:
      - dict with type='text' (litellm shape)
      - TextBlock object with .type=='text' and .text (anthropic SDK shape,
        what messages.stream().get_final_message() returns)
      - plain str (legacy)
    Returns '' for anything else (ToolUseBlock, etc.)."""
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        if c.get("type") == "text":
            return c.get("text") or ""
        return ""
    # Object form (anthropic.types.TextBlock).
    if getattr(c, "type", None) == "text":
        return getattr(c, "text", "") or ""
    return ""


def _summarize_recent_tools(messages: list[dict]) -> dict[str, Any]:
    """Walk message history backwards, list the last few tool call names.
    Used by empty-reply fallback so the operator knows what agent was doing
    before it went silent."""
    last_tools: list[str] = []
    n_calls = 0
    for m in reversed(messages):
        for tc in (m.get("tool_calls") or []):
            n_calls += 1
            nm = ((tc.get("function") or {}).get("name")
                    or tc.get("name") or "?")
            if len(last_tools) < 5:
                last_tools.append(nm)
    return {"n_calls": n_calls, "last_tools": list(reversed(last_tools))}
