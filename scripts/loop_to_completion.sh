#!/usr/bin/env bash
# Drive each task to COMPLETION for the RoboTwin self-evolution campaign.
#
#   - each task iterates up to N rounds, breaking as soon as the task GENUINELY
#     succeeds (the 3-role path's final `bot> ✓`, with legacy-fallback runs
#     rejected — their "2/2" is a fake claim);
#   - between rounds, newly-queued base/robotwin proposals are applied through
#     the harness gate (flock-serialised so two parallel campaigns don't clash)
#     so the NEXT round actually improves;
#   - after a task finishes, its persistent planner + reviewer sessions are
#     just CLEARED — no explicit memory save: durable knowledge already lives in
#     the task wiki + plan.md, so the next task starts a fresh session.
#
# Usage: GPU=0 bash scripts/loop_to_completion.sh <label> <rounds> <task...>
set -u
LABEL="${1:?label}"; ROUNDS="${2:?rounds}"; shift 2; TASKS=("$@")
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BICOORD_ROOT="${ROBORSI_BICOORD_ROOT:?set ROBORSI_BICOORD_ROOT to BiCoord-Bench}"
ROBOTWIN_ROOT="${ROBORSI_ROBOTWIN_ROOT:?set ROBORSI_ROBOTWIN_ROOT to RoboTwin}"
WORK=/tmp/loop_complete/$LABEL; mkdir -p "$WORK"
MASTER="$WORK/loop.log"; SKILLQ="$HOME/.roborsi/skill_review"; GPU="${GPU:-0}"
LOCK=/tmp/loop_complete/.apply.lock

CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"
source "$CONDA_BASE/etc/profile.d/conda.sh" && conda activate "${ROBORSI_CONDA_ENV:-RoboTwin}"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 LANG=C.UTF-8 LC_ALL=C.UTF-8
export ROBORSI_BICOORD_ROOT="$BICOORD_ROOT"
export ROBORSI_ROBOTWIN_ROOT="$ROBOTWIN_ROOT"
log(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$MASTER"; }

succeeded(){   # genuine success only: 3-role path's FINAL `bot> ✓`, and the run
               # did NOT silently fall back to the legacy loop (fake "2/2").
  grep -q "CRASHED → falling back to legacy" "$1" 2>/dev/null && return 1
  local last; last=$(grep -E "^bot> " "$1" 2>/dev/null | tail -1)
  [ -n "$last" ] && echo "$last" | grep -q "✓" && ! echo "$last" | grep -q "✗"; }

apply_gated_base(){   # flock-serialised: apply NEW pending base/robotwin proposals via the gate
  ( flock 9
    for f in "$SKILLQ"/*.json; do
      [ -f "$f" ] || continue
      read -r st pid cat nm < <(python3 - "$f" <<'PY'
import json,sys
d=json.load(open(sys.argv[1])); pp=d.get('proposal_payload') or {}
print(d.get('status','pending'), d.get('id',''), (pp.get('category') or d.get('category','')), (d.get('name') or pp.get('name','')))
PY
)
      [ "$st" = pending ] && [ "$cat" = "base/robotwin" ] || continue
      # cron policy (proposal-review point 4): CORE grasp / dual-arm skills are
      # NOT auto-applied. The grasp_holds_actor gate is too high-variance to
      # validate them on one roll (the SAME code was seen 3/3 in one run and
      # 0/3 in another, so a lucky roll would commit an unproven change to a
      # skill every grasp task depends on). Leave them pending for manual review.
      case "$nm" in
        grasp_object|grasp_then_lift_graspgen|grasp_then_lift|move_dual_arm|pick_actor)
          log "      skip(core, needs manual review) $pid $nm"; continue;;
      esac
      log "      apply(gated) $pid"
      CUDA_VISIBLE_DEVICES="$GPU" python "$REPO/scripts/apply_selfevo_proposal.py" "$pid" \
          >> "$WORK/applies.log" 2>&1 && log "        -> applied" || log "        -> gate blocked"
    done
  ) 9>"$LOCK"
}

log "=== TO-COMPLETION START label=$LABEL rounds=$ROUNDS tasks=${TASKS[*]} ==="
for T in "${TASKS[@]}"; do
  DONE=no
  for R in $(seq 1 "$ROUNDS"); do
    SEED=$((20 + R))
    RLOG="$WORK/${T}_r${R}.log"
    log "--- task=$T round $R/$ROUNDS seed=$SEED ---"
    cd "$BICOORD_ROOT"
    CUDA_VISIBLE_DEVICES="$GPU" python "$REPO/scripts/cli_3role.py" "$T" --seed "$SEED" > "$RLOG" 2>&1
    if succeeded "$RLOG"; then
      log "    ✓✓ task=$T GENUINELY COMPLETE at round $R/$ROUNDS"; DONE=yes; break
    fi
    last=$(grep -E "^bot> " "$RLOG" 2>/dev/null | tail -1 | cut -c1-80)
    log "    ✗ round $R not complete (${last:-no verdict}) — applying gated proposals, retry"
    apply_gated_base
  done
  [ "$DONE" = yes ] || log "    !! task=$T NOT completed after $ROUNDS rounds — needs Manager attention"
  log "    clearing $T sessions (fresh next task; durable knowledge in wiki + plan.md)"
  python3 -c "from roborsi.agents import persistent_agent as p; p.clear('planner','$T'); p.clear('reviewer','$T')" >> "$WORK/clears.log" 2>&1 || true
done
log "=== TO-COMPLETION DONE label=$LABEL ==="
