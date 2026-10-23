#!/usr/bin/env bash
# One-click LIBERO-Plus evaluation of the current release, frozen.
#
# End-to-end: environment → install → LIBERO-Plus checkout (sylvestf/LIBERO-plus
# @ 4976dc3) → official assets (HF: Sylvest/LIBERO-plus) → backend configure +
# doctor → PyRoKi IK/trajopt service → frozen campaign over the 840-instance
# panel (roborsi/evaluation/panels/libero_plus_840.json: 7 perturbation types
# x 120, seed 5) → journal audit → per-perturbation table.
#
# Frozen means no skill, task wiki or plan is written. The Planner and Reviewer
# still keep one persistent session per instance.
#
# Requirements: Linux, git, python3.12+, an NVIDIA/CUDA stack for the LIBERO
# renderer, and an OpenAI-compatible endpoint:
#   ROBORSI_EVAL_MODEL  fallback model; per-role ROBORSI_*_MODEL takes priority
#   API key for Engineer/perception: OPENAI_API_KEY, DEEPSEEK_API_KEY,
#   MOONSHOT_API_KEY or ANTHROPIC_API_KEY. See docs/MODEL_API_ADAPTATION.md.
#   OPENAI_BASE_URL     endpoint base URL (optional; default api.openai.com)
#   ROBORSI_OPENAI_TRANSPORT  responses | chat_completions (default: responses)
# Optional GraspGen grasp service (used for the reported results):
#   GRASPGEN_PYTHON, GRASPGEN_REPO, GRASPGEN_GRIPPER_CONFIG, GRASPGEN_PORT
#
# All environments, checkouts, and assets live outside the repository under
# ROBORSI_REPRO_ROOT (default ~/.roborsi/repro), so the source tree stays clean
# and the campaign records an exact code revision.
#
# Idempotent: every step checks for existing state before doing work, and the
# campaign itself is resumable (re-run the script to continue).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

REPRO_ROOT="${ROBORSI_REPRO_ROOT:-${HOME}/.roborsi/repro}"
VENV="${ROBORSI_REPRO_VENV:-${REPRO_ROOT}/venv}"
PYROKI_VENV="${ROBORSI_PYROKI_VENV:-${REPRO_ROOT}/venv-pyroki}"
LIBERO_PLUS_DIR="${ROBORSI_LIBERO_PLUS:-${REPRO_ROOT}/LIBERO-plus}"
LIBERO_PLUS_COMMIT="4976dc3"
OUT_DIR="${1:-${HOME}/.roborsi/evals/suites/libero-plus-repro-840}"
# LIBERO-Plus and LIBERO-PRO need different LIBERO roots; keep separate state.
export ROBORSI_HOME="${ROBORSI_HOME:-${HOME}/.roborsi-libero-plus}"
WORKERS="${ROBORSI_EVAL_WORKERS:-8}"
PYROKI_PORT="${ROBORSI_PYROKI_PORT:-5559}"
PYROKI_LOG="${REPRO_ROOT}/pyroki-server.log"
mkdir -p "${REPRO_ROOT}"

log() { printf '\n\033[1;36m[reproduce]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[reproduce]\033[0m %s\n' "$*" >&2; exit 1; }

# ── 0 · prerequisites ────────────────────────────────────────────────────────
command -v git >/dev/null || die "git is required"
PY="$(command -v python3.12 || command -v python3 || true)"
[ -n "${PY}" ] || die "python3.12+ is required"
"${PY}" - <<'EOF' || exit 1
import sys
assert sys.version_info >= (3, 12), f"python3.12+ required, found {sys.version}"
EOF
# Model configuration: keep CLI roles separate from the API Engineer.
export ROBORSI_PLANNER_MODEL="${ROBORSI_PLANNER_MODEL:-${ROBORSI_EVAL_MODEL:-}}"
export ROBORSI_ENGINEER_MODEL="${ROBORSI_ENGINEER_MODEL:-${ROBORSI_EVAL_MODEL:-}}"
export ROBORSI_REVIEWER_MODEL="${ROBORSI_REVIEWER_MODEL:-${ROBORSI_EVAL_MODEL:-}}"
: "${ROBORSI_PLANNER_MODEL:?set ROBORSI_PLANNER_MODEL or ROBORSI_EVAL_MODEL}"
: "${ROBORSI_ENGINEER_MODEL:?set ROBORSI_ENGINEER_MODEL or ROBORSI_EVAL_MODEL}"
: "${ROBORSI_REVIEWER_MODEL:?set ROBORSI_REVIEWER_MODEL or ROBORSI_EVAL_MODEL}"
roborsi_effort=medium
case "$ROBORSI_ENGINEER_MODEL" in
  deepseek/*|moonshot/*|kimi/*)
    roborsi_effort=high ;;
esac
export ROBORSI_REASONING_EFFORT="${ROBORSI_REASONING_EFFORT:-$roborsi_effort}"
roborsi_api_models=("$ROBORSI_ENGINEER_MODEL" "${ROBORSI_PERCEPTION_MODEL:-}")
if [ "${ROBORSI_ROLE_SESSION:-1}" = 0 ]; then
  roborsi_api_models+=("$ROBORSI_PLANNER_MODEL" "$ROBORSI_REVIEWER_MODEL")
else
  for roborsi_model in "$ROBORSI_PLANNER_MODEL" "$ROBORSI_REVIEWER_MODEL"; do
    case "$roborsi_model" in
      deepseek/*|moonshot/*|kimi/*)
        echo 'Planner/Reviewer use CLI; set only ENGINEER/PERCEPTION to DeepSeek/Kimi, or set ROBORSI_ROLE_SESSION=0 for API roles' >&2
        exit 1 ;;
    esac
  done
fi
for roborsi_model in "${roborsi_api_models[@]}"; do
  case "$roborsi_model" in
    "") continue ;;
    deepseek/*) roborsi_key=DEEPSEEK_API_KEY ;;
    moonshot/*|kimi/*) roborsi_key=MOONSHOT_API_KEY ;;
    anthropic/*|claude-*) roborsi_key=ANTHROPIC_API_KEY ;;
    *) roborsi_key=OPENAI_API_KEY
       [ -n "${OPENAI_API_KEY:-}" ] || roborsi_key=ROBORSI_OPENAI_API_KEY ;;
  esac
  [ -n "${!roborsi_key:-}" ] || { echo "Set $roborsi_key for $roborsi_model" >&2; exit 1; }
done
# End model configuration.
export ROBORSI_OPENAI_TRANSPORT="${ROBORSI_OPENAI_TRANSPORT:-responses}"
command -v nvidia-smi >/dev/null || log "WARNING: nvidia-smi not found — the LIBERO renderer needs a working NVIDIA stack"
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
  die "the RoboRSI checkout has uncommitted changes; frozen evaluation records an exact revision and needs a clean tree"
fi

# ── 1 · main environment ─────────────────────────────────────────────────────
if [ ! -f "${VENV}/.installed" ]; then
  log "creating main environment at ${VENV}"
  "${PY}" -m venv "${VENV}"
  "${VENV}/bin/pip" install -q --upgrade pip
  # LeRobot v0.5.1 is a git submodule at third_party/lerobot; install it from
  # there so pip does not resolve a different release from PyPI.
  git submodule update --init --depth 1 third_party/lerobot
  "${VENV}/bin/pip" install -q -e "third_party/lerobot[feetech,dynamixel,pi]"
  "${VENV}/bin/pip" install -q -e ".[libero]" "huggingface_hub[cli]"
  touch "${VENV}/.installed"
fi
export PATH="${VENV}/bin:${PATH}"

# ── 2 · LIBERO-Plus checkout + official assets ─────────────────────────────
if [ ! -d "${LIBERO_PLUS_DIR}/libero" ]; then
  log "cloning LIBERO-Plus @ ${LIBERO_PLUS_COMMIT}"
  git clone https://github.com/sylvestf/LIBERO-plus.git "${LIBERO_PLUS_DIR}"
  git -C "${LIBERO_PLUS_DIR}" checkout -q "${LIBERO_PLUS_COMMIT}"
  # NumPy 2 compatibility for the noise perturbation (np.float_ was removed;
  # the blur helpers were only imported when ImageMagick bindings load).
  git -C "${LIBERO_PLUS_DIR}" apply "${REPO_ROOT}/scripts/patches/libero-plus-numpy2.patch"
  "${VENV}/bin/pip" install -q -r "${LIBERO_PLUS_DIR}/extra_requirements.txt"
fi
if [ ! -d "${LIBERO_PLUS_DIR}/libero/libero/assets" ]; then
  log "downloading LIBERO-Plus assets (HF dataset Sylvest/LIBERO-plus)"
  "${VENV}/bin/hf" download Sylvest/LIBERO-plus assets.zip --repo-type dataset \
    --local-dir "${REPRO_ROOT}/libero-plus-assets"
  "${PY}" -m zipfile -e "${REPRO_ROOT}/libero-plus-assets/assets.zip" "${LIBERO_PLUS_DIR}/libero/libero/"
fi
[ -d "${LIBERO_PLUS_DIR}/libero/libero/assets" ] || die "LIBERO-Plus assets missing under ${LIBERO_PLUS_DIR}/libero/libero/assets"

# ── 3 · configure + doctor ───────────────────────────────────────────────────
log "configuring the LIBERO backend for LIBERO-Plus"
roborsi libero configure --root "${LIBERO_PLUS_DIR}"
export ROBORSI_LIBERO_SUITES="libero_spatial,libero_object,libero_goal"
log "running backend health check on a LIBERO-Plus instance"
roborsi libero doctor --backend libero-pro --task libero_goal/1003 --reset

# ── 4 · PyRoKi IK / trajectory-optimization service ─────────────────────────
# PyRoKi needs jax with numpy<2, which conflicts with the eval environment,
# so it runs from its own venv as a ZMQ service (see roborsi/embodied/motion/pyroki/server.py).
if [ ! -f "${PYROKI_VENV}/.installed" ]; then
  log "creating PyRoKi environment at ${PYROKI_VENV}"
  "${PY}" -m venv "${PYROKI_VENV}"
  "${PYROKI_VENV}/bin/pip" install -q --upgrade pip
  "${PYROKI_VENV}/bin/pip" install -q "numpy<2" jax pyzmq robot_descriptions yourdfpy \
    "git+https://github.com/chungmin99/pyroki.git"
  touch "${PYROKI_VENV}/.installed"
fi
port_open() { (exec 3<>"/dev/tcp/127.0.0.1/${PYROKI_PORT}") 2>/dev/null; }
if port_open; then
  log "PyRoKi service already listening on ${PYROKI_PORT}"
else
  log "starting PyRoKi service on port ${PYROKI_PORT} (log: ${PYROKI_LOG})"
  ROBORSI_PYROKI_PORT="${PYROKI_PORT}" nohup "${PYROKI_VENV}/bin/python" \
    "${REPO_ROOT}/roborsi/embodied/motion/pyroki/server.py" > "${PYROKI_LOG}" 2>&1 &
  PYROKI_PID=$!
  for _ in $(seq 1 120); do
    port_open && break
    kill -0 "${PYROKI_PID}" 2>/dev/null || { tail -n 20 "${PYROKI_LOG}" >&2; die "PyRoKi service exited during start-up"; }
    sleep 1
  done
  port_open || { tail -n 20 "${PYROKI_LOG}" >&2; die "PyRoKi service did not listen on ${PYROKI_PORT} within 120 s"; }
fi
export ROBORSI_PYROKI_PORT="${PYROKI_PORT}"

# ── 4b · GraspGen grasp service (optional, used for the reported results) ───
# GraspGen runs in its own CUDA environment (see the header of
# roborsi/embodied/sim/robotwin/graspgen_infer.py). Provide GRASPGEN_PYTHON,
# GRASPGEN_REPO and GRASPGEN_GRIPPER_CONFIG to start it here, or start it
# yourself on GRASPGEN_PORT. Without it, grasps fall back to the analytic
# planner and success rates will be lower than the reported ones.
export GRASPGEN_HOST="${GRASPGEN_HOST:-localhost}"
GRASPGEN_PORT="${GRASPGEN_PORT:-5556}"
graspgen_open() { (exec 4<>"/dev/tcp/${GRASPGEN_HOST}/${GRASPGEN_PORT}") 2>/dev/null; }
if graspgen_open; then
  log "GraspGen service already listening on ${GRASPGEN_HOST}:${GRASPGEN_PORT}"
elif [ -n "${GRASPGEN_PYTHON:-}" ] && [ -n "${GRASPGEN_REPO:-}" ] && [ -n "${GRASPGEN_GRIPPER_CONFIG:-}" ]; then
  log "starting GraspGen service on port ${GRASPGEN_PORT} (log: ${REPRO_ROOT}/graspgen-server.log)"
  nohup "${GRASPGEN_PYTHON}" "${GRASPGEN_REPO}/client-server/graspgen_server.py" \
    --gripper_config "${GRASPGEN_GRIPPER_CONFIG}" --port "${GRASPGEN_PORT}" \
    > "${REPRO_ROOT}/graspgen-server.log" 2>&1 &
  GRASPGEN_PID=$!
  for _ in $(seq 1 300); do
    graspgen_open && break
    kill -0 "${GRASPGEN_PID}" 2>/dev/null || { tail -n 20 "${REPRO_ROOT}/graspgen-server.log" >&2; die "GraspGen service exited during start-up"; }
    sleep 1
  done
  graspgen_open || die "GraspGen service did not listen on ${GRASPGEN_PORT} within 300 s"
else
  log "WARNING: no GraspGen service; grasps use the fallback planner and results will differ from the reported ones"
  GRASPGEN_PORT=""
fi
export GRASPGEN_PORT
if [ -n "${GRASPGEN_PORT}" ]; then
  # A listening port is not enough: an overloaded server never answers, and
  # every grasp then silently falls back to the analytic planner.
  log "checking that GraspGen answers an inference request"
  python - <<'PYEOF' || die "GraspGen on ${GRASPGEN_HOST}:${GRASPGEN_PORT} did not answer; restart it on a free GPU"
import os, numpy as np
from roborsi.embodied.sim.robotwin.graspgen_infer import _grasps_from_cloud
cloud = (np.random.randn(2000, 3) * [0.03, 0.03, 0.05]).astype(np.float32)
_grasps_from_cloud(cloud, top_k=1, host=os.environ["GRASPGEN_HOST"], port=int(os.environ["GRASPGEN_PORT"]))
PYEOF
fi

# ── 5 · frozen campaign over the 840-instance panel (resumable) ─────────────
log "launching the frozen LIBERO-Plus campaign → ${OUT_DIR}"
set +e
roborsi eval-suite \
  --backend libero-pro \
  --atomic libero_pick_place \
  --panel libero_plus_840 \
  --pass-at 1 \
  --seed-start 5 \
  --workers "${WORKERS}" \
  --tool-budget 80 \
  --infra-retries 2 \
  --run-mode frozen \
  --planner-model "${ROBORSI_PLANNER_MODEL}" \
  --engineer-model "${ROBORSI_ENGINEER_MODEL}" \
  --reviewer-model "${ROBORSI_REVIEWER_MODEL}" \
  --reasoning-effort "${ROBORSI_REASONING_EFFORT}" \
  --out "${OUT_DIR}"
CAMPAIGN_RC=$?
set -e
[ -f "${OUT_DIR}/campaign.json" ] || die "the campaign did not start (exit ${CAMPAIGN_RC}); see the messages above"
if [ "${CAMPAIGN_RC}" -eq 2 ]; then
  log "campaign incomplete; re-run this script to resume"
elif [ "${CAMPAIGN_RC}" -ne 0 ]; then
  die "the campaign failed with exit ${CAMPAIGN_RC}"
fi

# ── 6 · audit + per-perturbation table ──────────────────────────────────────
log "auditing the campaign journal"
roborsi eval-audit "${OUT_DIR}" --check-media
log "success per perturbation type"
python -m roborsi.evaluation.panels breakdown "${OUT_DIR}" libero_plus_840
