# RoboRSI 架构

> 设计原则：**skill 是一等公民**，VLM 是司机，base 是肌肉，atomic 是动作，long_horizon 是任务。
---

## 1. 一图鸟瞰

```
┌─────────────────────────────────────────────────────────────────┐
│  User                                                           │
│   ┌──────────────────────┐    ┌──────────────────────┐          │
│   │ "把桌子收拾干净"      │    │ "刷 1000 条 pick 数据" │          │
│   └────────────┬─────────┘    └─────────┬────────────┘          │
└────────────────┼────────────────────────┼───────────────────────┘
                 ▼                        ▼
        ┌────────────────┐      ┌──────────────────┐
        │ long_horizon/  │      │ atomic/<task>/    │
        │  plan / judge  │      │ zeroshot or      │
        │  posttrain     │      │  policy:vN       │
        └────────┬───────┘      └────────┬─────────┘
                 │ 选 atomic              │ 调 base tool
                 ▼                        ▼
            ┌────────────────────────────────────┐
            │     atomic/<task>/                  │
            │  (zeroshot, train, eval, reset_*)   │
            └────────────────┬───────────────────┘
                             │ 调 base tool
                             ▼
            ┌────────────────────────────────────┐
            │     base/<robot>/<primitive>/       │
            │  capture_image, move_to_pose,       │
            │  set_gripper, home, ...             │
            └────────────────┬───────────────────┘
                             ▼
                  ┌──────────────────────┐
                  │ Robot / SimBackend    │
                  │ (RoboTwin / Flexiv)   │
                  └──────────────────────┘
```

VLM 在三层都参与：长指令拆解 → atomic 选 executor → zeroshot 时调 base tool。
**所有"该做什么"都在 skill 里**，框架只是调度器。

---

## 2. 三层 skill（核心约定）

### 2.1 base —— 机器人原语

`skills/base/<robot>/<primitive>/`

- 一个机器人一份（`robotwin/`, `flexiv/`, ...）
- 每个原语 = 一个文件夹（`SKILL.md` + `policy.py`）
- **双形态**：`policy.run(env, **params)` 给 atomic 调；同时是 VLM tool（zeroshot 阶段被 VLM 调）

| 已实现（robotwin） |
|---|
| `capture_image` · `move_to_pose` · `move_to_pixel` · `set_gripper` · `home` · `read_joint_state` |

### 2.2 atomic —— 一个独立任务（4 件套，强约束）

`skills/atomic/<task>/`

```
SKILL.md            ← 任务定义（场景、success_predicate、active_executor 配置）
zeroshot/           ← VLM 调 base 工具零样本采集；只入成功轨迹
train/              ← LeRobot 数据集 + π₀ finetune
eval/               ← 保留 seed 评估；维护 active_executor 状态机（飞轮开关）
reset_success/      ← 任务成功后复位；落 reset_success DataStore
reset_failure/      ← 任务失败后救场；按失败模式落 reset_failure_<mode>
```

`active_executor` 状态切换：

```
zeroshot ──(eval ≥ threshold)──► policy:v1 ──(数据涨, 重训)──► policy:v2 ──► ...
```

切换后所有调用方（farm / long_horizon）自动用最新 executor。

### 2.3 long_horizon —— 多 atomic 串联（3 件套）

`skills/long_horizon/<task>/`

```
SKILL.md            ← 用户指令模板 + 期望分解
execute/            ← LH task 身份（wiki + 意图枚举）；执行走 3-role 三角（LHPlanner → LHExecutor → LHReviewer）
progress_judge/     ← 调 _lib.progress_score（每 phase 边界）
posttrain/          ← 整体 trace → 参与 atomic 联合 RL
```

### 2.4 _lib —— 工具库（不直接暴露用户）

`skills/_lib/<lifecycle>/<name>/` —— 9 个跨任务复用工具：collection / dataset / training / evaluation / planning / judging / rl / minting。
**用户不直接调，只被 atomic / long_horizon 的 policy.py import**。

---

## 3. 框架代码（不属 skill）

```
roborsi/embodied/
├── sim/                ── SimBackend / SimEnv / RoboTwin adapter
├── planner/            ── VLM 拆解长指令 → skill 序列
├── runtime.py          ── Plan-Act-Judge 闭环
├── farm.py             ── Parallel Farm（多 worker 并行）
├── bundle.py           ── 老的 bundle.yaml runner（向后兼容）
├── paths.py            ── ~/.roborsi 路径解析
├── executor.py         ── subprocess 包
├── embodiment/         ── Flexiv 实机驱动 / 相机 / manifest
├── engine/             ── vendored LeRobot
└── ...
```

加新 atomic / long_horizon 不动框架。加新机器人 base 也不动框架，只在 `base/<新 robot>/` 复制原语模板。

---

## 4. 数据流向

```
zeroshot/ 跑成功 ─────► DataStore: ~/.roborsi/data/<task>/
                              │
                              └─► train/ 调 _lib.lerobot_build → datasets/<task>_v1
                                       └─► train/ 调 _lib.pi0_finetune → checkpoints/<task>/<ts>
                                                └─► eval/ → evals/<task>/<ts>/eval_report.json
                                                         └─► atomic_state/<task>/active_executor.json
                                                                  │
                              ┌──────── policy:vN 接管 zeroshot ───┘
                              ▼
                         继续刷数据 → 重训 → 飞轮

reset_success/ 跑 ─────► data/<task>_reset_success/
reset_failure/ 跑 ─────► data/<task>_reset_failure_<mode>/
                                └─► 未来训 reset policy
```

`~/.roborsi/atomic_state/<task>/active_executor.json` 是飞轮的状态机，每个 atomic 一份。

---

## 5. CLI 三层入口

```
roborsi base list / show / run
roborsi atomic list / inspect <task> / run <task> <phase>
roborsi long-horizon list / inspect <task> / run <task> <phase>
```

支持仿真环境调用（`scripts/roborsi-sim` launcher 自动配置 RoboTwin conda env + LD_LIBRARY_PATH）。

---

## 6. 跟 paper / 其它平台的差异点

- **vs Rollout**：他们的 module 套件硬编码进 VLM prompt；我们的 base skill 就是 module，VLM 只读 frontmatter，扩展跨机器人零成本。
- **与纯 Markdown skill runtime 相比**：RoboRSI 还保留一份 `policy.py`，让机器人指令落到可执行代码。
- **vs LeRobot**：他们以 dataset/policy 为一等公民；我们以 skill 为一等公民，dataset/policy 是 atomic 的产物。
