# Skill Taxonomy

> 三层 skill。框架只有这三个名词，记住就够了。

---

## 三层

```
skills/
├── base/<robot>/<primitive>/        ← 机器人原语（"肌肉"）
├── atomic/<task>/                   ← 一个独立任务（4 件套，"动作"）
│   ├── SKILL.md                       任务定义
│   ├── zeroshot/                      VLM + base tools
│   ├── train/                         数据集 + π₀ finetune
│   ├── eval/                          held-out + 飞轮开关
│   ├── reset_success/                 成功后复位
│   └── reset_failure/                 失败救场
├── long_horizon/<task>/              ← 多 atomic 串联（3 件套，"任务"）
│   ├── SKILL.md
│   ├── plan/                          VLM 拆解
│   ├── progress_judge/                phase 打分
│   └── posttrain/                     整体回流
└── _lib/<lifecycle>/<name>/          ← 通用工具盒，不直接面向用户
```

---

## frontmatter 强约定

| 字段 | base | atomic | atomic_subskill | long_horizon | long_horizon_subskill |
|---|---|---|---|---|---|
| `kind` | `base` | `atomic` | `atomic_subskill` | `long_horizon` | `long_horizon_subskill` |
| `robot` | required | — | — | — | — |
| `parent` | — | — | required | — | required |
| `phase` | — | — | required（zeroshot/train/eval/reset_success/reset_failure） | — | required（plan/progress_judge/posttrain） |
| `domain` | — | required | — | required | — |

每个 skill 都有 `name`, `description`, `version`, `metadata` 标准字段。

---

## 加新东西的 3 个 case

### 加新机器人

```
mkdir skills/base/<新 robot>/
# 然后逐个原语：
mkdir skills/base/<新 robot>/capture_image/
# 写 SKILL.md (frontmatter 同 robotwin 模板) + policy.py (调新 SDK)
```

**zero 改 atomic / long_horizon 层**。已有 atomic 在新机器人上跑：把 atomic SKILL.md 的 `metadata.backends` 加上即可。

### 加新 atomic 任务

```
mkdir -p skills/atomic/<新任务>/{zeroshot,train,eval,reset_success,reset_failure}
# 5 个文件夹各写 SKILL.md + policy.py
# atomic SKILL.md 顶层定义场景 / success_predicate / vlm_prompts
```

很多 sub-skill 可以直接 import `_lib`：

- `train/policy.py` → `run_skill("lerobot_build", ...) + run_skill("pi0_finetune", ...)`
- `eval/policy.py` → `run_skill("success_rate", ...)`

只有 `zeroshot/` 和 `reset_failure/` 需要任务特定逻辑（VLM 调 base tools 的不同序列）。

### 加新 long_horizon 任务

```
mkdir -p skills/long_horizon/<新任务>/{plan,progress_judge,posttrain}
# 3 个 sub-skill 大多直接 thin-wrap _lib
```

---

## 数据飞轮（atomic 内）

```
SKILL.md 配置：
  active_executor:
    default: zeroshot
    threshold: 0.70

执行入口（farm / long_horizon 都读这个）：
  1. 读 ~/.roborsi/atomic_state/<task>/active_executor.json
  2. 没有 → executor = SKILL.md.default
  3. 有 → executor = "policy:<ckpt_path>"

eval/ 跑完写状态：
  if success_rate >= threshold:
    write executor = "policy:<latest_ckpt>"
```

每次 train 出新 ckpt 就重新 eval，eval 通过就切。**这是飞轮唯一的开关**。

---

## CLI 入口

```bash
# 列表
roborsi base list
roborsi base list --robot robotwin
roborsi atomic list
roborsi long-horizon list

# 详情
roborsi base show capture_image
roborsi atomic inspect beat_block_hammer
roborsi long-horizon inspect clean_table

# 跑
roborsi base run capture_image --params '{"camera": "head_camera"}'
roborsi atomic run beat_block_hammer zeroshot --params '{"episodes": 10}'
roborsi long-horizon run clean_table plan --params '{"instruction": "..."}'
```

仿真用 `scripts/roborsi-sim` 替换 `roborsi`（自动配置 RoboTwin conda env）。

---

## 一句话

**三个文件夹**：base / atomic / long_horizon。
**三个动作**：list / inspect / run。
**三个状态**：zeroshot / policy:vN / RL after。
**一切以 skill 为名词，平台为动词**。
