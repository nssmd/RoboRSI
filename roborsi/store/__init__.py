"""roborsi.store — sqlite-backed run/step/proposal/bench/vla store.

Separate from `roborsi.data` (which stores raw rollout assets like frames
and trajectory pkl). This package is the observability + learning database:
fast queries over which runs happened, what each agent step was, what
proposals were made, what benchmarks scored, what episodes went into VLA
training.
"""
from roborsi.store.trace_db import (
    db_path, init, insert_run, update_run, append_step,
    record_proposal, update_proposal_status,
    record_bench, record_vla_episode,
    get_run, list_runs, list_steps, list_proposals,
    skill_success_rate,
    append_event, list_events,
)

__all__ = [
    "db_path", "init", "insert_run", "update_run", "append_step",
    "record_proposal", "update_proposal_status",
    "record_bench", "record_vla_episode",
    "get_run", "list_runs", "list_steps", "list_proposals",
    "skill_success_rate",
    "append_event", "list_events",
]
