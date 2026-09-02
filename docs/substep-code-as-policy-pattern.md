# RoboRSI Substep-Level Code-as-Policy Pattern

> 2026-05-20 — 基于 CaP-X (arXiv:2603.22435) + Rollout (arXiv:2511.00917)
> 的研究，合并我们 STATUS CHECK 4-way 决策。**所有 atomic.zeroshot
> 都应按这个范式来**。

---

## 一句话

VLM 先把任务**分解成 substeps**；每个 substep 写**一小段 Python 伪代码**（5-15 行）调 base skill；每段代码跑完做 **STATUS CHECK 4-way**（PROCEED / REPLAN / RETRY / DONE）。

---

## 完整循环

```
┌─────────────────────────────────────────────────────────────┐
│  Turn 1 — PLAN                                              │
│    VLM 必须 emit plan() 调用：                              │
│      plan(goal="...",                                       │
│           substeps=[                                        │
│             {name: "locate_block",     primary: ..., fallback: ...},│
│             {name: "choose_arm",       primary: ..., fallback: ...},│
│             {name: "grasp_hammer",     primary: ..., fallback: ...},│
│             {name: "tap_on_block",     primary: ..., fallback: ...},│
│             {name: "verify_success",   primary: ..., fallback: ...},│
│           ])                                                │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Turn 2..N — per substep                                    │
│                                                             │
│    a) VLM emit exec_python(code) 写当前 substep 的代码      │
│       (~5-15 行；直接调 base skill 函数，可有 for/if)        │
│                                                             │
│    b) sim 执行代码，捕 stdout/stderr/image                  │
│                                                             │
│    c) STATUS CHECK 4-way (runtime 自动 prepend 决策提示):   │
│        - PROCEED  → 进下一个 substep                         │
│        - RETRY    → 重写当前 substep 代码（必须换策略）       │
│        - REPLAN   → 重新调 plan() 修改 substep 列表         │
│        - DONE     → 调 done(success=True/False)            │
│                                                             │
│    d) VLM 必须以这 4 个词之一开头其下一个 turn               │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
            ┌── DONE ──→ check_success() → end ──┐
            │                                    │
            └── 否则继续 Turn 2..N ───────────────┘
```

---

## 4 个 STATUS 的精确定义

### `PROCEED`
**条件**：当前 substep 代码跑完，stdout 证据表明子目标达成。
**行动**：emit 下一个 substep 的 `exec_python(code)`。
**注意**：不能没有证据就 PROCEED。如果 stdout 里没有打印目标值，就**不能**判定成功。

### `RETRY`
**条件**：当前 substep 失败（异常 / 预期没满足 / ok=False）。
**行动**：emit `exec_python(code)` 重写当前 substep。
**强制规则**：必须**换策略**（不同 base skill / 不同参数模式 / 不同 grasp pose）。
**反复触发 RETRY ≥3 次** → 必须改为 REPLAN 或 DONE(False)。

### `REPLAN`
**条件**：substep 列表本身有问题（漏了步骤 / 顺序错 / 目标定错）。
**行动**：emit `plan(goal=..., substeps=[...])`，整个 substep 列表重写。
**触发条件**：场景信息和原 plan 假设矛盾时（如 block 在两臂都不可达）。

### `DONE`
**条件**：所有 substep 完成 AND stdout 证据证明 task 真正完成。
**行动**：emit `done(success=True|False)`。
**强制**：success=True 必须有 print 出来的成功证据。允许 `done(success=False)` —— 当 task 物理上不可达，提早返回比耗 budget 强。

---

## 代码块约束

每段 `exec_python(code)` 必须：

1. **只解决一个 substep** — 不要在一段代码里跑完整个任务
2. **PRINT 所有中间值** — 否则 STATUS CHECK 没证据
3. **写函数定义当辅助** — `def helper(): ...`，成功后 AST 自动抽进 skill library
4. **直接调 base skill 函数** — 它们是 Python 函数，不是 tool_use；返回 dict
5. **5-15 行** —不超过 20 行；超过说明 substep 粒度过大，需要 REPLAN 拆分

### 反面例子
```python
# ❌ 错：一段代码做整个任务
r = localize_object_top_center(object='coloured block', grid_n=5)
for arm in ('left','right'):
    chk = is_reachable(arm=arm, x=r['xyz'][0], y=r['xyz'][1], z=r['xyz'][2])
    if chk['reachable']:
        chosen = arm
        break
g = grasp_then_lift_graspgen(arm=chosen, object='hammer', ...)
t = tap_held_on_target(arm=chosen, target_x=r['xyz'][0], ...)
done(success=t['ok'])
```

### 正面例子（substep `locate_block` 的代码）
```python
# substep: locate_block
r = localize_object_top_center(object='coloured block', grid_n=5)
print('block_xyz:', r['xyz'])
print('block_cameras_used:', r.get('cameras_used'))
print('block_ok:', r.get('ok'))
# 留给 STATUS CHECK 判定: 拿到 xyz？是 ok=True？
```

---

## System Prompt 必备段落

```
SUBSTEP CODE-AS-POLICY PROTOCOL (mandatory for every atomic task):

1. FIRST turn: call plan(goal=..., substeps=[...]) with ALL substeps listed
   (do not start executing yet). Each substep needs name + primary
   strategy + optional fallback strategy.

2. EACH subsequent turn: emit ONE exec_python(code=...) call for the
   CURRENT substep. The code must be 5-15 lines, print intermediate
   values, and call base skills directly as Python functions.

3. AFTER exec_python returns, runtime injects a STATUS CHECK question.
   Your VERY NEXT MESSAGE must start with one of these four words:
     PROCEED  — substep succeeded, advance to next
     RETRY    — substep failed, rewrite SAME substep with DIFFERENT strategy
     REPLAN   — substep list itself is wrong, re-emit plan()
     DONE     — finish (call done(success=True|False) next)

4. RETRY budget: max 3 consecutive RETRYs on the same substep, then
   forced REPLAN or DONE(False).

5. PROCEED requires evidence in stdout. Without printed proof, you may
   not PROCEED.
```

---

## SkillLibrary 飞轮（自动）

成功 turn 序列里所有 `def helper(): ...` 函数：

1. AST 抽出
2. 写到 `~/.roborsi/data/<task>/_function_library.json`
3. occurrences ≥2 → 自动 promote
4. 下次 task system_prompt 注入这些函数源码

VLM 会看到："PROMOTED FUNCTIONS (used in N past successful trials): def choose_arm(): ..."
直接 call 这些函数即可，不用重新发明。

---

## 跟 Rollout / CaP-X 的差异

| 维度 | Rollout | CaP-X | **本范式** |
|---|---|---|---|
| 基本单位 | substep code | full program | **substep exec_python** |
| 决策 | success ? next : replan | REGENERATE/FINISH | **PROCEED/RETRY/REPLAN/DONE 4-way** |
| 计划 | plan first turn | implicit | **plan() 强制 first turn** |
| Recovery | "return to free state" code | rewrite whole | **RETRY 限 3 次自动升 REPLAN** |
| 飞轮 | Evolution DB | SkillLibrary | **AST → 函数源码注入** |

---

## 对所有 atomic 任务的迁移要求

每个 `skills/atomic/<task>/zeroshot/policy.py` 必须：

1. 调 `run_substep_capx_episode()` (不是旧的 rollout_runtime 直接 loop)
2. 传 `task_name`, `instruction`, `expected_on_success`, `check_success_fn`
3. 不在 atomic 里写循环或重试逻辑（loop 框架已实现）

每个 `skills/atomic/<task>/zeroshot/SKILL.md` 的 instruction 字段必须明示：
- 该任务的 typical substeps
- 每个 substep 应该用哪些 base skill

base/robotwin/ 下不变 —— substep 代码就调它们。
