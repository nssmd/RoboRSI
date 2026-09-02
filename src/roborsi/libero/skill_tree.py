# ruff: noqa: E501
"""Standalone RoboRSI skill-tree visualization."""

from __future__ import annotations

import json
import re
from html import escape
from importlib import resources
from pathlib import Path
from typing import Any

SCHEMA = "roborsi.skill-tree-storyboard.v1"
CAMPAIGN_SCHEMA = "roborsi.campaign-skill-tree.v1"

ATOMIC_NODES = (
    ("sweep", "ACTIVE VIEW"),
    ("move_source", "MOVE TO OBJECT"),
    ("pick", "PICK UP OBJECT"),
    ("move_dest", "MOVE TO TARGET"),
    ("place", "PLACE OBJECT"),
    ("handoff", "TRACK PROGRESS"),
)

BASE_GROUPS = (
    (
        "NAVIGATION",
        (
            ("map_localize", "ROBOT POSITION"),
            ("sweep_control", "VIEWPOINT CONTROL"),
            ("chassis_nav", "BASE NAVIGATION"),
        ),
    ),
    (
        "VISUAL PERCEPTION",
        (
            ("capture_seg", "IMAGE MASKING"),
            ("source_binding", "OBJECT IDENTITY"),
            ("dest_geometry", "TARGET REGION"),
        ),
    ),
    (
        "MOTION PLANNING",
        (
            ("station_plan", "GRASP POSITION"),
            ("grasp_plan", "GRASP PLANNING"),
            ("gripper_hold", "GRIPPER CONTROL"),
            ("release_verify", "RELEASE / VERIFY"),
        ),
    ),
    (
        "STATE PERCEPTION",
        (
            ("held_state", "GRASP STATUS"),
            ("success_gate", "SUCCESS EVIDENCE"),
        ),
    ),
    (
        "ITERATION SUPPORT",
        (
            ("checkpoint", "PROGRESS SAVE"),
            ("health_dock", "POWER / DOCKING"),
            ("trace_eval", "TEST / REVIEW"),
        ),
    ),
)

PRIVATE_EVENT_FIELDS = {
    "repair_ids",
    "source_run_name",
    "source_runtime_id",
    "source_started_at_local",
}


def default_storyboard_text() -> str:
    resource = resources.files("roborsi.libero").joinpath("assets/skill_tree_storyboard.json")
    return resource.read_text(encoding="utf-8")


def sanitize_storyboard(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove run-local metadata and normalize public RoboRSI branding."""
    events = []
    for raw_event in payload.get("events") or []:
        if not isinstance(raw_event, dict):
            raise ValueError("storyboard events must be objects")
        event = {
            str(key): value for key, value in raw_event.items() if key not in PRIVATE_EVENT_FIELDS
        }
        events.append(event)
    return {
        "schema_version": SCHEMA,
        "title": str(payload.get("title") or "SKILL TREE"),
        "subtitle": "ROBORSI SELF-EVOLUTION / ONE TASK",
        "task": str(payload.get("task") or "ROBOT TASK"),
        "round_count": int(payload.get("round_count") or len(events)),
        "events": events,
        "color_semantics": {
            "blue": "new skills or capabilities",
            "yellow": "debugging or repair",
            "green": "stable skills",
        },
    }


def validate_storyboard(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA:
        raise ValueError(f"unsupported skill-tree schema: {payload.get('schema_version')}")
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("skill-tree storyboard requires at least one event")
    round_count = int(payload.get("round_count") or 0)
    rounds = [int(event.get("round", -1)) for event in events if isinstance(event, dict)]
    if len(rounds) != len(events) or rounds != list(range(1, round_count + 1)):
        raise ValueError("storyboard rounds must be consecutive from 1 to round_count")
    valid_nodes = {
        "task",
        *(key for key, _ in ATOMIC_NODES),
        *(key for _, nodes in BASE_GROUPS for key, _ in nodes),
    }
    for event in events:
        for field in ("atomics", "bases", "branch_additions", "finalizes"):
            values = event.get(field) or []
            if not isinstance(values, list):
                raise ValueError(f"event {event['round']} field {field} must be a list")
            unknown = sorted(set(map(str, values)) - valid_nodes)
            if unknown:
                raise ValueError(f"event {event['round']} contains unknown {field}: {unknown}")
        leaked = PRIVATE_EVENT_FIELDS.intersection(event)
        if leaked:
            raise ValueError(f"event {event['round']} exposes internal fields: {sorted(leaked)}")
    return payload


def load_storyboard(path: Path | str | None = None) -> dict[str, Any]:
    text = (
        default_storyboard_text()
        if path is None
        else Path(path).expanduser().read_text(encoding="utf-8")
    )
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("skill-tree storyboard must be a JSON object")
    if payload.get("schema_version") != SCHEMA:
        payload = sanitize_storyboard(payload)
    return validate_storyboard(payload)


def _node_payload() -> dict[str, Any]:
    return {
        "atomics": [{"id": key, "label": label} for key, label in ATOMIC_NODES],
        "groups": [
            {
                "label": label,
                "nodes": [{"id": key, "label": node_label} for key, node_label in nodes],
            }
            for label, nodes in BASE_GROUPS
        ],
    }


def _safe_json(payload: Any) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def build_skill_tree_html(storyboard: dict[str, Any]) -> str:
    validate_storyboard(storyboard)
    data = _safe_json(storyboard)
    nodes = _safe_json(_node_payload())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoboRSI Skill Tree</title>
<style>
:root{{--bg:#090b0e;--panel:#11151a;--line:#343b44;--text:#eef1f4;--muted:#929aa5;
--blue:#4ca6ff;--yellow:#f8ca48;--green:#45cf87;--node:#23282e}}
*{{box-sizing:border-box}} html,body{{margin:0;min-height:100%;background:var(--bg);color:var(--text);
font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
body{{background-image:linear-gradient(#161a1f 1px,transparent 1px),
linear-gradient(90deg,#161a1f 1px,transparent 1px);background-size:48px 48px}}
.shell{{min-width:1180px;max-width:1920px;margin:0 auto;padding:30px 34px 24px}}
.top{{display:flex;align-items:end;justify-content:space-between;border-bottom:1px solid var(--line);
padding-bottom:14px}} .eyebrow,.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
letter-spacing:0;color:var(--muted);font-weight:700}} h1{{margin:8px 0 0;font-size:52px;line-height:1}}
.legend{{display:flex;gap:24px;font-size:16px}} .legend b:nth-child(1){{color:var(--blue)}}
.legend b:nth-child(2){{color:var(--yellow)}} .legend b:nth-child(3){{color:var(--green)}}
.layout{{display:grid;grid-template-columns:minmax(0,1fr) 420px;gap:28px;margin-top:22px}}
.tree-wrap{{position:relative;min-height:720px}} svg{{width:100%;height:720px;display:block}}
.panel{{background:var(--panel);border:1px solid #3a424c;border-radius:8px;padding:28px;min-height:680px}}
.round{{font:700 44px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--blue)}}
.tag{{display:inline-flex;align-items:center;gap:10px;margin-top:26px;font:700 18px/1.2 ui-monospace,monospace}}
.tag:before{{content:"";width:10px;height:26px;background:currentColor}} .mode{{margin:14px 0 18px;color:var(--muted);
font:700 14px/1.2 ui-monospace,monospace}} .panel hr{{border:0;border-top:1px solid var(--line)}}
.panel h2{{font-size:24px;line-height:1.25;margin:28px 0 14px}} .panel p{{font-size:18px;line-height:1.45;color:#aeb5be}}
.label{{margin-top:28px;color:var(--blue);font:700 14px/1.2 ui-monospace,monospace}}
.affected{{font-size:15px;line-height:1.45;font-weight:700}} .controls{{display:grid;grid-template-columns:44px 1fr 100px;
gap:14px;align-items:center;margin-top:14px}} button{{width:44px;height:38px;border:1px solid #4b5662;
border-radius:6px;background:#171c22;color:white;cursor:pointer}} button:hover{{border-color:var(--blue)}}
.play-icon{{display:inline-block;width:0;height:0;border-top:7px solid transparent;border-bottom:7px solid transparent;
border-left:11px solid currentColor;margin-left:3px}} button.playing .play-icon{{width:10px;height:14px;border:0;
border-left:4px solid currentColor;border-right:4px solid currentColor;margin-left:0}}
input[type=range]{{width:100%;accent-color:var(--blue)}} .counter{{text-align:right;font:700 15px ui-monospace,monospace;color:var(--muted)}}
.node rect{{fill:var(--node);stroke:#5a636d;stroke-width:1.5;rx:7}} .node text{{fill:#dfe4e9;font-weight:700;
font-size:13px;text-anchor:middle;dominant-baseline:middle}} .node.atomic text{{font-size:15px}}
.node.new rect{{fill:#1d3852;stroke:var(--blue);stroke-width:2.5}} .node.new text{{fill:white}}
.node.repair rect{{fill:#463b17;stroke:var(--yellow);stroke-width:2.5}} .node.repair text{{fill:white}}
.node.stable rect{{fill:#173e2c;stroke:var(--green);stroke-width:2.5}} .node.stable text{{fill:white}}
.connector{{stroke:#4e5862;stroke-width:1.5;fill:none}} .connector.active{{stroke:var(--yellow)}}
.connector.stable{{stroke:var(--green)}} .tree-label{{fill:var(--muted);font:700 14px ui-monospace,monospace}}
.root-title{{font-size:25px!important}} .status-note{{position:absolute;bottom:16px;left:18px;color:var(--muted);
font:700 14px ui-monospace,monospace}} @media(max-width:1250px){{.shell{{min-width:1120px}}}}
</style>
</head>
<body>
<main class="shell">
  <header class="top">
    <div><div class="eyebrow">ROBORSI SELF-EVOLUTION / ONE TASK</div><h1>SKILL TREE</h1></div>
    <div class="legend"><span><b>BLUE:</b> new capability</span><span><b>YELLOW:</b> repair</span>
    <span><b>GREEN:</b> stable capability</span></div>
  </header>
  <section class="layout">
    <div class="tree-wrap"><svg id="tree" viewBox="0 0 1400 720" role="img"
    aria-label="RoboRSI evolving skill tree"></svg><div class="status-note" id="coverage"></div></div>
    <aside class="panel">
      <div class="round" id="round"></div><div class="tag" id="tag"></div>
      <div class="mode" id="mode"></div><hr><h2 id="headline"></h2><p id="summary"></p>
      <div class="label" id="detail-label"></div><p id="change"></p>
      <div class="label">ATOMIC SKILLS AFFECTED</div><p class="affected" id="atomics"></p>
      <div class="label">BASE SKILLS AFFECTED</div><p class="affected" id="bases"></p>
    </aside>
  </section>
  <div class="controls"><button id="play" aria-label="Play animation"><span class="play-icon"></span></button>
  <input id="timeline" type="range" min="1" max="{int(storyboard["round_count"])}" value="1">
  <div class="counter" id="counter"></div></div>
</main>
<script id="storyboard" type="application/json">{data}</script>
<script id="node-data" type="application/json">{nodes}</script>
<script>
const story=JSON.parse(document.getElementById("storyboard").textContent);
const nodeData=JSON.parse(document.getElementById("node-data").textContent);
const svg=document.getElementById("tree"), NS="http://www.w3.org/2000/svg";
const states=new Map(), finalRound=new Map(), addRound=new Map();
for(const event of story.events){{for(const id of event.finalizes||[])if(!finalRound.has(id))finalRound.set(id,event.round);
for(const id of event.branch_additions||[])if(!addRound.has(id))addRound.set(id,event.round)}}
function el(name,attrs={{}}){{const node=document.createElementNS(NS,name);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);return node}}
function line(x1,y1,x2,y2,cls="connector"){{svg.appendChild(el("line",{{x1,y1,x2,y2,class:cls}}))}}
function node(id,label,x,y,w,h,kind=""){{const g=el("g",{{class:`node ${{kind}}`,"data-node":id}});
g.appendChild(el("rect",{{x:x-w/2,y:y-h/2,width:w,height:h}}));const t=el("text",{{x,y}});
const words=label.split(" ");if(words.length>1&&!kind.includes("atomic")){{const mid=Math.ceil(words.length/2);
for(const [i,text] of [words.slice(0,mid).join(" "),words.slice(mid).join(" ")].entries()){{const s=el("tspan",{{x,dy:i?18:-8}});s.textContent=text;t.appendChild(s)}}}}else t.textContent=label;
g.appendChild(t);svg.appendChild(g);states.set(id,g)}}
const rootX=700,rootY=100;node("task",story.task,rootX,rootY,300,72,"atomic root");
const ax=[145,365,585,805,1025,1245], ay=285;line(rootX,136,rootX,220);line(145,220,1245,220);
nodeData.atomics.forEach((n,i)=>{{line(ax[i],220,ax[i],247);node(n.id,n.label,ax[i],ay,190,70,"atomic")}});
const flat=nodeData.groups.flatMap(g=>g.nodes.map(n=>({{...n,group:g.label}})));const bx=flat.map((_,i)=>55+i*(1290/(flat.length-1))),by=540;
line(145,320,145,420);line(145,420,1245,420);line(1245,320,1245,420);
flat.forEach((n,i)=>{{line(bx[i],420,bx[i],505);node(n.id,n.label,bx[i],by,82,68)}});
let groupStart=0;for(const group of nodeData.groups){{const count=group.nodes.length,end=groupStart+count-1,x1=bx[groupStart],x2=bx[end],cx=(x1+x2)/2;
const label=el("text",{{x:cx,y:468,class:"tree-label","text-anchor":"middle"}});label.textContent=group.label;svg.appendChild(label);groupStart+=count}}
const slider=document.getElementById("timeline"),play=document.getElementById("play");let timer=null;
function setState(id,state){{const n=states.get(id);if(!n)return;n.classList.remove("new","repair","stable");if(state)n.classList.add(state)}}
function render(round){{const event=story.events[round-1],affected=new Set([...(event.atomics||[]),...(event.bases||[])]);
for(const id of states.keys()){{let s="";if((finalRound.get(id)||Infinity)<=round)s="stable";else if(addRound.get(id)===round)s="new";else if(affected.has(id))s="repair";setState(id,s)}}
document.getElementById("round").textContent=`ROUND ${{String(round).padStart(3,"0")}} / ${{story.round_count}}`;
const tag=document.getElementById("tag");tag.textContent=event.emphasis_label||"ENGINEERING UPDATE";
tag.style.color=(event.branch_additions||[]).length?"var(--blue)":(event.finalizes||[]).length?"var(--green)":"var(--yellow)";
document.getElementById("mode").textContent=event.mode||"";
document.getElementById("headline").textContent=event.headline||"";
document.getElementById("summary").textContent=event.summary||"";
document.getElementById("detail-label").textContent=event.detail_label||"ENGINEERING CHANGE";
document.getElementById("change").textContent=event.change||"";
const atomicIds=event.atomics||[],baseIds=event.bases||[];
document.getElementById("atomics").textContent=atomicIds.length===nodeData.atomics.length?`ALL ${{atomicIds.length}} ATOMIC SKILLS`:atomicIds.map(id=>nodeData.atomics.find(n=>n.id===id)?.label||id).join(" / ")||"None";
document.getElementById("bases").textContent=baseIds.length===flat.length?`ALL ${{baseIds.length}} BASE SKILLS`:baseIds.map(id=>flat.find(n=>n.id===id)?.label||id).join(" / ")||"None";
document.getElementById("counter").textContent=`${{round}} / ${{story.round_count}}`;document.getElementById("coverage").textContent=`${{story.round_count}}-ROUND DEVELOPMENT TIMELINE`;
slider.value=round}}
function stop(){{if(timer)clearInterval(timer);timer=null;play.classList.remove("playing");play.setAttribute("aria-label","Play animation")}}
function start(){{stop();play.classList.add("playing");play.setAttribute("aria-label","Pause animation");timer=setInterval(()=>{{let n=Number(slider.value)+1;if(n>story.round_count)n=1;render(n)}},650)}}
play.addEventListener("click",()=>timer?stop():start());slider.addEventListener("input",()=>{{stop();render(Number(slider.value))}});
const requestedRound=Number(new URLSearchParams(location.search).get("round"));
render(Number.isFinite(requestedRound)?Math.max(1,Math.min(story.round_count,requestedRound)):1);
</script>
</body>
</html>
"""


def write_skill_tree_html(
    output: Path | str,
    *,
    storyboard_path: Path | str | None = None,
) -> Path:
    storyboard = load_storyboard(storyboard_path)
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_skill_tree_html(storyboard), encoding="utf-8")
    return destination


def _path_number(path: Path, prefix: str) -> int:
    for part in path.parts:
        match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", part)
        if match:
            return int(match.group(1))
    return -1


def load_campaign_skill_tree(
    campaign_root: Path | str,
    *,
    task_key: str | None = None,
) -> dict[str, Any]:
    from roborsi.embodied.sim.libero.run_records import load_records

    root = Path(campaign_root).expanduser().resolve()
    plans = []
    for path in sorted((root / "episodes").rglob("roles/plan.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema") != "roborsi.top_down_plan.v1":
            continue
        plans.append(
            {
                "path": path,
                "seed": _path_number(path, "seed"),
                "attempt": _path_number(path, "attempt"),
                "plan": payload,
            }
        )
    if not plans:
        raise ValueError(f"campaign has no retained top-down plans: {root}")
    selected_task = task_key or str(plans[-1]["plan"].get("task_key") or "")
    selected = [row for row in plans if str(row["plan"].get("task_key") or "") == selected_task]
    if not selected:
        raise ValueError(f"campaign has no top-down plan for task: {selected_task}")

    records = [
        record
        for journal in sorted((root / "journals").glob("*.episodes.jsonl"))
        for record in load_records(journal)
        if record.identity.task_key == selected_task
    ]
    record_index = {
        (int(record.identity.seed), int(record.identity.attempt)): record for record in records
    }
    selected.sort(key=lambda row: (row["seed"], row["attempt"], str(row["path"])))
    rounds = []
    for index, row in enumerate(selected, 1):
        plan = row["plan"]
        record = record_index.get((row["seed"], row["attempt"]))
        rounds.append(
            {
                "round": index,
                "seed": row["seed"],
                "attempt": row["attempt"],
                "release_id": str(record.release_id or "") if record else "",
                "verdict": str(record.category or "running") if record else "running",
                "steps": [
                    {
                        "id": str(step.get("id") or f"step-{step_index}"),
                        "goal": str(step.get("goal") or ""),
                        "skills": [str(value) for value in step.get("skills") or []],
                    }
                    for step_index, step in enumerate(plan.get("steps") or [], 1)
                    if isinstance(step, dict)
                ],
            }
        )

    promotions = []
    for path in sorted((root / "proposals").glob("*.json")):
        try:
            proposal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(proposal.get("benchmark_task") or "") != selected_task:
            continue
        promotions.append(
            {
                "name": str((proposal.get("payload") or {}).get("name") or ""),
                "status": str(proposal.get("status") or ""),
                "release_id": str(proposal.get("release_id") or ""),
                "validation_seeds": [
                    int(value) for value in proposal.get("validation_seeds") or []
                ],
            }
        )

    plan = selected[-1]["plan"]
    return {
        "schema_version": CAMPAIGN_SCHEMA,
        "run_id": root.name,
        "task_key": selected_task,
        "task_family": str(plan.get("task_family") or ""),
        "atomic_task": str(plan.get("atomic_task") or ""),
        "rounds": rounds,
        "promotions": promotions,
    }


def build_campaign_skill_tree_html(payload: dict[str, Any]) -> str:
    if payload.get("schema_version") != CAMPAIGN_SCHEMA:
        raise ValueError("unsupported campaign skill-tree schema")
    rounds = payload.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise ValueError("campaign skill tree requires retained rounds")
    data = _safe_json(payload)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RoboRSI Campaign Skill Tree</title>
<style>
:root{{--paper:#fbfcfd;--ink:#142735;--muted:#6e7d84;--line:#cbd8de;--blue:#007dce;
--sky:#00bfe8;--green:#79a900;--yellow:#e6a120;--red:#d84b35}}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--paper);color:var(--ink);
font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
body{{background-image:linear-gradient(rgba(0,125,206,.035) 1px,transparent 1px),
linear-gradient(90deg,rgba(0,125,206,.035) 1px,transparent 1px);background-size:42px 42px}}
.shell{{width:min(1480px,calc(100% - 48px));margin:0 auto;padding:34px 0 28px}}
.top{{display:flex;justify-content:space-between;gap:32px;align-items:end;border-bottom:1px solid var(--line);
padding-bottom:18px}}.eyebrow,.mono{{font:700 12px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;
color:var(--blue)}}h1{{margin:8px 0 0;font:650 42px/1.05 Georgia,serif;letter-spacing:0}}
.identity{{text-align:right;color:var(--muted);font-size:14px;line-height:1.5}}
.layout{{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:34px;margin-top:30px}}
.tree{{min-height:610px}}.layer{{position:relative;padding:0 0 42px}}.layer:not(:last-child)::after{{
content:"";position:absolute;left:50%;bottom:10px;width:1px;height:24px;background:var(--line)}}
.layer-label{{margin-bottom:12px;font:700 11px/1.2 ui-monospace,monospace;color:var(--muted);
text-align:center}}.nodes{{display:flex;flex-wrap:wrap;justify-content:center;gap:10px}}
.node{{min-width:150px;max-width:280px;padding:12px 14px;border:1px solid var(--line);background:white;
border-radius:6px;text-align:center;box-shadow:0 8px 24px rgba(20,39,53,.04)}}
.node strong{{display:block;font-size:14px;line-height:1.3}}.node small{{display:block;margin-top:5px;
color:var(--muted);font-size:11px;line-height:1.35}}.node.new{{border-color:var(--blue);background:#eef8ff}}
.node.repair{{border-color:var(--yellow);background:#fff8e8}}.node.stable{{border-color:#9bbf49;background:#f5fae9}}
.panel{{border-left:1px solid var(--line);padding-left:28px}}.round{{font:700 28px/1 ui-monospace,monospace;
color:var(--blue)}}.verdict{{display:inline-block;margin:18px 0 8px;padding:5px 8px;border:1px solid currentColor;
border-radius:4px;font:700 11px/1 ui-monospace,monospace}}.verdict.success{{color:var(--green)}}
.verdict.failure{{color:var(--red)}}.panel h2{{font-size:22px;line-height:1.25;margin:12px 0}}
.panel p{{color:var(--muted);line-height:1.55}}.promotion{{margin-top:26px;padding-top:20px;border-top:1px solid var(--line)}}
.promotion li{{margin:8px 0;color:var(--muted)}}.controls{{display:grid;grid-template-columns:42px 1fr 80px;
gap:12px;align-items:center;margin-top:22px}}button{{width:42px;height:38px;border:1px solid var(--line);
background:white;color:var(--blue);border-radius:5px;cursor:pointer}}input{{width:100%;accent-color:var(--blue)}}
.counter{{text-align:right;font:700 12px ui-monospace,monospace;color:var(--muted)}}
@media(max-width:900px){{.layout{{grid-template-columns:1fr}}.panel{{border-left:0;border-top:1px solid var(--line);
padding:24px 0 0}}.top{{align-items:start;flex-direction:column}}.identity{{text-align:left}}}}
</style>
</head>
<body>
<main class="shell">
  <header class="top">
    <div><div class="eyebrow">ROBORSI / RETAINED CAMPAIGN PLAN</div><h1>Top-down Skill Tree</h1></div>
    <div class="identity"><div>{escape(str(payload["run_id"]))}</div>
    <div>{escape(str(payload["task_key"]))}</div></div>
  </header>
  <section class="layout">
    <div class="tree" id="tree"></div>
    <aside class="panel">
      <div class="round" id="round"></div><div class="verdict" id="verdict"></div>
      <h2 id="headline"></h2><p id="detail"></p>
      <div class="promotion"><div class="mono">PROMOTION RECORDS</div><ul id="promotions"></ul></div>
    </aside>
  </section>
  <div class="controls"><button id="play" aria-label="Play rounds">▶</button>
  <input id="timeline" type="range" min="1" max="{len(rounds)}" value="1">
  <div class="counter" id="counter"></div></div>
</main>
<script id="payload" type="application/json">{data}</script>
<script>
const data=JSON.parse(document.getElementById("payload").textContent);
const tree=document.getElementById("tree"),slider=document.getElementById("timeline");
const play=document.getElementById("play");let timer=null;
function node(label,detail,state){{const el=document.createElement("div");el.className=`node ${{state||""}}`;
const strong=document.createElement("strong");strong.textContent=label;el.appendChild(strong);
if(detail){{const small=document.createElement("small");small.textContent=detail;el.appendChild(small)}}return el}}
function layer(label,nodes){{const wrap=document.createElement("section");wrap.className="layer";
const title=document.createElement("div");title.className="layer-label";title.textContent=label;
wrap.appendChild(title);const row=document.createElement("div");
row.className="nodes";nodes.forEach(n=>row.appendChild(n));wrap.appendChild(row);return wrap}}
function render(index){{const current=data.rounds[index],prior=data.rounds.slice(0,index);
const stable=new Set(prior.filter(r=>r.verdict==="task_success").flatMap(r=>r.steps.flatMap(s=>s.skills)));
const currentSkills=new Set(current.steps.flatMap(s=>s.skills));tree.replaceChildren();
tree.appendChild(layer("TASK FAMILY",[node(data.task_family,"Reusable decomposition","stable")]));
tree.appendChild(layer("ATOMIC TASK",[node(data.atomic_task,data.task_key,"stable")]));
tree.appendChild(layer("PLANNER STEPS",current.steps.map(s=>node(s.id,s.goal,
current.verdict==="task_failure"?"repair":"new"))));
const skills=[...currentSkills].map(name=>node(name,"Published visible capability",
current.verdict==="task_failure"?"repair":stable.has(name)?"stable":"new"));
tree.appendChild(layer("BASE / COMPOUND SKILLS",skills.length?skills:[node("No selected skill","Planner fallback","repair")]));
document.getElementById("round").textContent=`ROUND ${{index+1}} / ${{data.rounds.length}}`;
const verdict=document.getElementById("verdict");verdict.textContent=current.verdict.replaceAll("_"," ");
verdict.className=`verdict ${{current.verdict==="task_success"?"success":current.verdict.includes("failure")?"failure":""}}`;
document.getElementById("headline").textContent=`Seed ${{current.seed}} · Attempt ${{current.attempt}}`;
document.getElementById("detail").textContent=current.release_id?`Release: ${{current.release_id}}`:"Release pending";
document.getElementById("counter").textContent=`${{index+1}} / ${{data.rounds.length}}`;slider.value=index+1}}
const promotions=document.getElementById("promotions");
const promotionRows=data.promotions.length?data.promotions:[{{name:"None retained for this task.",status:"",validation_seeds:[]}}];
for(const p of promotionRows){{const item=document.createElement("li"),strong=document.createElement("strong");
strong.textContent=p.name;item.appendChild(strong);
if(p.status){{item.appendChild(document.createTextNode(` · ${{p.status}}${{p.validation_seeds.length?` · seeds ${{p.validation_seeds.join(", ")}}`:""}}`))}}
promotions.appendChild(item)}}
function stop(){{if(timer)clearInterval(timer);timer=null;play.textContent="▶"}}
function start(){{stop();play.textContent="Ⅱ";timer=setInterval(()=>{{let next=Number(slider.value);
if(next>=data.rounds.length)next=0;render(next)}},900)}}
play.addEventListener("click",()=>timer?stop():start());slider.addEventListener("input",()=>{{stop();render(Number(slider.value)-1)}});
render(0);
</script>
</body>
</html>
"""


def write_campaign_skill_tree_html(
    campaign_root: Path | str,
    output: Path | str,
    *,
    task_key: str | None = None,
) -> Path:
    payload = load_campaign_skill_tree(campaign_root, task_key=task_key)
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_campaign_skill_tree_html(payload), encoding="utf-8")
    return destination
