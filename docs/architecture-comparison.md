# Architecture Comparison: LeRobot / dimos / RoboRSI

> 2026-03-23 — 三方架构对比与 RoboRSI 定位分析

---

## 0. 架构总览图

### LeRobot 架构

```
                           没有 Agent / LLM 层，CLI 直连一切

┌─ CLI（用户入口，18 commands）─────────────────────────────────────────────────┐
│  ┌───────┐ ┌────────┐ ┌───────────┐ ┌──────┐ ┌───────────┐ ┌────────────┐  │
│  │ train │ │ record │ │teleoperate│ │ eval │ │ calibrate │ │ find-*     │  │
│  └───────┘ └────────┘ └───────────┘ └──────┘ └───────────┘ └────────────┘  │
└───────┬──────────────────────────┬──────────────────────────────┬───────────┘
        │                          │                              │
        ▼                          ▼                              ▼
┌─ Policies（策略层）──────┐ ┌─ Dataset（数据层）─────────┐ ┌─ Hardware（硬件层）──┐
│                          │ │                            │ │                      │
│  ACT     Diffusion       │ │  LeRobotDataset            │ │  Robot (ABC)         │
│  VQ-BeT  Pi0 / Pi0.5    │ │  Parquet + MP4             │ │  ├─ Teleoperator     │
│  SmolVLA GR00T N1.5     │ │       ▼                    │ │  ├─ Camera           │
│  HIL-SERL  TDMPC        │ │  HuggingFace Hub           │ │  └─ MotorsBus        │
│      14 policies         │ │  push / pull / streaming   │ │     ├─ Dynamixel     │
│                          │ │                            │ │     ├─ Feetech       │
│  ┌──────────────────┐   │ │  Stats  Video Encoder      │ │     └─ Damiao / ...  │
│  │Processor Pipeline│   │ │  Delta Timestamps          │ │                      │
│  │norm→tok→delta→bat│   │ └────────────────────────────┘ │  11 robots           │
│  └──────────────────┘   │                                │  SO100 Koch LeKiwi   │
│                          │ ┌─ Sim Envs（仿真，4 envs）─┐ │  Reachy2 Unitree G1  │
│  ┌──────────────────┐   │ │  ALOHA  LIBERO             │ │                      │
│  │gRPC Async Infer. │   │ │  MetaWorld  PushT          │ │  13 teleop devices   │
│  │PolicyServer↔Robot│   │ │  (全部第三方 wrapper)      │ │  leader arm 手柄     │
│  └──────────────────┘   │ └────────────────────────────┘ │  键盘 手机 ...       │
└──────────────────────────┘                                └──────────────────────┘

特征：数据 + 训练管线业界最强（14 算法 / Hub 生态 / 11 机器人）
缺失：没有 Agent / LLM / 自然语言 / 安全框架 / 内置仿真 / Web 可视化
```

---

### dimos 架构

```
┌─ UI（用户入口）─────────┐                                    ┌──────────────┐
│  ┌─────┐ ┌────────────┐ │          消息/回复                  │     LLM      │
│  │ CLI │ │  Web UI    │ │◄────────────────────────────────────│   (大模型)   │
│  └─────┘ │ FastAPI/WS │ │                                    │  GPT-4o      │
│          └────────────┘ │                                    │  Claude      │
└──────────┬──────────────┘                                    │  Ollama      │
           │ 消息                                               └──────┬───────┘
           ▼                                                           │
┌─ Agent Layer（智能体层）─────────────────────────────────────────────┘
│                                                         工具调用/回复
│   LangGraph Agent     VLM Agent      MCP Server
│   @skill → StructuredTool 自动转换
└──────────┬──────────────────────────────────────────────────────────┐
           ▼                                                          │
┌─ Core（模块/蓝图引擎）─────────────────────────────────────────┐   │
│   Module (forkserver 进程隔离)                                  │   │
│   Blueprint (声明式组合 + autoconnect 自动连线)                 │   │
│   In[T] / Out[T] 类型化流                                      │   │
└────┬──────────┬──────────────┬──────────────┬──────────────────┘   │
     ▼          ▼              ▼              ▼                      │
┌─ Percep. ┐ ┌─ Navig. ──┐ ┌─ Manip. ──┐ ┌─ Control ──────────┐   │
│ YOLO 2D/3D│ │ A* 规划   │ │ Pick&Place│ │ Tick Loop          │   │
│ Person ReID│ │ 前沿探索 │ │ GraspGen  │ │ read→compute→      │   │
│ Obj Track │ │ 视觉伺服  │ │ 运动规划  │ │ arbitrate→route→   │   │
│ Spatial   │ │ Costmap   │ │ (Drake)   │ │ write              │   │
└─────┬─────┘ └──────────┘ └──────────┘ └──────────┬──────────┘   │
      ▼                                              ▼              │
┌─ Memory ──────┐              ┌─ Transport ──────────────────────┐ │
│ CLIP 嵌入     │              │  LCM   SHM   ROS2 Bridge   DDS  │ │
│ ChromaDB      │              └──────────────┬───────────────────┘ │
│ 时空 RAG      │                             ▼                     │
└───────────────┘    ┌─ Hardware ──────┐ ┌─ Sim ──────┐            │
                     │ Go2  G1  xArm7  │ │ MuJoCo     │            │
┌─ Teleop ──────┐   │ Piper  MAVLink  │ │ Replay     │            │
│ 键盘 手机     │──▶│                 │ └────────────┘            │
│ Meta Quest    │   └─────────────────┘                            │
└───────────────┘                                                   │
                                                                    │
      没有数据采集 / 训练 / 算法 / Dataset ◄────────────────────────┘
      没有低成本臂 (SO101/Koch)

特征：Agent + 感知 + 导航 + 空间记忆业界最强
缺失：完全没有数据采集、训练、策略算法 — 所有行为都是手写 Skill
```

---

### RoboRSI 终极架构

从第一性原理出发的核心洞察：

```
1. RoboRSI 不是 "带 Agent 的机器人框架"，而是 "会控制机器人的 Agent"
   → Agent 是架构中心，不是附属模块

2. LeRobot 的数据+训练管线已是业界最优，重写是浪费
   → 直接作为引擎集成，RoboRSI 做驾驶舱

3. 安全不能靠 prompt 提醒，必须是架构级强制
   → Safety Gateway 拦截每一条到硬件的指令

4. Agent（慢脑，LLM）和 Control Runtime（快脑，确定性）必须分离
   → Agent 决定做什么（100ms+），Control 决定怎么做（20ms 级）
```

```
┌─ Interface（接入层）─────────────────────────────────────────────────────────┐
│                                                                              │
│   ┌─────────────┐   ┌──────────────┐   ┌──────────────────────────────────┐ │
│   │     CLI     │   │   Web UI     │   │  Channels                       │ │
│   │ roborsi    │   │  控制面板    │   │  Discord  Telegram  WeChat  ... │ │
│   │ agent       │   │  仿真查看器 │   │                                  │ │
│   └──────┬──────┘   └──────┬───────┘   └────────────────┬────────────────┘ │
│          └─────────────────┴────────────────────────────┘                   │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │ 消息/回复
                                       ▼
┌─ Agent Runtime（慢脑 · 智能体运行时）────────────────────┐       ┌──────────┐
│                                                          │       │   LLM    │
│   ┌──────────────────────────────────────────────────┐   │       │  (大模型) │
│   │                 Agent Loop                       │◀══╪══════▶│          │
│   │            对话理解 → 意图分析 → 工具调用         │   │       │  Claude  │
│   └──────────────────────────────────────────────────┘   │       │  GPT     │
│                                                          │       │  Local   │
│   ┌──────────┐   ┌──────────────┐   ┌────────────────┐  │       └──────────┘
│   │ Memory   │   │ Spawn + Tool │   │ Lifecycle Mgr  │  │
│   │ 对话记忆 │   │ 子agent 派发 │   │ 生命周期管理   │  │
│   │ 用户偏好 │   │ 工具调用     │   │ 阶段状态追踪   │  │
│   └──────────┘   └──────┬───────┘   └────────────────┘  │
└──────────────────────────┬───────────────────────────────┘
                           │ 调用 Skill
                           ▼
┌─ Skill Ecosystem（技能生态 · Skill to Any Embodiment）──────────────────────┐
│                                                                              │
│  ┌─ Primitive（原语）──┐ ┌─ Skill（技能）─────┐ ┌─ Policy（策略）────────┐ │
│  │ 本体绑定 · 原子动作 │ │ 本体无关 · 可组合  │ │ 学习获得 · 可部署     │ │
│  │ move_joint           │ │ pick_and_place     │ │ ACT checkpoint        │ │
│  │ open_gripper         │ │ pour_water         │ │ Diffusion checkpoint  │ │
│  │ set_velocity         │ │ stack_blocks       │ │ VLA fine-tune         │ │
│  └──────────────────────┘ └────────────────────┘ └───────────────────────┘ │
│                                                                              │
│  Skill Hub：社区共享 · 上传 / 下载 / 复用 · 能力匹配 · 自动适配             │
└──────────┬──────────────────────┬───────────────────────────┬────────────────┘
           │ 派发                  │                           │
           │ ▲ 注册 Primitive      │ ▲ 注册 Policy             │ ▲ 提供感知
           ▼ │                     ▼ │                         ▼ │
┌═ Embodiment Engine ════╗ ┌─ Learning Engine ─────┐ ┌─ Perception Engine ────┐
║ (控制 + 安全)          ║ │ (数据 + 训练 + 部署)  │ │ (感知 + 视觉 + 记忆)  │
║                        ║ │                       │ │                       │
║ ┌────────────────────┐ ║ │ ┌───────────────────┐ │ │ ┌───────────────────┐ │
║ │ Control Dispatch   │ ║ │ │ Data Collector    │ │ │ │ Camera Manager    │ │
║ │ 意图 → Primitive   │ ║ │ │ Episode 录制      │ │ │ │ 多相机 / 流媒体  │ │
║ │ 遥操作调度         │ ║ │ │ 采集 GUI          │ │ │ └───────────────────┘ │
║ └────────────────────┘ ║ │ └───────────────────┘ │ │ ┌───────────────────┐ │
║ ┌────────────────────┐ ║ │ ┌───────────────────┐ │ │ │ Detection         │ │
║ │ Embodiment Reg.    │ ║ │ │ Dataset Manager   │ │ │ │ YOLO / DINO       │ │
║ │ Manifest / 能力    │ ║ │ │ Parquet + MP4     │ │ │ │ 分割 / 追踪       │ │
║ │ Builtins / 用户    │ ║ │ │ Hub 推拉          │ │ │ └───────────────────┘ │
║ └────────────────────┘ ║ │ │ 编辑 / 回放       │ │ │ ┌───────────────────┐ │
║ ╔════════════════════╗ ║ │ └───────────────────┘ │ │ │ VLM               │ │
║ ║   Safety Gateway   ║ ║ │ ┌───────────────────┐ │ │ │ 场景理解 / 问答   │ │
║ ║  关节限位 / 力矩   ║ ║ │ │ Policy Library    │ │ │ │ 视觉反馈给 Agent  │ │
║ ║  碰撞检测 / 急停   ║ ║ │ │ ACT  Diffusion    │ │ │ └───────────────────┘ │
║ ║  每条指令必经此处  ║ ║ │ │ Pi0  SmolVLA      │ │ │ ┌───────────────────┐ │
║ ╚════════════════════╝ ║ │ │ GR00T  VQ-BeT ... │ │ │ │ Spatial Memory    │ │
║                        ║ │ └───────────────────┘ │ │ │ 嵌入 + 向量库     │ │
║  RoboRSI 自研         ║ │ ┌───────────────────┐ │ │ │ 空间 RAG          │ │
║                        ║ │ │ Train + Deploy    │ │ │ └───────────────────┘ │
║                        ║ │ │ 训练编排 / 调参   │ │ │                       │
║                        ║ │ │ gRPC 推理服务     │ │ │                       │
║                        ║ │ │ 部署监督 / 复位   │ │ │                       │
║                        ║ │ └───────────────────┘ │ │                       │
║                        ║ │  LeRobot 驱动         │ │  标准 ML 库驱动       │
╚════════════════════════╝ └───────────┬───────────┘ └───────────┬───────────┘
              │                        │                         │
              └────────────────────────┼─────────────────────────┘
                                       │
                                       ▼
┌─ ROS2（统一通信层 · 三引擎共享 · 唯一传输协议）──────────────────────────────┐
│  /joint_commands  /joint_states  /camera/*  /gripper  /episode  /safety     │
└──────────────────────┬──────────────────────────────────┬────────────────────┘
                       │                                  │
                       ▼                                  ▼
┌─ Real World（真机）──────────┐  ◀══ sim-to-real ══▶  ┌─ Sim World（仿真）──────────┐
│                               │    同一 ROS2 接口     │                              │
│  ┌─────────┐  ┌────────────┐ │    无缝切换           │  ┌──────────────────────┐    │
│  │  Robot  │  │   Sensor   │ │                        │  │  MuJoCo Runtime      │    │
│  └─────────┘  └────────────┘ │                        │  │  + Web 3D Viewer     │    │
└──────────────┬────────────────┘                        └──────────────┬───────────────┘
               ▲                                                        ▲
               │                                                        │
               └────────────────────────┬───────────────────────────────┘
                                        │ 注入新本体
┌─ Embodiment Onboarding（任意本体接入 · 零代码范式）─────────────────────────────────┐
│                                                                                     │
│  对话描述硬件 → Agent 生成 manifest → 自动生成 adapter → 引导校准 → 即刻可用       │
│                                                                                     │
│  真机：探测串口 → 识别协议 → 生成 ROS2 adapter → 校准             ──▶ Real World   │
│  仿真：提供 URDF/MJCF 或对话描述 → 加载/生成仿真模型 → ROS2 adapter ──▶ Sim World  │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

```
三者关系：
  LeRobot  → 最好的 "数据 → 训练 → 策略" 管线，但没有 Agent 入口
  dimos    → 最好的 "Agent → 感知 → 控制" 架构，但没有学习能力
  RoboRSI → Agent 驾驶舱 + LeRobot 引擎 + 安全框架 + 感知层 = 完整闭环

两个世界的关系：
  · 暴露完全相同的 ROS2 接口（topic 名 / 消息类型）
  · 策略在仿真训练验证 → 零修改部署到真机
  · 没有硬件的用户从 Sim World 开始（L0），有硬件后切到 Real World（L1+）
```

---

## 1. LeRobot — "数据驱动的机器人学习工具箱"

**仓库**: [huggingface/lerobot](https://github.com/huggingface/lerobot) | v0.5.1 | Apache-2.0 | Python >=3.12

### 核心优势（做得很好的）

- **数据集格式**: Parquet + MP4，分块存储，Hub 原生推拉，delta timestamps 做时序关联。业界最好的机器人数据格式。
- **14 个策略算法**: ACT、Diffusion Policy、VQ-BeT、TDMPC、SAC/HIL-SERL、Pi0/Pi0Fast/Pi0.5、SmolVLA、GR00T N1.5、XVLA 等。每个策略统一 `configuration + modeling + processor` 三件套。
- **硬件抽象**: Robot / Teleoperator / Camera / MotorsBus 四层，接新机器人只需实现 ~6 个抽象方法。工厂 + 注册表模式（draccus.ChoiceRegistry）。
- **11 种机器人**: SO100/SO101、Koch、LeKiwi、Hope JR、Reachy2、Unitree G1、EarthRover 等。
- **13 种遥操作设备**: Leader arm、手柄、键盘、手机、Homunculus 等。
- **训练管线**: 完整 train → eval → checkpoint，支持分布式（Accelerate），step-based 训练循环。
- **gRPC 异步推理**: 大模型跑远程 GPU，机器人本地控制，延迟追踪。
- **18 个 CLI 命令**: 覆盖硬件发现、校准、遥操作、录制、训练、评估全流程。
- **Hub 生态**: 数据集和模型一键推拉 Hugging Face Hub，支持流式加载大数据集。
- **视频编码**: libsvtav1/h264/hevc，硬件加速，流式/批量编码。
- **在线 RL**: HIL-SERL 分布式 learner-actor 架构。
- **处理器管线**: 可组合的 normalize → tokenize → batch → device 链。

### 做得不好 / 没有的

- **没有自然语言接口** — 全部 CLI + 配置文件驱动，用户必须理解命令行参数和 draccus 配置层级。
- **仿真很浅** — 只有 4 个第三方 wrapper（ALOHA/PushT/LIBERO/MetaWorld），没有内置仿真器、没有 URDF/MJCF 加载、没有 Web 查看器。
- **没有 sim-to-real 流水线** — 没有域随机化、没有迁移工具。仿真和真机是完全独立的工作流。
- **没有自动硬件发现/配置** — 用户必须手动指定串口、电机 ID、相机索引。
- **配置复杂** — draccus 嵌套 dataclass 层级深，错误信息不友好。
- **没有安全框架** — 只有 `max_relative_target` 位置限制，没有碰撞检测、力矩限制、急停。
- **没有技能组合** — 每次录制是单一任务，不能链式组合行为。
- **没有 Web 可视化** — 用 Rerun，需要本地桌面。
- **没有 ROS2 集成** — 机器人类型几乎全是串口总线伺服臂。
- **没有多机器人协调**。
- **没有部署/边缘推理流水线**（量化/ONNX/模型优化）。
- **数据管线只支持录制** — 不能导入 ROS bag、CSV 等外部格式。

### 源码结构

```
src/lerobot/
├── async_inference/     # gRPC 远程策略推理
├── cameras/             # OpenCV, RealSense, ZMQ, Reachy2
├── configs/             # 训练/评估/管线配置
├── data_processing/     # SARM 标注处理
├── datasets/            # LeRobotDataset (Parquet+MP4), Hub 集成
├── envs/                # 仿真环境 wrapper (LIBERO, MetaWorld)
├── model/               # 运动学
├── motors/              # Dynamixel, Feetech, Damiao, Robstride
├── optim/               # 优化器/学习率调度器工厂
├── policies/            # 14 个策略实现
├── processor/           # 预处理/后处理管线
├── rl/                  # 在线 RL (HIL-SERL)
├── robots/              # 11 种机器人
├── scripts/             # 18 个 CLI 入口
├── teleoperators/       # 13 种遥操作设备
├── templates/           # 模型卡模板
├── transport/           # gRPC protobuf
├── utils/               # 17 个工具模块
└── types.py             # 核心类型定义
```

---

## 2. dimos — "物理空间的 Agent 操作系统"

**仓库**: [dimensionalOS/dimos](https://github.com/dimensionalOS/dimos) | Pre-release | Python 3.12+

### 核心优势（做得很好的）

- **Module/Blueprint 架构**: 类型化流（`In[T]`/`Out[T]`）+ `autoconnect()` 自动连线，forkserver 进程隔离。Blueprint 不可变，每次修改返回新实例。
- **LLM Agent 集成**: LangChain/LangGraph，`@skill` 装饰器自动转 `StructuredTool`，MCP Server 支持。GPT-4o/Claude/Ollama 多后端。
- **空间记忆**: CLIP 嵌入 + ChromaDB 向量库 + 机器人位姿 = 时空语义 RAG。"厨房在哪？"这种查询能工作。
- **感知栈全面**: YOLO 2D/3D 检测、人员 ReID、目标追踪、VLM 视觉问答、空间注册。
- **导航**: A* 重规划、前沿探索（wavefront）、视觉伺服、ROS nav bridge。
- **操控栈**: 抓取生成（GraspGen）、运动规划（Drake）、轨迹生成、拾放流程。
- **控制协调器**: 单 tick 循环（read → compute → arbitrate → route → write），关节级优先级仲裁。
- **多传输后端**: LCM、共享内存、ROS2 bridge、DDS。
- **回放模式**: 不需要硬件/仿真器就能开发测试。
- **硬件广度**: 四足（Go2/B1）、人形（G1）、臂（xArm7/Piper）、无人机。

### 做得不好 / 没有的

- **完全没有学习管线** — 没有 episode 录制、没有数据集格式、没有 ACT/Diffusion 训练、没有 checkpoint 管理。所有行为都是手写 skill。
- **没有低成本臂支持** — 只有 xArm7（商用价格）和 Piper，没有 SO100/SO101/Koch 等教育/爱好者平台。
- **依赖极重** — LangChain/Ultralytics/MuJoCo/OpenAI/Whisper/ChromaDB/sentence-transformers/rerun-sdk... 版本冲突维护负担大。
- **LangChain 强耦合** — 锁定特定版本（如 `langchain==1.2.3`），LangChain API 频繁变动带来脆弱性。
- **Unitree 中心** — 技能、system prompt、Blueprint 主要围绕 Go2 设计。接新机器人仍需大量代码。
- **ROS2 不是原生** — 自建 LCM 传输，ROS2 只是可选桥接。重复造了 ROS2 提供的消息类型/pub-sub/TF。
- **传输层自己承认需重写** — 代码注释中明确说"需要重写和简化"。
- **仿真实际只有 MuJoCo** — Genesis/Isaac 目录是占位符。没有 sim-to-real 迁移。
- **没有多机器人协调**。
- **没有实时安全保证** — Python 线程 + sleep 定时，安全约束只在 system prompt 层面。
- **操控规划依赖 Drake** — 重依赖，平台兼容问题。

### 源码结构

```
dimos/
├── core/           # Module 基类, Blueprint 引擎, 流系统, 传输层, 守护进程
├── agents/         # LLM Agent (LangChain/LangGraph), VLM, Ollama, MCP
├── robot/          # 硬件适配: unitree/ (Go2, G1, B1), drone/, manipulators/ (xArm, Piper)
├── control/        # ControlCoordinator — tick 循环, 任务类型
├── perception/     # 2D/3D 检测 (YOLO), 人员追踪, ReID, 空间感知
├── navigation/     # A* 规划, 前沿探索, 视觉伺服, ROS nav bridge
├── mapping/        # 代价图, 占据栅格, 体素, 点云, Google Maps/OSM
├── memory/         # CLIP 嵌入, ChromaDB 向量库, 时序记忆
├── manipulation/   # 操控模块, 拾放, 抓取 (GraspGen), 运动规划
├── simulation/     # MuJoCo 引擎, Genesis/Isaac 占位符
├── skills/         # Skill 基类, Unitree/操控/导航/REST 技能
├── teleop/         # 键盘, 手机, Meta Quest 遥操作
├── stream/         # 视频 (RTSP, ROS), 音频, 帧处理器
├── msgs/           # ROS 兼容消息类型 (无 ROS 依赖)
├── models/         # ML 模型: 嵌入, Qwen, 分割, VLM
├── web/            # FastAPI/Flask, WebSocket 可视化
└── visualization/  # Rerun SDK, 自定义 viewer
```

---

## 3. 六版图对比矩阵

| 版图 | LeRobot | dimos | RoboRSI 现状 |
|------|---------|-------|--------------|
| **本体接入** | ✅ 强（11 机器人，干净抽象） | ✅ 强（四足/人形/臂/无人机） | 🟡 SO101 已通 |
| **控制操作** | 🟡 CLI 控制，无对话 | ✅ 自然语言 + @skill 系统 | 🟡 Agent + ROS2 |
| **感知视觉** | ❌ 基本没有 | ✅ 强（YOLO/ReID/VLM/空间记忆） | 🟡 基础相机 |
| **数据训练** | ✅ 业界最强（14 算法 + 完整管线） | ❌ 完全没有 | 🟡 ACT 骨架 |
| **部署监督** | 🟡 gRPC 推理 | 🟡 MCP，无监督 | ❌ 未开始 |
| **生态扩展** | ✅ Hub 生态 | 🟡 Blueprint 可组合 | 🟡 Onboarding |

---

## 4. RoboRSI 定位策略

### 核心思路

**不重新造轮子，站在巨人肩上做集成层 + 自然语言胶水层。**

LeRobot 和 dimos 各做了一半：LeRobot 有最好的数据+训练管线但没有自然语言入口，dimos 有最好的 Agent+感知架构但没有学习能力。RoboRSI 要做的是**把两者最好的部分通过自然语言统一起来**。

### 按版图借力

| 版图 | 借谁的 | RoboRSI 自己做什么 |
|------|--------|-------------------|
| **本体接入** | LeRobot 的硬件抽象（Motor/Camera/Robot） | 自然语言引导接入 + 自动发现（Onboarding 已有） |
| **控制操作** | dimos 的 Skill → Tool 模式（参考设计） | Agent 调度 + 框架级安全约束（已有 Agent） |
| **感知视觉** | 直接用 Ultralytics/CLIP 等独立库 | 统一感知接口 + 与 Agent 对话结合 |
| **数据训练** | LeRobot 的数据集格式 + 策略实现 | 对话式采集/标注 + 对话选算法/调参 |
| **部署监督** | LeRobot 的 gRPC 推理架构 | 对话式部署 + 人在环监督 |
| **生态扩展** | LeRobot 的 Hub 生态 | 对话生成 adapter + ClaWHub 社区 |

### RoboRSI 的真正差异化

1. **自然语言是唯一入口** — LeRobot 需要 CLI 命令，dimos 需要理解 Blueprint 配置，RoboRSI 全程对话。
2. **零代码本体接入** — 对话描述硬件 → 自动生成 adapter（Onboarding 已有骨架）。
3. **对话驱动的完整闭环** — 采集 → 训练 → 部署 → 监督，全部通过对话完成。

---

## 5. 架构启示

### 从 LeRobot 学习

- **数据集格式**: 采用兼容的 Parquet + MP4 格式，实现与 LeRobot Hub 生态的数据互通。
- **策略三件套**: `configuration + modeling + processor` 模式，算法可插拔。
- **处理器管线**: 可组合的预处理/后处理链。
- **工厂 + 注册表**: draccus.ChoiceRegistry 模式，字符串名实例化。

### 从 dimos 学习

- **Skill → Tool 自动转换**: `@skill` 装饰器让模块方法自动暴露为 LLM 工具。
- **空间记忆**: CLIP + 向量库 + 位姿的时空 RAG 架构。
- **控制协调器**: 单 tick 循环 + 关节级优先级仲裁。
- **Blueprint 组合**: 声明式模块组合 + 自动连线。

