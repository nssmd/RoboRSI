#!/usr/bin/env bash
# One-click fresh LIBERO-PRO evaluation on the current frozen release.
#
# End-to-end: environment → install → LIBERO-PRO checkout → official
# perturbation assets (HF: zhouxueyang/LIBERO-Pro) → backend configure +
# doctor → PyRoKi IK/trajopt service → frozen code-on Pass-1 campaign →
# journal audit.
#
# This launches a NEW campaign against the current frozen release. It does
# not replay or claim to reproduce the historical cross-release cumulative
# results; see docs/EVALUATION.md for that boundary.
#
# Requirements: Linux, git, python3.12+, an NVIDIA/CUDA stack for the LIBERO
# renderer, and OPENAI_API_KEY for an OpenAI-compatible Responses endpoint.
#
# Idempotent: every step checks for existing state before doing work, and the
# campaign itself is resumable (re-run the script to continue).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

VENV="${ROBORSI_REPRO_VENV:-${REPO_ROOT}/.venv-repro}"
PYROKI_VENV="${ROBORSI_PYROKI_VENV:-${REPO_ROOT}/.venv-pyroki}"
ASSETS_DIR="${ROBORSI_LIBERO_ASSETS:-${REPO_ROOT}/LIBERO-PRO-assets}"
LIBERO_PRO_DIR="${ROBORSI_LIBERO_PRO:-${REPO_ROOT}/LIBERO-PRO}"
OUT_DIR="${1:-${HOME}/.roborsi/evals/suites/libero-pro-repro-pass1}"
PYROKI_PORT="${ROBORSI_PYROKI_PORT:-5559}"

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
[ -n "${OPENAI_API_KEY:-}" ] || die "export OPENAI_API_KEY first (OpenAI-compatible Responses endpoint)"
command -v nvidia-smi >/dev/null || log "WARNING: nvidia-smi not found — the LIBERO renderer needs a working NVIDIA stack"

# ── 1 · main environment ─────────────────────────────────────────────────────
if [ ! -x "${VENV}/bin/python" ]; then
  log "creating main environment at ${VENV}"
  "${PY}" -m venv "${VENV}"
fi
log "installing RoboRSI (libero extra)"
"${VENV}/bin/pip" install -q --upgrade pip
"${VENV}/bin/pip" install -q -e ".[libero]" "huggingface_hub[cli]"
export PATH="${VENV}/bin:${PATH}"

# ── 2 · LIBERO-PRO checkout + official assets ───────────────────────────────
if [ ! -d "${LIBERO_PRO_DIR}/libero" ]; then
  log "cloning LIBERO-PRO"
  git clone --depth 1 https://github.com/Zxy-MLlab/LIBERO-PRO.git "${LIBERO_PRO_DIR}"
fi
if [ ! -d "${ASSETS_DIR}/bddl_files" ] || [ ! -d "${ASSETS_DIR}/init_files" ]; then
  log "downloading official perturbation assets (HF dataset zhouxueyang/LIBERO-Pro, CC-BY-4.0)"
  "${VENV}/bin/hf" download zhouxueyang/LIBERO-Pro --repo-type dataset --local-dir "${ASSETS_DIR}"
fi
[ -d "${ASSETS_DIR}/bddl_files" ] || die "assets download did not produce ${ASSETS_DIR}/bddl_files"

# ── 3 · configure + doctor ───────────────────────────────────────────────────
log "configuring the LIBERO backend"
"${VENV}/bin/roborsi" libero configure \
  --root "${LIBERO_PRO_DIR}" \
  --bddldir "${ASSETS_DIR}/bddl_files" \
  --initdir "${ASSETS_DIR}/init_files"
log "running backend health check"
"${VENV}/bin/roborsi" libero doctor --backend libero --task libero_object/0 --reset

# ── 4 · PyRoKi IK / trajectory-optimization service ─────────────────────────
# PyRoKi needs jax with numpy<2, which conflicts with the eval environment,
# so it runs from its own venv as a ZMQ service (see scripts/pyroki_ik_server.py).
if [ ! -x "${PYROKI_VENV}/bin/python" ]; then
  log "creating PyRoKi environment at ${PYROKI_VENV}"
  "${PY}" -m venv "${PYROKI_VENV}"
  "${PYROKI_VENV}/bin/pip" install -q --upgrade pip
  "${PYROKI_VENV}/bin/pip" install -q "numpy<2" jax pyzmq robot_descriptions yourdfpy \
    "git+https://github.com/chungmin99/pyroki.git"
fi
if ! (exec 3<>"/dev/tcp/127.0.0.1/${PYROKI_PORT}") 2>/dev/null; then
  log "starting PyRoKi service on port ${PYROKI_PORT} (log: ${REPO_ROOT}/.pyroki-server.log)"
  ROBORSI_PYROKI_PORT="${PYROKI_PORT}" nohup "${PYROKI_VENV}/bin/python" \
    "${REPO_ROOT}/scripts/pyroki_ik_server.py" \
    > "${REPO_ROOT}/.pyroki-server.log" 2>&1 &
  sleep 8
else
  exec 3>&- || true
  log "PyRoKi service already listening on ${PYROKI_PORT}"
fi

# ── 5 · frozen code-on Pass-1 campaign (resumable) ──────────────────────────
log "launching the frozen code-on Pass-1 campaign → ${OUT_DIR}"
export ROBORSI_LIBERO_BDDLDIR="${ASSETS_DIR}/bddl_files"
export ROBORSI_LIBERO_INITDIR="${ASSETS_DIR}/init_files"
export ROBORSI_PYROKI_PORT="${PYROKI_PORT}"
"${REPO_ROOT}/scripts/run_libero_pro_matched_pass1.sh" "${OUT_DIR}" || true

# ── 6 · independent audit from the append-only journal ──────────────────────
log "auditing the campaign journal"
"${VENV}/bin/roborsi" eval-audit "${OUT_DIR}" --check-media || true

log "done. summary: ${OUT_DIR}/summary.json · journal: ${OUT_DIR}/episodes.jsonl"
log "re-run this script to resume an incomplete campaign."
