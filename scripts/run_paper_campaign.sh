#!/usr/bin/env bash
set -euo pipefail

# Paper protocol (arXiv:2610.12424, Table 1) for LIBERO and LIBERO-PRO:
# every task runs five episodes (seeds 0-4) in evolve mode, and RoboRSI
# improves online while it is evaluated. Every episode is run, so the summary
# reports episode success (successful/valid episodes) and task coverage
# (tasks solved at least once).
#
#   scripts/run_paper_campaign.sh libero     <out>   # 30 tasks x 5 = 150 episodes
#   scripts/run_paper_campaign.sh libero-pro <out>   # 120 tasks x 5 = 600 episodes
#
# ROBORSI_TOOL_BUDGET (default 80, as in the paper) is the episode's tool
# budget; ROBORSI_ROUND_TOOL_BUDGET (default half of it) caps one round.
#
# Evolution runs on a copy of the skill library under <out>/library; the
# checkout is not modified. Results depend on the model and on what the run
# learns, so a rerun is not expected to give the paper's numbers exactly.

benchmark="${1:?usage: run_paper_campaign.sh libero|libero-pro <out>}"
output="${2:?usage: run_paper_campaign.sh libero|libero-pro <out>}"
workers="${ROBORSI_EVAL_WORKERS:-8}"
model="${ROBORSI_EVAL_MODEL:?set ROBORSI_EVAL_MODEL to the model id served by your endpoint}"

: "${ROBORSI_LIBERO_BDDLDIR:?configure the official LIBERO-PRO BDDL directory}"
: "${ROBORSI_LIBERO_INITDIR:?configure the official LIBERO-PRO init directory}"
: "${ROBORSI_PYROKI_PORT:?start PyRoKi and export its port}"

case "${benchmark}" in
  libero)
    suites="libero_spatial,libero_object,libero_goal" ;;
  libero-pro)
    suites="libero_goal_task,libero_goal_object,libero_goal_swap,libero_goal_lan,libero_spatial_task,libero_spatial_object,libero_spatial_swap,libero_spatial_lan,libero_object_task,libero_object_object,libero_object_swap,libero_object_lan" ;;
  *)
    echo "unknown benchmark '${benchmark}' (libero | libero-pro)" >&2; exit 2 ;;
esac

export ROBORSI_OPENAI_TRANSPORT="${ROBORSI_OPENAI_TRANSPORT:-responses}"
export ROBORSI_LIBERO_SUITES="${suites}"
# Wiki, sessions and proposal queues of this campaign, kept apart from other runs.
export ROBORSI_HOME="${ROBORSI_HOME:-${HOME}/.roborsi-paper-${benchmark}}"

exec roborsi eval-suite \
  --backend libero-pro \
  --atomic libero_pick_place \
  --pass-at 5 \
  --run-all-seeds \
  --seed-start 0 \
  --workers "${workers}" \
  --tool-budget "${ROBORSI_TOOL_BUDGET:-80}" \
  --infra-retries 2 \
  --run-mode evolve \
  --planner-model "${model}" \
  --engineer-model "${model}" \
  --reviewer-model "${model}" \
  --reasoning-effort medium \
  --out "${output}"
