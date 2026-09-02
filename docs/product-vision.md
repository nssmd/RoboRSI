# RoboRSI 产品愿景

## 一句话

让 VLM 用 **skill** 做两件事：**自动数据采集** 和 **长时任务执行**。

---

## 为什么做

把"具身侧的 OpenHermes"做成一个开源平台。

VLA 流水线的现状是三段拆开（**采** ↔ **训** ↔ **部**），各靠人手粘合：

- **采** 要人遥操、要人重置场景，体力活，scale 不动
- **训** 跟"采"用的语义不一致，distribution shift
- **部** 长时任务一阶失败就崩，没救
- **多 policy 管理**靠脚本胶水，O(N²)

RoboRSI 的回答是：**统一在 skill 框架下**。
平台把"该做什么 / 该练什么 / 该评什么"全部外置成 skill 文件，VLM 当司机，skill 当车。

---

## 做什么 · 两个目标

### 目标 1 · 自动数据采集

每个 atomic task 一旦定义好（zeroshot / train / eval / reset），平台就能把"刷数据"压低到一行命令。
关键引擎：

- VLM 用 base 工具零样本试 → 攒成功轨迹 → π₀.5 finetune → 成功率超阈值就把"刷数据"的 executor 从 VLM 切到 policy → 成本指数下降
- reset 是数据飞轮的瓶颈，分两类（success / failure），各自落 DataStore，未来训出 reset policy

### 目标 2 · 长时任务执行

一句"把桌子收拾干净"自动拆成 atomic 序列、按需选 executor、phase 边界打分、失败自救：

- VLM 拆解（plan）→ 每个 atomic 用它当前最稳的 executor 跑 → progress_judge 把关 → posttrain 整体回流

---

## 做不做的边界

| 做 | 不做 |
|---|---|
| 三层 skill 抽象（base / atomic / long_horizon） | 端到端"训一个万能 VLA"赌长尾 |
| sim-first 验证（RoboTwin 优先），实机随后 | 自研 sim 引擎或自研 robotics SDK |
| 复用 LeRobot / π₀ / Anthropic 等开源底层 | 重新造神经网络框架 |
| 每个机器人独立 base 适配 | 一个 base 套全机器人 |
| 失败也是数据（reset_failure 落库） | 失败就丢、靠人补 |

---

## 北极星指标

不看"系统级成功率"，看每个 atomic 的成长曲线：

1. 出生时（VLM 零样本）→ ≥ 起跑线（值得继续）
2. 训完 → ≥ expert baseline（学会了）
3. RL 后 → > expert（真泛化）

每多一个 atomic 走完这条曲线，平台多一份能力。**加新任务 = 加 markdown，不动 Python**。

---

## 致敬

- 通用 Agent skill runtime —— 提供了 Markdown skill 组织方式的相关实践
- [Rollout (UPenn)](https://rollout-robot.github.io/) —— VLM 编排参考

走自己的路：**skill-first**，不是 agent-first，也不是 model-first。
