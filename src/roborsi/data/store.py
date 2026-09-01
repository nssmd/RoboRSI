"""Append-only trajectory storage for completed RoboRSI rollouts."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from roborsi.data.trajectory import WrittenEpisode, write_rollout
from roborsi.embodied.agent_loop.env import Rollout
from roborsi.embodied.paths import data_root


class DataStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or data_root()).resolve()

    @staticmethod
    def _new_run_id() -> str:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{ts}-{uuid.uuid4().hex[:6]}"

    def write(
        self,
        rollout: Rollout,
        *,
        skill: str,
        run_id: str | None = None,
        plan_trace: list[dict[str, Any]] | None = None,
        judge_scores: list[dict[str, Any]] | None = None,
        extra_meta: dict[str, Any] | None = None,
    ) -> WrittenEpisode:
        self.root.mkdir(parents=True, exist_ok=True)
        rid = run_id or self._new_run_id()
        return write_rollout(
            rollout,
            skill=skill,
            run_id=rid,
            store_root=self.root,
            plan_trace=plan_trace,
            judge_scores=judge_scores,
            extra_meta=extra_meta,
        )
