#!/usr/bin/env bash
# Run a RoboRSI task WITH a live dashboard exposed over a Cloudflare tunnel.
# Usage: run_with_dashboard.sh <task> <seed> [gpu]
# Prints a public https://<random>.trycloudflare.com URL the user can open to
# watch the run in real time (Manager decision -> Planner -> Engineer steps +
# thinking -> Reviewer), plus a token panel.
set -u
TASK="${1:?task required}"; SEED="${2:-0}"; GPU="${3:-0}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROBOTWIN="${ROBORSI_ROBOTWIN_ROOT:?set ROBORSI_ROBOTWIN_ROOT to RoboTwin}"
BICOORD_ROOT="${ROBORSI_BICOORD_ROOT:?set ROBORSI_BICOORD_ROOT to BiCoord-Bench}"
PB=/tmp/pb
LOG="$PB/live_${TASK}_s${SEED}.log"
CFLOG="$PB/cf_${TASK}.log"
DLOG="$PB/dashsrv_${TASK}.log"
CLOUDFLARED="${CLOUDFLARED:-$(command -v cloudflared || true)}"

CONDA_BASE="${CONDA_BASE:-$HOME/miniconda3}"
source "$CONDA_BASE/etc/profile.d/conda.sh" && conda activate "${ROBORSI_CONDA_ENV:-RoboTwin}"
: > "$LOG"; touch "$LOG.running"

# 1. Dashboard on an OS-assigned free port.
setsid bash -c "python $REPO/scripts/live_dashboard.py --log '$LOG' --port 0 > '$DLOG' 2>&1" < /dev/null & disown
PORT=""
for i in $(seq 1 30); do
  PORT=$(grep -oE 'PORT=[0-9]+' "$DLOG" 2>/dev/null | head -1 | cut -d= -f2)
  [ -n "$PORT" ] && break; sleep 0.3
done
[ -z "$PORT" ] && { echo "dashboard failed to start"; cat "$DLOG"; exit 1; }
echo "[launcher] dashboard on 127.0.0.1:$PORT"

# 2. Cloudflare tunnel -> random public URL.
[ -n "$CLOUDFLARED" ] || { echo "cloudflared not found; set CLOUDFLARED"; exit 1; }
: > "$CFLOG"
setsid bash -c "$CLOUDFLARED tunnel --url http://localhost:$PORT > '$CFLOG' 2>&1" < /dev/null & disown
URL=""
for i in $(seq 1 40); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CFLOG" 2>/dev/null | head -1)
  [ -n "$URL" ] && break; sleep 0.5
done
echo "[launcher] PUBLIC_URL=${URL:-<tunnel-not-ready>}"
echo "$URL" > "$PB/dash_url_${TASK}.txt"

# 3. Run the task, streaming stdout into the dashboard log.
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 LANG=C.UTF-8 LC_ALL=C.UTF-8
export ROBORSI_ROBOTWIN_ROOT="$ROBOTWIN" ROBORSI_BICOORD_ROOT="$BICOORD_ROOT"
export ROBORSI_DIRECT_3ROLE=1 CUDA_VISIBLE_DEVICES=$GPU
export PYTHONPATH=$REPO
timeout 1500 python "$REPO/scripts/cli_3role.py" "$TASK" --seed "$SEED" >> "$LOG" 2>&1
RC=$?
rm -f "$LOG.running"
echo "[launcher] run finished rc=$RC" >> "$LOG"
echo "[launcher] DONE rc=$RC url=$URL"
