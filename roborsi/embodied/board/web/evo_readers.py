#!/usr/bin/env python3
"""Evo-dashboard data layer (self-evolution 看板, :8787).

Readers specific to the evo dashboard page (:mod:`board.web.page`): the live
call-chain / role-phase parsing, newest camera frame, recent runs, and the
per-task self-evolution tree — all computed by reading disk files (campaign.log,
per-run logs, workspace frames, skill_review/wiki_review/plan_review queues, each
task's wiki.md) fresh per request, no cache. The richer trace.db-aware readers
(sessions, manager aggregation) live alongside in :mod:`board.web.readers`; the
FastAPI wrapper is :mod:`board.web.evo_app`.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

HOME = Path.home()
# Repo root = five levels up (roborsi/embodied/board/web/evo_readers.py).
REPO = Path(__file__).resolve().parents[4]
PB = Path("/tmp/pb")
CAMPAIGN_LOG = PB / "campaign.log"
WORKSPACES = HOME / ".roborsi" / "workspaces"
ATOMIC_DIR = REPO / "roborsi" / "embodied" / "skills" / "atomic"
QUEUES = {
    "skill_review": HOME / ".roborsi" / "skill_review",
    "wiki_review": HOME / ".roborsi" / "wiki_review",
    "plan_review": HOME / ".roborsi" / "plan_review",
}

# Role → colour (argus palette). Planner blue, Engineer green, Reviewer violet,
# Manager amber; success green, fail red.
ROLE_COLOR = {"planner": "#2f6df0", "engineer": "#27a567",
              "reviewer": "#7c5cff", "manager": "#e0930f"}


# ─────────────────────────── file helpers ───────────────────────────

def _tail(path: Path, max_bytes: int = 65536) -> list[str]:
    """Return the last chunk of a (possibly large) text file as lines."""
    if not path.exists():
        return []
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        data = f.read()
    return data.decode("utf-8", "replace").splitlines()


def _load_json(path: Path) -> dict | None:
    """Read one queue JSON; skip files caught mid-write (unparseable)."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _queue_files(name: str, *, sub: str = "") -> list[Path]:
    root = QUEUES[name] / sub if sub else QUEUES[name]
    return sorted(root.glob("*.json")) if root.exists() else []


# ─────────────────────────── aggregation ───────────────────────────

_CUR_RE = re.compile(r"--- (\S+) seed=(\d+) \(round (\d+)\) -> (\S+) ---")
_OUT_RE = re.compile(r"(✓|✗) (\S+) seed=(\d+)")


def _lane_defs() -> list[dict]:
    """Discover ALL campaign lanes from /tmp/pb/current*.txt — one per running
    daemon, not a hardcoded A/B. current.txt → lane A (campaign.log);
    current_<x>.txt → lane <X> (campaign_<x>.log). More daemons appear
    automatically as more current_*.txt files show up."""
    out = []
    for cf in sorted(PB.glob("current*.txt")):
        suf = cf.stem[len("current"):].lstrip("_")     # "" | "b" | "c" ...
        out.append({"id": (suf or "a").upper(), "cur": cf,
                    "log": PB / (f"campaign_{suf}.log" if suf else "campaign.log")})
    return out or [{"id": "A", "cur": PB / "current.txt", "log": CAMPAIGN_LOG}]


def _lane_files(lane: str) -> tuple[Path, Path]:
    """(campaign log, current-marker file) for a lane id, from _lane_defs."""
    lane = (lane or "A").upper()
    for d in _lane_defs():
        if d["id"] == lane:
            return d["log"], d["cur"]
    d0 = _lane_defs()[0]
    return d0["log"], d0["cur"]


def _lane_current(lane: str) -> dict:
    """The current run marker for one lane, parsed from its campaign log."""
    for l in reversed(_tail(_lane_files(lane)[0], 200_000)):
        m = _CUR_RE.search(l)
        if m:
            return {"task": m.group(1), "seed": int(m.group(2)),
                    "round": int(m.group(3)), "log": m.group(4)}
    return {"task": None, "seed": None, "round": None, "log": None}


def _campaign(lane: str = "A") -> dict:
    """All lanes' totals + the SELECTED lane's current run marker, plus a
    per-lane summary (list) so the UI can offer a dynamic all-lanes selector."""
    all_succ, all_runs = [], []
    for d in _lane_defs():
        lines = _tail(d["log"], 200_000)
        all_succ += [l for l in lines if "SIM SUCCESS" in l]
        all_runs += [l for l in lines if "no Sim success" in l or "SIM SUCCESS" in l]
    log = _lane_files(lane)[0]
    live = (log.exists() and (time.time() - log.stat().st_mtime) < 900)
    solved: list[str] = []
    for l in all_succ:
        m = re.search(r"✓✓\s+(\S+)\s+seed=(\d+)", l)
        if m and m.group(1) not in solved:
            solved.append(m.group(1))          # distinct tasks — 相同任务不重复计数
    lanes = [{"id": d["id"], **_lane_current(d["id"])} for d in _lane_defs()]
    return {"real_success": len(solved), "total_runs": len(all_runs),
            "success_list": solved[-8:], "current": _lane_current(lane),
            "live": live, "lane": lane, "lanes": lanes}


def _current_run_log(cur: dict) -> Path | None:
    if cur.get("log"):
        p = PB / cur["log"]
        if p.exists():
            return p
    logs = sorted(PB.glob("run_*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


_STEP_ARROW = re.compile(r"\[zeroshot\] step=(\d+) → (\w+)\((.*)\)")
_STEP_OK = re.compile(r"\[zeroshot\] step=(\d+) tool=(\w+) .*ok=(True|False)")
_REFLECT = re.compile(r"\[zeroshot\] step=\d+ 💭 (.*)")


def _roles_and_steps(cur: dict) -> tuple[list[dict], list[dict], str]:
    """Active-role war-room + recent tool steps + latest reflection, parsed
    from the current run log."""
    log = _current_run_log(cur)
    lines = _tail(log, 40000) if log else []
    # which phase are we in (last phase marker wins)
    phase = "engineer"
    for l in reversed(lines):
        if "🔍 Reviewer" in l:
            phase = "reviewer"
            break
        if "🧠 Planner" in l:
            phase = "planner"
            break
        if "step=" in l or "🎛️" in l:
            phase = "engineer"
            break
    # recent tool steps
    steps: list[dict] = []
    ok_by_step: dict[str, str] = {}
    for l in lines:
        m = _STEP_OK.search(l)
        if m:
            ok_by_step[m.group(1)] = m.group(3)
    for l in lines:
        m = _STEP_ARROW.search(l)
        if m:
            steps.append({"step": int(m.group(1)), "tool": m.group(2),
                          "args": m.group(3)[:80],
                          "ok": ok_by_step.get(m.group(1))})
    steps = steps[-16:]
    reflection = ""
    for l in reversed(lines):
        m = _REFLECT.search(l)
        if m:
            reflection = m.group(1)[:280]
            break
    cur_skill = steps[-1]["tool"] if steps else "—"
    goal = ""
    for l in reversed(lines):
        if "🧠 Planner goal:" in l:
            goal = l.split("goal:", 1)[1].strip()[:160]
            break
    pend = sum(len(_queue_files(q)) for q in QUEUES)
    roles = [
        {"role": "planner", "color": ROLE_COLOR["planner"],
         "active": phase == "planner",
         "action": goal or "写 plan.md(读种子+wiki 线索)"},
        {"role": "engineer", "color": ROLE_COLOR["engineer"],
         "active": phase == "engineer",
         "action": f"{cur_skill}  ·  {cur['task'] or ''} seed={cur.get('seed')}"},
        {"role": "reviewer", "color": ROLE_COLOR["reviewer"],
         "active": phase == "reviewer",
         "action": "诊断失败 → wiki_review 队列"},
        {"role": "manager", "color": ROLE_COLOR["manager"], "active": False,
         "action": f"审 {pend} 条待办 · 晋升/淬炼 gate"},
    ]
    return roles, steps, reflection


def _recent_runs() -> list[dict]:
    out = []
    for l in _tail(CAMPAIGN_LOG, 60000):
        m = _OUT_RE.search(l)
        if m:
            tc = re.search(r"\((\d+) tool calls\)", l)
            out.append({"ok": m.group(1) == "✓", "task": m.group(2),
                        "seed": int(m.group(3)),
                        "tool_calls": int(tc.group(1)) if tc else None})
    return out[-14:][::-1]


def _count_leads(wiki_md: str) -> int:
    """Bullets under '## Manager-approved leads'."""
    if "## Manager-approved leads" not in wiki_md:
        return 0
    body = wiki_md.split("## Manager-approved leads", 1)[1]
    body = re.split(r"\n## ", body, 1)[0]
    return sum(1 for ln in body.splitlines() if ln.startswith("- ["))


def _evolution() -> dict:
    """Per-task knowledge tree + global skill-evolution counts."""
    # queue tallies by task (pending in root; applied/rejected in archives)
    def _by_task(name: str, sub: str) -> dict[str, int]:
        d: dict[str, int] = {}
        for f in _queue_files(name, sub=sub):
            p = _load_json(f)
            if p and p.get("task"):
                d[p["task"]] = d.get(p["task"], 0) + 1
        return d
    hyp_pending = _by_task("wiki_review", "")
    promo_pending = _by_task("plan_review", "")
    promo_applied = _by_task("plan_review", "applied")

    tasks = []
    if ATOMIC_DIR.exists():
        for tdir in sorted(ATOMIC_DIR.iterdir()):
            zs = tdir / "zeroshot"
            wiki = zs / "wiki.md"
            if not wiki.exists():
                continue
            md = wiki.read_text(encoding="utf-8", errors="replace")
            node = {
                "task": tdir.name,
                "seed_plan": (zs / "plan.md").exists(),
                "success": md.count("outcome: ✓ success"),
                "fail": md.count("outcome: ✗ failure"),
                "leads": _count_leads(md),
                "hyp_pending": hyp_pending.get(tdir.name, 0),
                "promo_pending": promo_pending.get(tdir.name, 0),
                "promo_applied": promo_applied.get(tdir.name, 0),
            }
            # keep only tasks with real accumulated knowledge/activity so the
            # tree stays signal-rich (skip the ~47 never-run task stubs).
            if (node["seed_plan"] or node["success"] or node["fail"]
                    or node["leads"] or node["hyp_pending"]
                    or node["promo_pending"] or node["promo_applied"]):
                tasks.append(node)
    tasks.sort(key=lambda t: (t["success"], t["leads"], t["fail"]),
               reverse=True)
    # global skill evolution (skill_review applied = matured skills)
    new_n = upd_n = 0
    recent = []
    for f in sorted(_queue_files("skill_review", sub="applied"),
                    key=lambda p: p.stat().st_mtime)[-10:]:
        p = _load_json(f)
        if not p:
            continue
        kind = p.get("kind", "update")
        if kind == "new":
            new_n += 1
        else:
            upd_n += 1
        recent.append({"skill": p.get("skill") or p.get("name") or "?",
                       "kind": kind})
    return {
        "tasks": tasks,
        "skills": {"new": new_n, "updated": upd_n, "recent": recent[::-1]},
        "pending": {q: len(_queue_files(q)) for q in QUEUES},
    }


def _newest_frame(lane: str = "A") -> Path | None:
    """Live head_camera tick for the SELECTED lane's active run workspace (from
    that lane's current.txt) so we never glob the (millions-of-files) whole
    workspaces tree — that made /frame.jpg time out (Cloudflare 524)."""
    if not WORKSPACES.exists():
        return None
    tasks = []
    cf = _lane_files(lane)[1]
    if cf.exists():
        parts = cf.read_text(encoding="utf-8", errors="replace").split()
        if parts:
            tasks.append(parts[0])
    cand_dirs = []
    for t in tasks:
        ds = sorted(WORKSPACES.glob(f"{t}-*"),
                    key=lambda d: d.stat().st_mtime, reverse=True)
        if ds:
            cand_dirs.append(ds[0])
    if not cand_dirs:                        # fallback: 4 newest run dirs overall
        ds = [d for d in WORKSPACES.iterdir() if d.is_dir()]
        ds.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        cand_dirs = ds[:4]
    newest, newest_mt = None, 0.0
    for d in cand_dirs:
        for f in d.glob("rollout/*/tick_*.jpg"):
            mt = f.stat().st_mtime
            if mt > newest_mt:
                newest, newest_mt = f, mt
    return newest


# ── Manager chat conversation ring (fed by POST /message), per session ──────
_CONVO: dict[str, list[dict]] = {}
_CONVO_MAX = 40


def _convo_add(session: str, role: str, text: str, secs: float | None = None) -> None:
    entry = {"role": role, "text": text, "ts": time.strftime("%H:%M:%S")}
    if secs is not None:
        entry["secs"] = round(secs, 1)
    ring = _CONVO.setdefault(session or "direct", [])
    ring.append(entry)
    del ring[:-_CONVO_MAX]


def _list_sessions() -> list[str]:
    try:
        from roborsi.agents.manager_chat import list_sessions
        return list_sessions()
    except Exception:
        return ["direct"]


def _run_command(cmd: str, pid: str) -> tuple[bool, str]:
    """Approve/reject a pending proposal via the existing apply script."""
    if cmd not in ("approve", "reject") or not pid:
        return False, "usage: /approve|/reject <proposal_id>"
    argv = [sys.executable, str(REPO / "scripts" / "apply_selfevo_proposal.py")]
    if cmd == "reject":
        argv.append("--reject")
    argv.append(pid)
    r = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, timeout=900)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-800:]


_TOOL_CAT = {
    "perceive": ("look", "find_pixel", "unproject_pixel", "detect_object",
                 "get_object_bbox", "zoom_in", "localize_object_top_center",
                 "read_task_wiki", "estimate_feature_point", "find_object_via_wrist"),
    "reach": ("is_reachable", "probe_ik_workspace", "get_grasp_pose"),
    "grasp": ("grasp_object", "grasp_diverse", "grasp_top_down", "gripper",
              "is_holding", "verify_holding_visual", "get_grasp_pose_segmented"),
    "move": ("move_to_pose", "move_fingertip_to", "descend_tcp_to_z", "park_arm",
             "move_dual_arm"),
    "place": ("place_object_in", "place_beside", "place_held_at_target_servo",
              "tip_pour"),
    "verify": ("verify_pick_complete", "check_success", "done"),
}
_CAT_OF = {t: c for c, ts in _TOOL_CAT.items() for t in ts}
# category → colour + swim-lane row (y order top→bottom)
CAT_COLOR = {"perceive": "#2f6df0", "reach": "#12b5c9", "grasp": "#27a567",
             "move": "#e0930f", "place": "#7c5cff", "verify": "#5fd39b",
             "other": "#8a97ad"}
CAT_ROW = {"perceive": 0, "reach": 1, "grasp": 2, "move": 3, "place": 4,
           "verify": 5, "other": 6}


def _call_chain(cur: dict) -> list[dict]:
    """The Engineer's control-flow chain for the current run — tool calls PLUS
    the state-machine transitions that make it a tree: REPLAN (plan() with a
    reason_for_revision), RETRY (STATUS-CHECK 💭 RETRY on the current substep),
    and DONE (terminal). Each node: {step,tool,args,ok,cat,kind,status,note}.
    kind ∈ tool|replan|done. status ∈ RETRY|PROCEED|DONE|None (from 💭)."""
    log = _current_run_log(cur)
    lines = _tail(log, 160000) if log else []
    ok_by_step: dict[str, str] = {}
    note_by_step: dict[str, tuple[str, str]] = {}
    for l in lines:
        m = _STEP_OK.search(l)
        if m:
            ok_by_step[m.group(1)] = m.group(3)
        s = re.search(r"\[zeroshot\] step=(\d+) 💭 (RETRY|PROCEED|DONE)\s*[-:]?\s*(.*)", l)
        if s:
            note_by_step[s.group(1)] = (s.group(2), s.group(3)[:120])
    chain: list[dict] = []
    for l in lines:
        m = _STEP_ARROW.search(l)
        if not m:
            continue
        step, tool, args = m.group(1), m.group(2), m.group(3)
        status, note = note_by_step.get(step, (None, ""))
        kind = "replan" if tool == "plan" else ("done" if tool == "done" else "tool")
        if kind == "replan":
            rr = re.search(r"reason_for_revision=([^,]+)", args)
            note = rr.group(1)[:120] if rr else note
        chain.append({"step": int(step), "tool": tool, "args": args[:60],
                      "ok": ok_by_step.get(step), "cat": _CAT_OF.get(tool, "other"),
                      "kind": kind, "status": status, "note": note})
    return chain[-70:]


def _task_leads_text(task: str) -> list[str]:
    """The Manager-approved leads as full-text bullets for a task (from wiki.md)."""
    wiki = ATOMIC_DIR / (task or "_") / "zeroshot" / "wiki.md"
    if not task or not wiki.exists():
        return []
    md = wiki.read_text(encoding="utf-8", errors="replace")
    if "## Manager-approved leads" not in md:
        return []
    body = md.split("## Manager-approved leads", 1)[1].split("\n## ", 1)[0]
    leads, cur = [], ""
    for line in body.splitlines():
        st = line.strip()
        if st.startswith(("- ", "* ")):
            if cur:
                leads.append(cur.strip())
            cur = st[2:]
        elif st and cur:
            cur += " " + st
    if cur:
        leads.append(cur.strip())
    return leads[:12]


def _task_evolution(task: str) -> dict:
    """What self-evolution has produced for THIS task: approved leads (full text),
    hypothesis funnel (approved/rejected/pending), and success/fail trace counts.
    This is 'what the loop learned from running this task', not a global tree."""
    wiki = ATOMIC_DIR / (task or "_") / "zeroshot" / "wiki.md"
    md = wiki.read_text(encoding="utf-8", errors="replace") if (task and wiki.exists()) else ""
    funnel = {"pending": 0, "approved": 0, "rejected": 0}
    for f in _queue_files("wiki_review", sub=""):
        p = _load_json(f)
        if p and p.get("task") == task and p.get("status") in funnel:
            funnel[p["status"]] += 1
    return {"task": task, "leads": _task_leads_text(task),
            "success": md.count("outcome: ✓ success"),
            "fail": md.count("outcome: ✗ failure"), "funnel": funnel}


def snapshot(lane: str = "A", session: str = "direct") -> dict:
    camp = _campaign(lane)
    roles, steps, reflection = _roles_and_steps(camp["current"])
    return {
        "generated_str": time.strftime("%Y-%m-%d %H:%M:%S"),
        "campaign": camp,
        "roles": roles,
        "steps": steps,
        "chain": _call_chain(camp["current"]),
        "task_evo": _task_evolution(camp["current"].get("task")),
        "reflection": reflection,
        "runs": _recent_runs(),
        "evolution": _evolution(),
        "conversation": list(_CONVO.get(session, [])),
        "session": session,
        "sessions": _list_sessions(),
        "has_frame": _newest_frame(lane) is not None,
    }


def _qparam(path: str, key: str, default: str) -> str:
    if "?" in path:
        for kv in path.split("?", 1)[1].split("&"):
            if kv.startswith(key + "="):
                from urllib.parse import unquote
                return unquote(kv.split("=", 1)[1]) or default
    return default


# ─────────────────────────── HTTP server ───────────────────────────

def _lane_of(path: str) -> str:
    """Extract ?lane=A|B (default A) from a request path."""
    if "?" in path:
        for kv in path.split("?", 1)[1].split("&"):
            if kv.startswith("lane="):
                v = kv.split("=", 1)[1].upper()
                return v if v in ("A", "B") else "A"
    return "A"


