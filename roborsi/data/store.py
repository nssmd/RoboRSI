"""DataStore — where collected rollouts live.

Default root: ``~/.roborsi/data`` (will become ``~/.roborsi/data`` after
the package rename sweep). Callers pass a ``skill`` label; the store issues
a fresh ``run_id`` and writes under ``<root>/<skill>/<run_id>/``.

Run-id scheme: ``YYYYMMDD-HHMMSS-<shortuuid>``. Collision-safe under
parallel workers (different processes get different uuids).

Browse:

    ds = DataStore()
    for ep in ds.list("beat_block_hammer"):
        print(ep.run_id, ep.frames, ep.success)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from roborsi.data.trajectory import WrittenEpisode, write_rollout
from roborsi.embodied.agent_loop.env import Rollout
from roborsi.embodied.paths import data_root


def _default_root() -> Path:
    return data_root()


@dataclass
class EpisodeSummary:
    skill: str
    run_id: str
    dir: Path
    success: bool
    frames: int
    task: str
    seed: int
    outcome: str

    @classmethod
    def from_meta(cls, skill: str, run_id: str, dir_: Path, meta: dict[str, Any]) -> "EpisodeSummary":
        return cls(
            skill=skill,
            run_id=run_id,
            dir=dir_,
            success=bool(meta.get("success", False)),
            frames=int(meta.get("frames_written", 0)),
            task=str(meta.get("task", "")),
            seed=int(meta.get("seed", -1)),
            outcome=str(meta.get("outcome", "")),
        )


class DataStore:
    def __init__(self, root: Path | None = None) -> None:
        from roborsi.runtime_mode import is_eval_mode
        if is_eval_mode():
            from roborsi.embodied.paths import evals_root
            requested = Path(root).expanduser().resolve() if root is not None else None
            training_root = data_root().resolve()
            if requested is None:
                root = evals_root()
            elif requested == training_root or training_root in requested.parents:
                root = evals_root() / requested.relative_to(training_root)
        self.root = (root or _default_root()).resolve()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

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
        self.ensure()
        rid = run_id or self._new_run_id()
        from roborsi.runtime_mode import current_mode
        meta = dict(extra_meta or {})
        meta.setdefault("run_mode", current_mode().value)
        return write_rollout(
            rollout,
            skill=skill,
            run_id=rid,
            store_root=self.root,
            plan_trace=plan_trace,
            judge_scores=judge_scores,
            extra_meta=meta,
        )

    def list(self, skill: str | None = None) -> Iterable[EpisodeSummary]:
        if not self.root.exists():
            return
        skills = [skill] if skill else [p.name for p in self.root.iterdir() if p.is_dir()]
        for sk in skills:
            skill_dir = self.root / sk
            if not skill_dir.is_dir():
                continue
            for run_dir in sorted(skill_dir.iterdir()):
                meta_path = run_dir / "meta.json"
                if not meta_path.exists():
                    continue
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                yield EpisodeSummary.from_meta(sk, run_dir.name, run_dir, meta)
