#!/usr/bin/env bash
set -euo pipefail

# Current-release, frozen LIBERO-PRO seed-0 baseline matching the resource
# budget used by the historical adaptive campaign. This does not replay or
# claim to reproduce the historical cross-release 80/120 result.

: "${ROBORSI_LIBERO_BDDLDIR:?configure the official LIBERO-PRO BDDL directory}"
: "${ROBORSI_LIBERO_INITDIR:?configure the official LIBERO-PRO init directory}"
: "${ROBORSI_PYROKI_PORT:?start PyRoKi and export its port}"

output="${1:-$HOME/.roborsi/evals/suites/libero-pro-matched-pass1}"
workers="${ROBORSI_EVAL_WORKERS:-8}"
model="${ROBORSI_EVAL_MODEL:-gpt-5.6-sol}"

exec roborsi eval-suite \
  --backend libero-pro \
  --atomic libero_pick_place \
  --pass-at 1 \
  --seed-start 0 \
  --workers "${workers}" \
  --tool-budget 80 \
  --infra-retries 2 \
  --planner-model "${model}" \
  --engineer-model "${model}" \
  --reviewer-model "${model}" \
  --reasoning-effort medium \
  --code-on \
  --out "${output}"
