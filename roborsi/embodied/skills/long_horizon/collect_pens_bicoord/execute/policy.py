"""long_horizon.collect_pens_bicoord.execute — retired direct entry point.

The long-horizon execution path is the 3-role triangle
(Planner.decompose → LHExecutor → Reviewer.review_lh), driven by ``_run_lh_3role`` in
``roborsi.channels.core.agent``. This skill dir is kept
only so discover() registers ``collect_pens_bicoord.execute`` (the wiki +
LH-intent enumeration both key off the ``.execute`` name); it no longer
runs anything itself.
"""

from __future__ import annotations

from typing import Any


def run(**_: Any) -> dict[str, Any]:
    raise NotImplementedError(
        "collect_pens_bicoord.execute is not directly runnable — long-horizon "
        "tasks run through the 3-role triangle (_run_lh_3role: "
        "Planner.decompose → LHExecutor → Reviewer.review_lh)."
    )
