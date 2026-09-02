"""3-role agent architecture for atomic tasks.

  Planner  → writes plan.md (goal / sub-goals / candidate skills)
  Engineer → reads plan.md, runs the sim loop, writes summary.md
  Reviewer → reads everything, writes review.md, optionally drops a
             proposal into ~/.roborsi/skill_review/

This package is currently active only on the atomic-task path
(`handle_user_message` routes `.zeroshot` requests through it).
The long-horizon path still uses the legacy single-agent loop.
"""

from roborsi.agents.workspace import Workspace, new_workspace
from roborsi.agents.planner import Planner
from roborsi.agents.engineer import Engineer
from roborsi.agents.reviewer import Reviewer
from roborsi.agents.skill_selector import SkillSelector, SKILL_LIST_SOFT_CAP
from roborsi.agents.validator import (
    ProposalValidator, ValidationReport, CheckOutcome,
)
from roborsi.agents.lh_executor import (
    LHExecutor, LHExecutorResult, MAX_ATOMIC_RETRIES,
)

__all__ = [
    "Planner", "Engineer", "Reviewer",
    "Workspace", "new_workspace",
    "SkillSelector", "SKILL_LIST_SOFT_CAP",
    "ProposalValidator", "ValidationReport", "CheckOutcome",
    "LHExecutor", "LHExecutorResult",
    "MAX_ATOMIC_RETRIES",
]
