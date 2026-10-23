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
export ROBORSI_LIBERO_SUITES="${ROBORSI_LIBERO_SUITES:-libero_goal_task,libero_goal_object,libero_goal_swap,libero_goal_lan,libero_spatial_task,libero_spatial_object,libero_spatial_swap,libero_spatial_lan,libero_object_task,libero_object_object,libero_object_swap,libero_object_lan}"
exec roborsi eval-suite \
  --backend libero-pro \
  --atomic libero_pick_place \
  --pass-at 1 \
  --seed-start 0 \
  --workers "${workers}" \
  --tool-budget 80 \
  --infra-retries 2 \
  --planner-model "${ROBORSI_PLANNER_MODEL}" \
  --engineer-model "${ROBORSI_ENGINEER_MODEL}" \
  --reviewer-model "${ROBORSI_REVIEWER_MODEL}" \
  --reasoning-effort "${ROBORSI_REASONING_EFFORT}" \
  --out "${output}"
