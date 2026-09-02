"""SkillSelector — Sonnet sub-agent that narrows the tool list per step.

Active only when the registered base/robotwin skill count exceeds
`SKILL_LIST_SOFT_CAP`. Reads plan.md + recent tool results + current
obs description (text only, not the image) and returns the top-K skill
names Engineer should expose to the inner VLM for the next step.

Returning ≤15 names keeps the inner VLM's tool list short enough that
descriptions of when_NOT_to_use stay readable.
"""
from __future__ import annotations

from typing import Any


SKILL_LIST_SOFT_CAP = 50
TOP_K_DEFAULT = 15


_SYSTEM_PROMPT = """You are a SKILL SELECTOR sub-agent. You do NOT
execute anything. You look at the current plan, the last few tool
results, the per-task success history, and the candidate skill index,
and return a JSON array of skill names the Engineer should consider next.

Constraints:
- Return ONLY a JSON array of strings. No prose, no fences.
- ≤ {top_k} names.
- Honor each skill's `when_NOT_to_use` — exclude skills whose negative
  rule matches the current situation.
- Skills in PROVEN WINNERS ON THIS TASK should rank HIGHER (they've
  worked before under their current code version). Higher count = more
  weight. But still allow a few exploration picks if the plan suggests
  the situation is novel.
- Prefer composite / high-level skills over low-level primitives when
  both fit (e.g. press_button_at_xyz over manual move+gripper).
"""


class SkillSelector:
    """Lightweight per-step pre-filter. Cached per (plan_hash, results_hash)."""

    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, model: str | None = None, top_k: int = TOP_K_DEFAULT) -> None:
        self.model = model or self.DEFAULT_MODEL
        self.top_k = top_k
        self._cache: dict[tuple[int, int], list[str]] = {}

    def pick(self, *, plan_md: str, recent_results: list[str],
             skill_index: str, scene_hint: str = "",
             success_counts: dict[str, int] | None = None) -> list[str]:
        """Return ≤top_k skill names. `skill_index` is the rendered
        listing of all base/robotwin skills (name + description +
        when_to_use + when_NOT_to_use), as produced by robotwin_agent
        `_build_tools_block()`.

        `success_counts`: optional {skill_name: n_successes_under_current_sha}
        for this task — selector should bias top-K toward proven winners
        (auto-resets when a skill's code commit changes; see
        agents/skill_history.py)."""
        # Cache on text content — same plan + same recent results = same answer.
        cache_key = (hash(plan_md), hash(tuple(recent_results)),
                     hash(tuple(sorted((success_counts or {}).items()))))
        if cache_key in self._cache:
            return self._cache[cache_key]

        from roborsi.embodied.agent_loop.vlm_io import _call_vlm_tools
        sys_prompt = _SYSTEM_PROMPT.format(top_k=self.top_k)
        recent_block = "\n".join(f"  {i}: {r[:200]}"
                                   for i, r in enumerate(recent_results[-3:]))
        # Success history — sorted by count desc, top 10 shown
        if success_counts:
            top_winners = sorted(success_counts.items(),
                                 key=lambda kv: -kv[1])[:10]
            success_block = "\n".join(f"  {n}: {c} success(es)"
                                       for n, c in top_winners)
        else:
            success_block = "  (no prior success records for this task)"
        user_block = (
            f"=== CURRENT PLAN (plan.md) ===\n{plan_md}\n\n"
            f"=== LAST 3 TOOL RESULTS ===\n{recent_block or '  (no calls yet)'}\n\n"
            f"=== SCENE HINT ===\n{scene_hint or '(no hint)'}\n\n"
            f"=== PROVEN WINNERS ON THIS TASK (current code sha) ===\n"
            f"{success_block}\n\n"
            f"=== CANDIDATE SKILL INDEX ===\n{skill_index}\n"
        )
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_block},
        ]
        resp = _call_vlm_tools(self.model, messages, [],
                                  thinking_budget=0, tool_choice="none")
        content = getattr(resp, "content", "") or ""
        if isinstance(content, list):
            from roborsi.channels.core.agent import _extract_text_block
            content = "".join(_extract_text_block(c) for c in content)
        names = self._parse_names(content)[: self.top_k]
        self._cache[cache_key] = names
        return names

    def _parse_names(self, raw: str) -> list[str]:
        """Best-effort parse: JSON array, or one-name-per-line fallback."""
        import json
        import re
        # Strip code fences if present.
        m = re.search(r"\[.*\]", raw, re.S)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if isinstance(x, (str, int))]
            except json.JSONDecodeError:
                pass
        # Fallback: extract plausible identifiers line-by-line.
        out: list[str] = []
        for line in raw.splitlines():
            tok = line.strip().lstrip("-*0123456789. ").strip("\"',`")
            if re.fullmatch(r"[a-z_][a-z0-9_]+", tok):
                out.append(tok)
        return out
