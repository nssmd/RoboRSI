#!/usr/bin/env bash
# Loop scripts/cli_3role.py over a LIST of tasks x seeds, in the RoboTwin env.
# Surfaces every NEW proposal to a pending-review log — NEVER auto-applies
# (the Manager reviews + applies). Each task runs sequentially within the loop;
# launch two of these (LH set / atomic set) for parallel coverage.
#
# Usage: GPU=0 bash scripts/loop_3role.sh <label> <start_seed> <rounds> <task...>
set -u
LABEL="${1:?label}"; START_SEED="${2:?start_seed}"; ROUNDS="${3:?rounds}"; shift 3
TASKS=("$@")
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BICOORD_ROOT="${ROBORSI_BICOORD_ROOT:?set ROBORSI_BICOORD_ROOT to BiCoord-Bench}"
WORKDIR=/tmp/loop_3role/$LABEL
mkdir -p "$WORKDIR"
MASTER="$WORKDIR/loop.log"
SKILLQ="$HOME/.roborsi/skill_review"
WIKIQ="$HOME/.roborsi/wiki_review"
GPU="${GPU:-0}"

CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"
source "$CONDA_BASE/etc/profile.d/conda.sh" && conda activate "${ROBORSI_CONDA_ENV:-RoboTwin}"
# Force UTF-8 across the whole (Python 3.10) process: the RoboTwin env runs
# headless with a C locale → default ASCII codec → em-dash (—) in reviewer
# prompts / Chinese summaries crashes with UnicodeEncodeError. PYTHONUTF8=1
# fixes stdout/stderr/subprocess pipes; the CodexRunner Popen also pins utf-8.
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 LANG=C.UTF-8 LC_ALL=C.UTF-8
log() { echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$MASTER"; }
qcount() { ls "$SKILLQ"/*.json "$WIKIQ"/*.json 2>/dev/null | wc -l; }

log "=== LOOP START label=$LABEL gpu=$GPU seeds=${START_SEED}+${ROUNDS} tasks=${TASKS[*]} ==="
for R in $(seq 1 "$ROUNDS"); do
  SEED=$((START_SEED + R - 1))
  for T in "${TASKS[@]}"; do
    BEFORE=$(qcount)
    RLOG="$WORKDIR/${T}_seed${SEED}.log"
    log "--- round $R/$ROUNDS  task=$T  seed=$SEED  -> ${RLOG##*/} ---"
    cd "$BICOORD_ROOT"
    CUDA_VISIBLE_DEVICES="$GPU" python "$REPO/scripts/cli_3role.py" "$T" --seed "$SEED" \
        > "$RLOG" 2>&1
    AFTER=$(qcount); NEW=$((AFTER - BEFORE))
    OUT=$(grep -ohE "completed_atomics=[0-9]+/[0-9]+|2/[0-9]+|success=(True|False)" "$RLOG" | tail -1)
    log "    done task=$T seed=$SEED result=${OUT:-?} new_proposals=$NEW"
    if [ "$NEW" -gt 0 ]; then
      log "    ** $NEW NEW PROPOSAL(S) queued — Manager review **"
      ls -t "$SKILLQ"/*.json "$WIKIQ"/*.json 2>/dev/null | head -"$NEW" \
          | tee -a "$WORKDIR/pending_review.log"
    fi
  done
done
log "=== LOOP DONE label=$LABEL ==="
