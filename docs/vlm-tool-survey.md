# 新 VLM+Tool 机器人工作综述（2024–2026）— 怎么做的 & 我们能学什么

> 2026-05-20 整理。聚焦于 **VLM 编排工具** 这一路线，不含纯 VLA。
> 每篇分三块：**他们怎么做** / **结果** / **robo-rsi 怎么学**。
> 配套见 [`rollout-and-vlm-tool-learnings.md`](./rollout-and-vlm-tool-learnings.md) 的总动作清单。

---

## 排序

按对 robo-rsi 当前阶段的相关性：

1. [Rollout](#1-rollout--upenn-2025-11) — 主参考
2. [CaP-X / CaP-Agent0](#2-cap-x--berkeley-stanford-cmu-nvidia-2026-03) — benchmark + 5 个 agentic trick
3. [LoHo-Manip](#3-loho-manip--ucsd--nvidia-2026-04) — visual trace 长 horizon
4. [COME-Robot](#4-come-robot--bigai-2024-04) — 闭环 + 失败溯源
5. [Reflective Planning](#5-reflective-planning--2025-02) — 想象未来 + 反思
6. [Neuro-Symbolic CaP](#6-neuro-symbolic-cap--2025-10) — 探索性代码 + 符号验证
7. [SpaceTools / DIRL](#7-spacetools--nvidia-2025-12) — 多工具协调用 RL 教
8. [ReKep](#8-rekep--stanford-2024-09) — keypoint constraints
9. [CoPa](#9-copa--2024-03) — 两阶段（grasp + post-grasp）
10. [MOKA](#10-moka--ucb-2024-03) — mark-based 视觉 prompt
11. [Manipulate-Anything](#11-manipulate-anything--2024-06) — VLM 生数据可训
12. [OVAL-Grasp](#12-oval-grasp--2025-11) — 轻量 task-oriented grasping
13. [RoboInter](#13-robointer--2026-02) — 中间表示数据
14. [CoRAL](#14-coral--2026-05) — LLM 当 cost designer
15. [VLMgineer / RobotSmith](#15-vlmgineer-2025-07--robotsmith-2025-06) — VLM 设计工具本身

---

## 1. Rollout — UPenn, 2025-11

**arXiv:2511.00917v1** · [rollout-robot.github.io](https://rollout-robot.github.io/)

### 怎么做

- VLM coding agent（Gemini Robotics-ER 1.5）编排一套 **perception/reasoning/control/learned-policy/image-edit** 模块写代码
- 闭环 `plan → react → replan`：每个 substep 执行后把 RGB + stdout + state 回灌 VLM
- `evolution`：把 trial 的 (code, stdout, Gemini 失败分析) 存库，下次作为 in-context 例子

### 关键工具

- **Active perception**（腕相机 zoom/look around）—— 消融 −31%
- **Geometry/linalg**（测距、构向量、夹角、绕角旋转）—— 消融 −18%
- ReKep 风格 **task-relevant keypoints**（mask + grid + GPT-o3 选）
- **cuRobo** collision-free planning、**GraspGen**、**FoundationStereo** 深度
- 把 **π₀.₅ 当 callable tool**，用 **Qwen2.5-VL-72B 本地 2Hz yes/no** 监控打断
- **Set-of-Mark 风格 image editing**（draw points / overlay 6D pose）

### robo-rsi 学习点

- 主架构层不变（你的 skill-first 已经比 Rollout 的硬编码 prompt 更模块化），但**内容深度差一截**
- 加 `_lib/geometry/` + `base/<robot>/active_perceive/` 是性价比最高的两件
- atomic.zeroshot 从 episode 级闭环改成 substep 级

---

## 2. CaP-X — Berkeley/Stanford/CMU/NVIDIA, 2026-03

**arXiv:2603.22435** · [capgym.github.io](https://capgym.github.io)
作者群里有 Fei-Fei Li、Yuke Zhu、Jim Fan、Ken Goldberg。

### 怎么做

三层：**CaP-Gym**（交互环境）→ **CaP-Bench**（12 个模型横评）→ **CaP-Agent0**（training-free agent）+ **CaP-RL**（带 verifiable reward 的 RL）

核心发现：模型能力随**人工抽象**递减而下降；但 5 个 agentic trick 可以补回来：
1. **Multi-turn 交互**（debugging 提示）
2. **Structured execution feedback**（结构化 stdout 回灌）
3. **Visual differencing**（前后帧 diff 给 VLM 看）
4. **Automatic skill synthesis**（让 VLM 自己合成 task-agnostic skill 库）
5. **Ensembled reasoning**（多 temperature 集成）

### 结果

CaP-Agent0 不训练就能在多个 manipulation 任务上达到人类水平，部分超越后训练的 VLA。

### robo-rsi 学习点

- ⭐ **Visual differencing**：你 `_lib/perception/` 加一个 `visual_diff/`，每个 substep 之后把执行前/后两张图叠加交叉差异，喂 VLM 比单纯给后帧更准。**便宜且通用**。
- ⭐ **Automatic skill synthesis**：CaP-Agent0 让 VLM 在跑任务过程中自动析出"task-agnostic skill"加进库。对位你的"VLM 帮人写 atomic SKILL.md 草案"——值得验。
- **Ensemble**：跑 zeroshot 时多 temperature 跑 3 次取最优 code，可显著降单点失败。建议在 atomic.zeroshot 加一个开关。
- **CaP-RL**（verifiable reward 的 RL）：未来 `posttrain/` 可以走这条，比 BC-only 更接近最终 deployable policy。

---

## 3. LoHo-Manip — UCSD + NVIDIA, 2026-04

**arXiv:2604.21924** · [liuisabella.com/LoHoManip](https://www.liuisabella.com/LoHoManip)

### 怎么做

**Task-manager VLM** + **Executor VLA** 解耦。Manager 在 receding-horizon 模式下被反复调用，每次输出：
1. **subtask 序列 + 显式 done/remaining 切分**（轻量语言记忆，不需要 history buffer）
2. **Visual trace**：一条 2D keypoint 轨迹画在当前观察上，告诉 VLA "去哪 / 接近什么"

Executor VLA 被 finetune 成 trace-conditioned。
**隐式闭环**：失败的 step 会留在下次 manager 输出里，trace 自然更新，不需要手写 recovery 逻辑。

### robo-rsi 学习点

- ⭐ **你的 `long_horizon/<task>/plan` 当前输出"atomic 序列 + 参数"，可以加一类输出 visual trace**，把它喂给 atomic 的 policy。这比纯语言指令的可控性高一档。
- ⭐ **"done/remaining 显式切分"是替代 history buffer 的极简方案**——`long_horizon/progress_judge/` 直接借鉴。
- 长 horizon 失败恢复**不需要专门写 reset logic**：只要每步重新 plan，错过的 step 自动重排。对你的 `reset_failure_<mode>` 设计是个反思——可能不是所有失败都要专 skill，简单的"重 plan"够。

---

## 4. COME-Robot — BIGAI, 2024-04

**arXiv:2404.10220** · GPT-4V 闭环移动操作

### 怎么做

两个核心模块：
1. **Multi-level open-vocab perception + situated reasoning**：3D 环境探索、目标识别用 commonsense
2. **Iterative closed-loop feedback + restoration**：分三步
   - **Verify feasibility**（任务可行吗？）
   - **Monitor success**（执行中实时打分）
   - **Trace failure cause across modules**（按 perception / planning / control 归因）

### 结果

8 个真机任务，比当时 SOTA 高约 35%。

### robo-rsi 学习点

- ⭐ **失败归因到模块**：你的 `reset_failure_<mode>` 现在按"现象"分（碰倒、抓空），不如按**模块归因**分：
  - `reset_failure_perception/`（看错了）
  - `reset_failure_planning/`（plan 错了）
  - `reset_failure_control/`（执行失败）
  - `reset_failure_ik/`（IK/碰撞）
  
  这样的分类对训 reset policy 更有泛化性。
- **Verify feasibility 前置**：atomic 接到任务先调一次"看一眼场景判断这个任务在此场景能不能做"，能省大量无效 trial。

---

## 5. Reflective Planning — 2025-02

**arXiv:2502.16707** · [reflect-vlm.github.io](https://reflect-vlm.github.io)

### 怎么做

测试时计算框架，三步循环：
1. **Imagine** future world state（用生成模型）
2. **Use prediction to guide action**
3. **Reflect on suboptimality** 修正推理

效果在 multi-stage 操作上**超 MCTS** 和商业 VLM。

### robo-rsi 学习点

- "想象 next-frame 应该长什么样" 可以作为 `_lib/judging/` 的辅助信号：让 VLM 生成"预期下一帧描述"作为前置判据，对比实际结果。
- 适合 long_horizon，可以在 `progress_judge/` 里加这个机制。
- ⚠️ 需要图像生成模型，权重大；先观察够用了再上。

---

## 6. Neuro-Symbolic CaP — 2025-10

**arXiv:2510.21302**

### 怎么做

在 code 生成阶段加：
- **Symbolic verification**：生成的代码必须符合预定义符号约束
- **Interactive validation**：生成"探索性代码"主动去拿缺失的观察，再生成执行代码

任务成功率 +46.2%（在 RLBench + 真机）。

### robo-rsi 学习点

- ⭐ **两阶段 codegen**：你的 `zeroshot/policy.py` 应该把"探索代码"和"执行代码"分两步：
  ```
  step 1: 如果场景信息不全 → VLM 写一段 active_perceive 代码 → 执行
  step 2: 信息齐了 → VLM 写执行代码
  ```
- 对应 Rollout 的 active_perception，但更显式地变成两个 prompt round。

---

## 7. SpaceTools / DIRL — NVIDIA, 2025-12

**arXiv:2512.04069**

### 怎么做

VLM 协调多个视觉工具（depth / segmentation / pose）用 **Double Interactive RL**：
- **Teaching phase**：单工具 RL specialist 的 demo + frontier 模型的 all-tools trace 合在一起 SFT
- **Exploration phase**：继续 interactive RL 精调多工具协调

工具库（"Toolshed"）：vision tools + robotic tools，可热插拔。

### robo-rsi 学习点

- 现在不训，但**架构启发**：你的 `_lib/` 应该成为一个明确的 **"Toolshed"**——每个工具用统一 API 描述（输入 / 输出 / 调用代价 / 何时用），让 VLM 自己挑。SKILL.md 的 frontmatter 应该有 `cost`、`when_to_use`、`example_call` 字段。
- ⚠️ 远期：当你想做 atomic 的 RL 微调（多工具调度策略），DIRL 的两阶段是当前最佳实践。

---

## 8. ReKep — Stanford, 2024-09

**arXiv:2409.01652** · [rekep-robot.github.io](https://rekep-robot.github.io)

### 怎么做

任务表示为 **Relational Keypoint Constraints**：Python 函数 over 3D keypoints → 数值 cost。
任务 = constraint 序列；执行用层次化优化求 SE(3) 末端位姿；用 large VM/VLM 自动从语言+RGB-D 产 ReKep（不需要手标）。

### robo-rsi 学习点

- ⭐ 你的 `_lib/perception/task_keypoints/` 直接照搬 ReKep 的产生流程
- **task = constraint sequence** 是个非常优美的表达，比 "task = code" 更结构化、更容易做 progress_judge
- 可以作为 `atomic/<task>/SKILL.md` 里 `success_predicate` 的可选表达形式（python lambda on keypoints）

---

## 9. CoPa — 2024-03

**arXiv:2403.08248**

### 怎么做

明确分两阶段：
1. **Task-oriented grasping**：VLM 用 "coarse-to-fine grounding" 选物体的哪个部位抓
2. **Task-aware motion planning**：VLM 标 task-relevant 部位的几何约束 → 推 post-grasp pose

### robo-rsi 学习点

- "grasp 部位选择"是单独问题，建议在 `_lib/grasping/` 加一个 **"grasp part selector"**，跟 GraspGen 串联。
- "post-grasp 是另一个推理步骤"——你的 atomic.zeroshot 可以把抓和放后拆成显式两子步，每一步都过 VLM。

---

## 10. MOKA — UCB, 2024-03

**arXiv:2403.03174**

### 怎么做

**Mark-based visual prompting**：候选 affordance 在图上用数字编号，让 VLM **选编号**（比让它给像素坐标准很多）。

### robo-rsi 学习点

- ⭐ **`_lib/perception/visual_prompt/`** 必须有 set-of-mark 功能。这是几乎所有 VLM-as-policy 论文的共识。
- 简单实现：`mask_with_id(image, masks)` 函数，返回带编号的图 + (id → 实体)映射。

---

## 11. Manipulate-Anything — 2024-06

**arXiv:2406.18915** · [robot-ma.github.io](https://robot-ma.github.io)

### 怎么做

无 privileged state、无手设计 skill、可操作任意静态物体。
生成的演示轨迹用来训 BC policy。

### 关键证据

**VLM 生成的数据训出来的 BC 比 VoxPoser / Scaling-up / CaP 生成的更强**，部分场景超人示教。

### robo-rsi 学习点

- ⭐ 这是你"zeroshot → train"飞轮的**外部铁证**：VLM 调 base tools 生成的成功轨迹是有效训练数据
- 给你设计目标背书——不用担心 zeroshot data 训不出 policy

---

## 12. OVAL-Grasp — 2025-11

**arXiv:2511.20841**

### 怎么做

LLM 选 part → VLM 分割该 part → 生成 2D affordance heatmap → 抓取。
真机 95% part 识别率 / 78.3% 实抓成功率（20 物体 × 3 任务）。

### robo-rsi 学习点

- 比 GraspGen 轻得多，依赖只有 LLM + SAM
- 建议作为 `_lib/grasping/oval_grasp/`，跟 `_lib/grasping/graspgen/` 并列；zeroshot 阶段 default 用 OVAL（启动快），训出 policy 后切 GraspGen 或干脆用 π₀.₅

---

## 13. RoboInter — 2026-02

**arXiv:2602.09973**

### 怎么做

发布 230k episode 数据集，密集标注 **10+ 类中间表示**（subtask, trace, keypoint, ...）和半自动标注 GUI。

### robo-rsi 学习点

- ⭐ 你的 atomic 数据格式现在是 (obs, action) pair；**扩成 (obs, action, intermediates)**：
  - `subtask_id`（当前在哪个 substep）
  - `keypoints`（当前 frame 的任务关键点）
  - `trace`（接下来 N 步的 2D 轨迹）
- 这些字段为后续训 LoHo-Manip 风格的 trace-conditioned policy 留接口
- 不会增加多少存储（每帧几十个 float），但开门很多

---

## 14. CoRAL — 2026-05

**arXiv:2605.02600**

### 怎么做

LLM **不当 controller**，**当 cost designer** 给 **MPPI**（sampling-based motion planner）合成 cost 函数。
**Neuro-symbolic adaptation loop**：VLM 给 mass/friction 先验 → 在线 sysid 实时修正 → LLM 根据交互反馈改 cost 结构。
带 retrieval-based memory 复用成功策略。

### robo-rsi 学习点

- 接触密集任务（穿钉、拧瓶盖、按按钮）走这条比纯 BC 稳。**远期收藏**。
- 现在用不到 MPPI 但**"LLM 给物理参数先验"** 这个 idea 可以在 `_lib/reasoning/physical_priors/` 实现：VLM 看图估物体质量/摩擦/刚度，给 control 模块当 default。

---

## 15. VLMgineer (2025-07) / RobotSmith (2025-06)

**arXiv:2507.12644** / **arXiv:2506.14763**

### 怎么做

VLM **设计物理工具本身**（不是调用工具）。code generation + evolutionary search 共同优化"工具几何 + 操作策略"。

### robo-rsi 学习点

- 现在远超你的 scope。
- 但**理念延伸**到软件侧：你的 sim（RoboTwin）+ VLM 可以做**"任务驱动的 atomic 自动合成"**——给定一个长 horizon 目标，VLM 自己提议要添加哪些 atomic skill，写草案 SKILL.md，进化搜索（trial-and-error）来精修参数。
- 这是 `_lib/minting/`（你已经有的"造 skill"工具盒）的天花板版本。

---

## 横向归纳：5 个跨论文共识

按出现频率排，这些**几乎所有论文都同意**：

1. **闭环 > 开环**：plan-execute-react-replan，每个 substep 都给 VLM 回灌（Rollout / COME / CaP-X / LoHo / Reflective）
2. **Mark-based visual prompt**：让 VLM 选编号比让它给像素坐标准（MOKA / Rollout / PIVOT / OVAL）
3. **Coarse-to-fine perception**：raw → mask → keypoint 分级，VLM 自挑（Rollout / CoPa / OVAL）
4. **失败要溯源**：归因到模块比"重跑一次"有效（COME / Neuro-Sym CaP / Rollout evolution）
5. **几何/物理需要外脑工具**：VLM 没 spatial CoT，必须有 vector/distance/rotation 函数（Rollout 消融 / CoRAL / ReKep）

## 横向归纳：5 个跨论文分歧

1. **VLM 写 code 还是 VLM 写 constraint**：Rollout/CaP-X 写 code；ReKep/CoPa 写 constraint
2. **Plan 输出形式**：纯语言（COME / Rollout）vs visual trace（LoHo-Manip）
3. **是否训 VLM**：训（SpaceTools）vs 不训（Rollout / CaP-Agent0）
4. **Recovery 机制**：显式 recovery skill（很多）vs 每步重 plan 隐式恢复（LoHo-Manip）
5. **抓取**：重型模型（GraspGen）vs 轻量 affordance heatmap（OVAL-Grasp）

---

## 给 robo-rsi 的"高 ROI" 增量改动（按优先级）

按"性价比"重新排：

| 优先级 | 改动 | 来源 | 工时 |
|---|---|---|---|
| P0 | `_lib/geometry/` 4 个函数 | Rollout 消融 | 0.5d |
| P0 | `_lib/perception/visual_prompt/`（set-of-mark） | MOKA / Rollout / 几乎所有 | 1d |
| P0 | atomic.zeroshot → substep 级 plan-react-replan | Rollout / CaP-X / LoHo | 2d |
| P0 | `_lib/perception/visual_diff/`（前后帧 diff 给 VLM） | CaP-X | 0.5d |
| P1 | `base/<robot>/active_perceive/` | Rollout 消融 | 1d |
| P1 | 两阶段 codegen（探索 → 执行） | Neuro-Sym CaP | 0.5d |
| P1 | atomic 数据扩 intermediate 字段（subtask/keypoints/trace） | RoboInter / LoHo | 1d |
| P1 | `_lib/judging/vlm_monitor/`（高频 yes/no） | Rollout | 1d |
| P2 | `reset_failure_<module>` 重新分类（perception/plan/control/ik） | COME | 1d |
| P2 | `_lib/perception/task_keypoints/`（ReKep 风格） | Rollout / ReKep | 1.5d |
| P2 | `_lib/grasping/oval_grasp/` | OVAL-Grasp | 1d |
| P2 | zeroshot 多 temperature ensemble | CaP-X | 0.5d |
| P3 | `long_horizon/plan` 加 visual trace 输出 | LoHo-Manip | 2d |
| P3 | `_lib/reasoning/physical_priors/`（VLM 估质量/摩擦） | CoRAL | 1d |
| P4 | Automatic skill synthesis（VLM 帮人写 atomic SKILL.md 草案） | CaP-X / VLMgineer | 3d |
| P4 | atomic 的 RL 后训用 DIRL | SpaceTools | 大 |

P0 + P1 加起来约 **8.5 工日**，做完 robo-rsi 的 atomic.zeroshot 应该能跟 Rollout 同一档。

---

## 参考速查表

| 论文 | ID | 一句话 |
|---|---|---|
| Rollout | 2511.00917 | VLM 编排 module 的 zero-shot generalist |
| CaP-X | 2603.22435 | CaP 系统 benchmark + 5 个 agentic trick |
| LoHo-Manip | 2604.21924 | task-manager VLM 输 visual trace + executor VLA |
| COME-Robot | 2404.10220 | GPT-4V 闭环 + 跨模块失败溯源 |
| Reflective Planning | 2502.16707 | 想象未来 + 反思 |
| Neuro-Symbolic CaP | 2510.21302 | 探索代码 + 符号验证 |
| SpaceTools | 2512.04069 | Double Interactive RL 教多工具协调 |
| ReKep | 2409.01652 | 任务 = relational keypoint constraints |
| CoPa | 2403.08248 | grasp 选 part + post-grasp 推理 |
| MOKA | 2403.03174 | mark-based visual prompting |
| Manipulate-Anything | 2406.18915 | VLM 生数据能训 BC |
| OVAL-Grasp | 2511.20841 | 轻量 task-oriented grasp |
| RoboInter | 2602.09973 | 中间表示数据集 |
| CoRAL | 2605.02600 | LLM 当 cost designer 喂 MPPI |
| VLMgineer | 2507.12644 | VLM 设计物理工具 |
| RobotSmith | 2506.14763 | 同上 |
