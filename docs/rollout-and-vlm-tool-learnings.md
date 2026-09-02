# 从 Rollout 与 VLM+Tool 路线学到什么 — robo-rsi 行动清单

> 2026-05-20 整理。基于 Rollout (arXiv:2511.00917v1, UPenn, 2025-11) 全文，加 12 篇周边 VLM+tool 工作（含 2026 年 1–5 月最新）。
> 关注落点：**该往 `base/` 和 `_lib/` 加什么**，以及 `atomic/zeroshot` 该用什么模式。

---

## TL;DR — 五件马上能做的事

| # | 加在哪 | 是什么 | 为什么 | 工时 |
|---|---|---|---|---|
| 1 | `_lib/geometry/` | 4 个向量函数：测距、构向量、夹角、绕角旋转 | Rollout 消融：去掉后 rotate-cube 60→25，VLM 没 spatial CoT | 0.5d |
| 2 | `base/<robot>/active_perceive/` | 腕相机 zoom_in / look_around / multi_view | Rollout 消融：去掉后 fold-towel 71→40 | 1d |
| 3 | `_lib/perception/visual_prompt/` | Grounded-SAM + Set-of-Mark 编号 overlay | Rollout / MOKA / PIVOT 共识：让 VLM 选 ID 比让它给像素准 | 1d |
| 4 | atomic.zeroshot 改 **substep-level plan-react-replan** | 每个 substep 后把 RGB+stdout+state 回灌 VLM | Rollout §III-B 的核心闭环，CaP-X 也实锤 | 2d |
| 5 | `_lib/judging/vlm_monitor/` | 小 VLM 2Hz yes/no 监控，作为 policy:vN 的打断器 | Rollout 用 Qwen2.5-VL-72B 跑 2Hz；不监控 VLA 就会"跑飞" | 1d |

---

## Part 1 — Rollout 详解

### 1.1 一句话

> 不练 VLA，把 VLM 当 coding agent，编排一套精心挑选的 perception/planning/control 模块；在 7 个 tabletop + 4 个 mobile 任务上 zero-shot 反超 π₀ / π₀.₅ / Gemini Robotics Agent。

闭环：`plan → react → replan`，每个 substep 执行完把图像 + stdout 回灌 VLM。
进化：`evolution` 把过往 trial 的 (code, stdout, Gemini 失败分析) 存库，下次作为 in-context 示例。

### 1.2 完整工具清单（这是你最该抄的）

#### Tabletop（DROID, Franka + Robotiq）

| 类别 | 工具 | RoboRSI 对位建议 |
|---|---|---|
| **Perception (coarse→fine)** | Raw RGB+proprio | `base/<robot>/capture_image` ✅ 已有 |
|  | **FoundationStereo 深度** | `_lib/perception/foundation_stereo/` — 新增 |
|  | Grounded-SAM mask centroid | `_lib/perception/grounded_sam/` — 新增 |
|  | Gemini pointing (语言 → 2D 点) | `_lib/perception/vlm_pointing/` — 新增 |
|  | **ReKep 风格 task-relevant keypoints**（mask + grid + GPT-o3 选点） | `_lib/perception/task_keypoints/` — 新增（杀手锏）|
|  | **Active perception**（wrist 相机 zoom/look around） | `base/<robot>/active_perceive/` — **新增 base 原语** |
| **Reasoning** | 测距 / 构向量 / 夹角 / 绕角旋转 | `_lib/geometry/` — 新增（4 个函数）|
| **Control** | Cartesian move/open/close | `base/<robot>/move_to_pose` ✅ + `set_gripper` ✅ |
|  | **cuRobo collision-free planning** | `base/<robot>/move_to_pose_safe/` — 新增（"安全版"）|
| **Learned policies** | **GraspGen** 抓取模型 | `_lib/grasping/graspgen/` — 新增 |
|  | **π₀.₅ as callable tool** + 2Hz Qwen monitor | 对应 `active_executor=policy:vN` + `_lib/judging/vlm_monitor/` |
| **Image editing** | 画点、overlay 6D pose（Set-of-Mark） | `_lib/perception/visual_prompt/` — 新增 |

#### Mobile（Unitree Go2-W + PiPER）— 等你做移动平台时再看

| 类别 | 工具 |
|---|---|
| 状态估计 | Faster-LIO（LiDAR-Inertial Odometry）|
| Active | look_left/right/ground、view_basket、**remember_object_location**（语义地图缓存）|
| 导航 | Nav2 全局 + **nudge** 局部微调 |
| 操作 | put_in_basket（多物体携带）|

### 1.3 三个设计原则（你设计 base 时直接拿来用）

1. **Coarse-to-fine 分层 perception**：raw → mask centroid → task keypoints。VLM 自己挑速度/精度的平衡点。
2. **Active perception 是其他工具的乘数**：腕相机 zoom 之后，grasp 模型质量、keypoint 准度都涨。
3. **几何/线代是 VLM 的"外脑"**：没有这些 tool，VLM 写不出 rotate-cube/open-cabinet 的代码（消融实验有铁证）。

### 1.4 消融实验（决定优先级）

| 变体 | Fold Towel | Rotate Cube |
|---|---|---|
| rollout | 71.3 | 60.0 |
| − advanced perception | 40.0 | 25.0 |
| − geometry modules | 67.5 | 42.5 |

→ **advanced perception 比 geometry 影响更大**，但 geometry 写起来更便宜。**两个都做**。

---

## Part 2 — 周边 VLM+Tool 工作（按对 robo-rsi 的相关性排序）

### A. 直接同路线（VLM+module 编排）

**CaP-X** (arXiv:2603.22435, 2026-03)
- 系统 benchmark Code-as-Policy 在机器人操作上的表现
- 关键发现：**performance 随人工抽象增加而涨，去掉就掉**；但用 **multi-turn 交互 + 结构化执行反馈 + visual differencing + 自动 skill 合成 + ensemble** 可以弥补
- → **对你最大启发**：你的 `_lib/judging/visual_diff/` 可以加一个"前后两帧 diff 给 VLM 看变化"工具；并验证 **automatic skill synthesis**（让 VLM 写 atomic SKILL.md 草案）

**COME-Robot** (arXiv:2404.10220, 2024-04, 被 Rollout 引用为 [19])
- GPT-4V 闭环移动操作
- 两个创新：(i) 多层 open-vocab perception + situated reasoning；(ii) **iterative closed-loop feedback + restoration**（验可行 → 监督执行 → 跨模块追失败因）
- → 启发：你的 `reset_failure_<mode>` 可以把"失败因归属哪个模块"作为 mode 维度（perception / planning / control / IK）

**Manipulate-Anything** (arXiv:2406.18915, 2024-06, [18])
- 无 privileged state、无手工 skill，VLM 直接驱动
- 关键贡献：**生成的轨迹可以训 BC，且比人示教 + VoxPoser/CaP 生成的更强**
- → 对你 zeroshot→train 飞轮的强证据：**VLM 调 base 工具生成的数据是可训练数据**

**Reflective Planning** (arXiv:2502.16707, 2025-02, [47])
- "想象未来状态 → 用预测引导动作 → 反思 suboptimality"
- → 启发：你的 `long_horizon/progress_judge` 可以加"VLM 想象 next-frame 应该长啥样"作为辅助信号

**Neuro-Symbolic CaP** (arXiv:2510.21302, 2025-10)
- 在 code 生成时加 **symbolic verification + interactive validation**
- 关键 trick：生成"探索性代码"主动去拿缺失的观察
- → 启发：你 zeroshot/policy.py 可以引入"如果不确定 → 先生成一段 active_perceive 代码再 plan"的两阶段

### B. 关键 perception/skill primitives（直接加进 _lib）

**ReKep** (arXiv:2409.01652, 2024-09, [27])
- **Python 函数 over 3D keypoints → numerical cost**；VLM 写 cost，求解器算 SE(3) 序列
- → 你的 `_lib/perception/task_keypoints/` 应该直接照抄 ReKep 风格

**CoPa** (arXiv:2403.08248, 2024-03, [43])
- 两阶段：task-oriented grasping + task-aware motion planning
- "coarse-to-fine grounding" 选抓取部位
- → 启发：你的 atomic.zeroshot 把"选 grasp part" 和 "post-grasp pose 推理"显式分两步

**MOKA** (arXiv:2403.03174, 2024-03, [34])
- Mark-based visual prompting（编号让 VLM 选）
- → 直接进 `_lib/perception/visual_prompt/`

**OVAL-Grasp** (arXiv:2511.20841, 2025-11)
- LLM 选 part + VLM 分割 + 2D affordance heatmap
- 95% part 识别 / 78% 实抓
- → 比 GraspGen 更轻；可作为 `_lib/grasping/oval_grasp/` 替代方案，依赖更少

### C. 数据 / 训练 / 中间表示

**RoboInter** (arXiv:2602.09973, 2026-02)
- 230k episodes，标注 10+ 类 **intermediate representations**（subtask、trace、keypoint 等）
- → 启发：你的 atomic 数据可以**不止存 obs-action**，还存 substep 序列、关键点、trace；为后续训"plan-conditioned policy"留接口

**LoHo-Manip** (arXiv:2604.21924, 2026-04)
- 长 horizon = **task-manager VLM + executor VLA**
- Manager 输出 (i) done+remaining subtask 列表（语言记忆）；(ii) **visual trace（2D keypoint 轨迹 prompt）**
- → 你 `long_horizon/plan` 可以试着输出 visual trace 喂给 atomic.policy，比纯语言指令更可控

### D. Tool design / Tool augmentation

**SpaceTools / DIRL** (arXiv:2512.04069, 2025-12)
- **两阶段 RL** 训 VLM 学多工具协作（depth/segmentation/pose）
- 突破了"以前只能 RL 单工具"的限制
- → 现在用不到（你不训 VLM），但**告诉你"多工具调度"本质是个 RL 问题**，未来 `posttrain/` 可以走这条路

**VLMgineer** (arXiv:2507.12644, 2025-07, [41]) + **RobotSmith** (arXiv:2506.14763, 2025-06)
- VLM **设计物理工具本身**，配进化搜索
- → 远期启发：你的 sim（RoboTwin）+ VLM 可以做"任务驱动的新 atomic 自动合成"

**CoRAL** (arXiv:2605.02600, 2026-05)
- LLM 不当 controller，**当 cost designer** 喂给 MPPI；VLM 给 mass/friction 先验，在线 sysid 修正
- → 启发：接触密集任务（穿钉、拧瓶盖）走这条路比纯 BC/π₀ 更稳

---

## Part 3 — 具体到 `skills/` 的改动清单

### 新增 base 原语（每个机器人都要）

```
skills/base/robotwin/
├── active_perceive/        ← 新增（zoom_in / look_around / multi_view，wrist camera）
└── move_to_pose_safe/      ← 新增（cuRobo collision-free 版的 move_to_pose）
```

### 新增 _lib（跨机器人复用）

```
skills/_lib/
├── geometry/                       ← 新增（向量算术，最便宜最值）
│   ├── distance/
│   ├── make_vector/
│   ├── angle_between/
│   └── rotate_vector/
├── perception/
│   ├── grounded_sam/               ← 新增（mask + centroid）
│   ├── foundation_stereo/          ← 新增（depth）
│   ├── vlm_pointing/               ← 新增（语言 → 2D 点，Gemini 或 GPT 都行）
│   ├── task_keypoints/             ← 新增（ReKep 风格关键点）
│   ├── visual_prompt/              ← 新增（Set-of-Mark / MOKA，画编号 + 6D pose overlay）
│   └── visual_diff/                ← 新增（CaP-X 启发，前后帧 diff）
├── grasping/
│   ├── graspgen/                   ← 新增（重型）
│   └── oval_grasp/                 ← 新增（轻量，LLM+SAM heatmap）
└── judging/
    └── vlm_monitor/                ← 新增（2Hz yes/no 监控，给 policy:vN 用）
```

### atomic 层模式升级

`skills/atomic/<task>/zeroshot/policy.py` 应该从"一次性 plan + 顺序执行"改成：

```python
# 伪码
substeps = vlm_plan(instruction, image)
for substep in substeps:
    code = vlm_codegen(substep, image, robot_state, available_tools)
    stdout = execute(code)
    new_image = capture_image()
    verdict = vlm_react(substep, new_image, stdout, robot_state)  # success / replan / abort
    if verdict == 'replan':
        substep_code = vlm_replan(substep, failure_image, stdout)
        # 失败 trace 进 reset_failure DataStore，同时进 in-context evolution
```

### atomic.zeroshot 的进化机制（in-context evolution，零训练成本）

仿 Rollout §III-C：

```
~/.roborsi/data/<task>/_evolution/
├── trial_001.json  { code, stdout, success/failure, vlm_analysis }
├── trial_002.json
└── ...
```

每次新 trial 把最近 N 条作为 in-context 示例喂 system prompt。**这是 `posttrain/` 之外的免费飞轮**。

---

## Part 4 — 跟你架构的关系图

```
                            ┌─ Rollout 的强项 ─┐
                            │  perception 深度  │
                            │  geometry 工具    │
                            │  active perceive │
                            │  visual prompt   │
                            └────────┬─────────┘
                                     │ 进
                                     ▼
RoboRSI 强项                ┌──────────────┐
─────────────                  │  _lib/       │
skill-first 工程结构      ────►│  base/       │
active_executor 飞轮            │              │
reset_success/failure 落库      └──────────────┘
跨机器人零成本扩展                     │
long_horizon 显式 skill                ▼
                              ┌──────────────────┐
                              │  你的 zeroshot   │
                              │  从 episode 级   │
                              │  → substep 级    │
                              │  plan-react-replan│
                              └──────────────────┘
```

**结论**：不动主架构（skill-first 是对的），只在 base/ 和 _lib/ 补齐 Rollout 的内容深度；atomic.zeroshot 升级到 substep-level 闭环。

---

## 参考

- Rollout (arXiv:2511.00917v1) — 主参考，v2 已撤回但 v1 仍可访问
- CaP-X (2603.22435) — coding agent benchmark + 改进
- COME-Robot (2404.10220) — GPT-4V 闭环移动操作
- Manipulate-Anything (2406.18915) — VLM 生成数据可训
- Reflective Planning (2502.16707) — 想象未来状态
- Neuro-Symbolic CaP (2510.21302) — 探索性代码 + 符号验证
- ReKep (2409.01652) — relational keypoint constraints
- CoPa (2403.08248) — spatial constraints of parts
- MOKA (2403.03174) — mark-based visual prompting
- OVAL-Grasp (2511.20841) — open-vocab task-oriented grasping
- RoboInter (2602.09973) — intermediate representation suite
- LoHo-Manip (2604.21924) — visual trace planning
- SpaceTools (2512.04069) — 多工具 RL
- VLMgineer (2507.12644) / RobotSmith (2506.14763) — VLM 设计工具
- CoRAL (2605.02600) — LLM 当 cost designer
