# RoboRSI 模型 API 接入：结构重构与原版代码对照

更新日期：2026-10-22。原始基线：`580876f747dea67d6bc5b3cf0569f46d0d5a6c43`。

本轮已按“沿用原项目结构、最小侵入”的要求重构。**模型调用集中于原 `vlm_io.py`，消息处理和压缩集中于原 `messages.py`，移除独立 `provider_compat.py` 及新增脚本库。** Engineer 等直接 API 路径可选 DeepSeek/Kimi，原 GPT/Claude API 分支和主流程 Codex CLI 继续保留。

**压缩边界已进一步统一：GPT、Azure、Claude、DeepSeek、Kimi 等在原执行循环中都使用“最近窗口保留完整工具组”的算法。** 不再按供应商选择压缩边界；超过 30 条消息触发、以最近 12 条为基础窗口、旧工具记录摘要到 `user.content` 的规则继续沿用。详见第 6.5 节。

**模型路由也已统一：**原 `vlm_io.py` 用一张前缀表和 `_model_route()` 选择请求实现；工具入口按同一个 `route` 分派，纯文本入口直接复用它，图像入口复用前缀表。此前“先判断新供应商，再执行旧判断”的结构已替换，具体代码见[第 6.1 节](#model-routing)。

**执行循环按最新选择保留宽松容错：**`rollout.py` 对所有供应商恢复原版 JSON 解码，不额外校验调用参数是否符合 schema；只统一补齐 `done` 后剩余调用的未执行结果。此前该处“新模型严格校验”的实现已撤回，详见[第 6.6 节](#rollout-tool-policy)。

**图片交付已统一：**GPT、Azure、Claude、DeepSeek、Kimi 共用“每次工具返回时捕获图片数据，整轮结果齐全后追加一条 user 观察消息”的流程。本轮待发送图片只取最新观察阶段，普通视角各留最后一张；裁剪、标注、拼图和预览保留独立语义。已经进入历史的图片不额外删除，继续沿用原压缩逻辑；没有增加自动 `look` 或补拍。详见[第 6.7 节](#observation-delivery)。

**2026-10-22 按用户决定进一步收敛：**所有执行入口恢复各自原版宽松参数解析；OpenETA 只保存和执行首个调用；感知模型显式配置优先，否则跟随当前执行模型，不写环境变量；内部收尾调用统一传工具定义，是否发送由适配层决定。具体代码与例子见第 6.8、6.9、6.12 节及 [本轮实施记录](../../RoboRSI_Remaining_Unification_Review.md)。后续维护遵循 [S2AC/AGENTS.md](../../AGENTS.md)。

**同日风险修正已实施：**按用户后续要求，原实现中有证据的风险对所有模型统一修复：进度真假值和评分归一化、所有异常退出时释放采样钩子、gate 有效测量、真实图片编码、Reviewer 证据索引。保持宽松 JSON 参数入口及原模块结构。具体代码与例子见 [统一修正记录第 8 节](../../RoboRSI_Remaining_Unification_Review.md#risk-fixes)。

阅读顺序：第 1 节是配置与运行，第 3 节解释重构后的职责，第 5 节记录验证，[第 6 节逐项对照原始代码](#code-changes)，第 7 节给出自己查看完整 diff 的命令。

**已经完成 LIBERO reset、准备开始使用新模型：直接按 [环境与复现简明指南第 4～9 步](../../RoboRSI_NoAPI_Quickstart.md#continue-with-models) 执行。** 该指南接续已有环境，包含 PyRoKi、视觉权重、CLI/API 检查、提交要求、单任务、正式基线和 evolve；以下配置说明用于查阅。

已完成代码重构与离线回归；尚未完成付费 API、真实仿真和完整技能更新验收。本轮没有提交或推送 Git 改动。

## 1. 最先需要知道的使用方式

### 1.1 支持哪些模型

| 配置中的模型名 | 用途与限制 | 凭据 |
|---|---|---|
| `deepseek/deepseek-flash` | 当前 Flash 配置，支持工具与图片，可用于 Engineer / 感知 | `DEEPSEEK_API_KEY` |
| `moonshot/kimi-k3` | K3 配置，支持工具与图片，始终思考 | `MOONSHOT_API_KEY` |
| `kimi/kimi-k3` | 上一项的别名，仍使用 Moonshot 端点和密钥 | `MOONSHOT_API_KEY` |
| `deepseek/deepseek-v4-pro` | 仅文本用途；不能作为会接收相机图片的 Engineer / Reviewer | `DEEPSEEK_API_KEY` |
| 原 `gpt-*`、`openai/*`、`azure/*` 等 | 保留原 OpenAI / Azure 实现与配置 | 原有凭据 |
| 原 `claude-*`、`anthropic/*` | 保留原 Anthropic 实现与配置 | 原有凭据 |

这是按具体模型建立的配置，不是给所有 DeepSeek / Kimi 型号统一套一个协议。未知的新供应商型号会明确报错。K2.x、旧 R1 等型号未在这一版中增加配置。能力依据：[DeepSeek 模型表](https://api-docs.deepseek.com/quick_start/pricing/)、[Kimi K3 指南](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)。

### 1.2 现有环境只需确认依赖和导入路径

此前如果已经通过 `pip install -e` 安装了当前 RoboRSI，Python 会直接读取这里修改后的源码，无需重装 Torch、LIBERO 或重新下载资产。

```bash
cd /home/user/hyn/agentrl/S2AC/RoboRSI
source "$ROBORSI_REPRO_ROOT/venv/bin/activate"
python -m pip install 'jsonschema>=4.20.0,<5.0.0'
python -c 'from roborsi.embodied.agent_loop import vlm_io; print(vlm_io.__file__); print(callable(vlm_io.resolve_api_config))'
```

最后一行命令应输出当前 `S2AC/RoboRSI/roborsi/embodied/agent_loop/vlm_io.py` 路径及 `True`。`jsonschema` 仅用于新适配层的工具定义 schema 检查，已加入 `pyproject.toml`；不再用于检查模型返回的调用参数。Reviewer、OpenETA、外层会话与 rollout 均沿用各自原版宽松解析。

### 1.3 公共配置：主流程继续使用 Codex CLI

在运行 RoboRSI 的同一个终端中设置：

```bash
export ROBORSI_ROLE_SESSION=1
export ROBORSI_ROLE_BACKEND=codex
export ROBORSI_PLANNER_MODEL=gpt-5.6-sol
export ROBORSI_REVIEWER_MODEL=gpt-5.6-sol
export ROBORSI_MANAGER_MODEL=gpt-5.6-sol
export ROBORSI_API_REASONING_EFFORT=high
```

前三个角色使用你原先的 CLI 登录和会话；这里的 `gpt-5.6-sol` 是仓库原有配置示例，应替换为你的 Codex 账号实际可用并验证过的模型 ID。最后一个变量只控制新增 API 适配层，不会把 CLI 角色改成 API，也不改变旧 GPT / Claude API 参数。

**选择 DeepSeek：**

```bash
read -rsp 'DeepSeek API key: ' DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY
printf '\n'
export ROBORSI_ENGINEER_MODEL=deepseek/deepseek-flash
unset ROBORSI_PERCEPTION_MODEL  # 使用当前 Engineer；希望独立指定时再显式 export
export ROBORSI_GATE_MODEL=deepseek/deepseek-flash
```

**或者选择 Kimi：**

```bash
read -rsp 'Moonshot API key: ' MOONSHOT_API_KEY
export MOONSHOT_API_KEY
printf '\n'
export ROBORSI_ENGINEER_MODEL=moonshot/kimi-k3
unset ROBORSI_PERCEPTION_MODEL  # 使用当前 Engineer；希望独立指定时再显式 export
export ROBORSI_GATE_MODEL=moonshot/kimi-k3
```

`read -rsp` 从终端读密钥，输入时不显示字符。两组配置选择一组即可，不必同时拥有两家额度，也不需要为这条混合路径提供 OpenAI API Key。

默认端点分别为 `https://api.deepseek.com` 和 `https://api.moonshot.ai/v1`；使用其他官方地区端点或自建兼容网关时，分别配置 `DEEPSEEK_BASE_URL`、`MOONSHOT_BASE_URL`，并确认密钥属于该端点。不要用 `OPENAI_BASE_URL` 覆盖新供应商端点；新增分支不读取该变量。

这些 `export` 只对当前终端及其子进程有效。新终端仍需重新加载自己的配置。本文没有修改你的 shell 启动文件。

### 1.4 不消耗额度的配置检查

```bash
python - <<'PY'
import json
import os
from roborsi.embodied.agent_loop.vlm_io import resolve_api_config

for name in ("ROBORSI_ENGINEER_MODEL", "ROBORSI_PERCEPTION_MODEL", "ROBORSI_GATE_MODEL"):
    model = os.environ.get(name) or (os.environ["ROBORSI_ENGINEER_MODEL"]
                                     if name == "ROBORSI_PERCEPTION_MODEL" else None)
    config = resolve_api_config(model)
    print(name, json.dumps(config.public(), ensure_ascii=False))
PY
```

这仅解析配置并检查密钥是否存在，不发送请求，也不显示密钥。它不能证明密钥有效或模型有额度。

### 1.5 接下来怎样运行单任务

**正式 `eval-suite` 需要先检查并提交本次修改。** 这是 RoboRSI 原有的干净工作区要求；技能更新 gate 还会从已提交的 `HEAD` 新建临时 worktree，因此也需要包含适配代码的提交。此次实现没有自动替你提交。

继续使用此前配置好的 LIBERO 资产、EGL 和 PyRoKi 服务。满足原运行前置条件后，可先做一个小任务：

```bash
ROBORSI_EPISODE_MAX_ROUNDS=1 roborsi eval-suite \
  --backend libero-pro \
  --atomic libero_pick_place \
  --task libero_object_task/0 \
  --pass-at 1 --seed-start 0 --workers 1 --infra-retries 0 \
  --tool-budget 20 --run-mode frozen \
  --planner-model "$ROBORSI_PLANNER_MODEL" \
  --engineer-model "$ROBORSI_ENGINEER_MODEL" \
  --reviewer-model "$ROBORSI_REVIEWER_MODEL" \
  --out "$ROBORSI_REPRO_ROOT/evals/model-api-smoke-001"
```

这一步会调用真实 API 并使用额度。首次检查限制为一轮角色流程，工具预算 20，不自动重跑基础设施错误，不能和官方 80 预算、默认最多六轮的结果直接比较。完整环境准备、独立状态目录和审计命令见前面的简明指南。该命令尚未在本次修改过程中执行。

已有的 `scripts/reproduce_libero_pro.sh`、`scripts/reproduce_libero_plus.sh` 和 `scripts/run_libero_pro_matched_pass1.sh` 也支持上述分角色配置。优先级为：**各自的 `ROBORSI_*_MODEL` > `ROBORSI_EVAL_MODEL` 统一回退值**。不要只把 `ROBORSI_EVAL_MODEL` 改成 Kimi，同时仍让 Planner / Reviewer 使用 Codex；那会把 CLI 角色的模型名也设成 Kimi。

做正式模型对照时，分别使用独立输出目录和 `ROBORSI_HOME`，在各自状态目录完成原 LIBERO 配置，避免混用 CLI 历史与技能更新状态。

### 1.6 怎样切回 GPT / Claude

切回 GPT 时，恢复原 API 凭据，把 Engineer、gate 模型设回所需 GPT 型号；未显式设置感知模型时，它自动跟随本次 Engineer。如果此前显式设置过感知模型，希望恢复跟随行为，应在自己的终端执行 `unset ROBORSI_PERCEPTION_MODEL`。`ROBORSI_OPENAI_TRANSPORT` 仍控制原 OpenAI Responses / Chat Completions 分支。

切换 Engineer 为 Claude 的示例：

```bash
export ROBORSI_ENGINEER_MODEL=anthropic/claude-sonnet-4-6
export ROBORSI_PERCEPTION_MODEL=anthropic/claude-sonnet-4-6
export ROBORSI_GATE_MODEL=anthropic/claude-sonnet-4-6
export ROBORSI_VLM_PROVIDER=anthropic
```

仍需原 Anthropic 凭据。主流程三个 CLI 角色可以继续用前面的 Codex 配置。新供应商专用参数不会传入旧 GPT / Claude 分支。

## 2. 修改后的调用关系

```text
主流程
  Planner  ── 原 persistent_agent / Codex CLI ── plan.md
  Engineer ── run_rollout
                ├─ messages + tools
                ├─ vlm_io._call_vlm_tools
                │    ├─ GPT / Azure → 原 OpenAI transport
                │    ├─ Claude      → 原 Anthropic transport
                │    └─ DeepSeek / Kimi → vlm_io._chat_call_with_tools
                │                           └─ 指定供应商的 Chat Completions
                ├─ 原 Python 分派器执行工具，操作当前 env
                └─ 工具结果 + 新观测 → 下一轮请求
  Reviewer ── 原 Codex CLI ── 检查证据 / verdict / 技能改进提案
  Manager  ── 原 Codex CLI ── 审核、验证、按原流程处理技能更新

技能更新中的 episode gate
  ROBORSI_GATE_MODEL
    └─ 已提交 HEAD 的隔离 worktree
         └─ ROBORSI_ROLE_SESSION=0
              ├─ Planner API
              ├─ Engineer API
              └─ Reviewer API（可读取证据工具）
```

**“Reviewer 仍用 CLI”和“适配 API Reviewer”并不矛盾。** 主流程 Reviewer 默认使用 CLI；gate 原本就强制走 API。此次保留这个区别，给 gate 增加独立模型配置。

这里使用 OpenAI Python SDK 作为 Chat Completions 客户端，不表示请求发送给 OpenAI。实际请求目标由`vlm_io.py` 解析的供应商端点、模型名和独立密钥决定。

## 3. 本轮重构后的职责边界

这次按“沿用原模块、保持原接口、控制业务层改动”的要求调整第一版接入实现。`roborsi/` 与 `scripts/` 下没有新增运行时源文件；新增测试和本文分别留在原来的 `tests/`、`docs/` 目录。

| 职责 | 原 RoboRSI 所在位置 | 重构后的实现 |
|---|---|---|
| 模型路由、认证、HTTP、重试和统计 | `agent_loop/vlm_io.py` | 同一前缀表选择 OpenAI、Anthropic 或专用 Chat 请求实现 |
| 消息格式转换、图片和历史压缩 | `agent_loop/messages.py` | 同一文件保留原字段、校验消息组，所有供应商共用完整工具组边界 |
| 请求模型、执行工具、回填结果 | `agent_loop/rollout.py` | 原循环调用上述辅助函数，原分派器继续执行动作 |
| 评测运行信息、恢复配置 | `evaluation/suite.py` | 同一文件记录 API 配置指纹 |
| 环境准备和 benchmark 启动 | 原三个复现脚本 | 在原参数位置解析各角色模型和检查密钥 |

对上一版实现作了以下调整：

- 移除 `provider_compat.py`：模型配置和网络调用归回 `vlm_io.py`；消息处理归回 `messages.py`；实验指纹归回 `evaluation/suite.py`。
- 移除 `CompatibleMessage` 专用类。消息规范化返回原调用方可读取的 `.content/.tool_calls` 对象，附带 `request_message` 用于完整保存 assistant 字段。
- 移除跨模块的 `on_response`、`track_call` 注入。新网络分支直接调用同文件已有的 `_log_tokens()`、`_track_vlm_call()`。
- 模型前缀集中到 `_MODEL_ROUTES`，由 `_model_route()` 统一解析。`_call_vlm_tools()` 按路由分派，纯文本调用不再先区分新旧供应商，图像调用不再重复写一份 OpenAI 前缀。
- 消息辅助函数不再接收供应商 `APIConfig`。`vlm_io.py` 解析模型差异后，传入 `require_reasoning`、`vision`、允许的图片前缀等简单参数。
- rollout、API Reviewer、OpenETA、外层 API 会话全部沿用各自原版参数解码和工具分派；移除 `messages.tool_arguments()` 及其供应商专用严格校验。
- `rollout.py` 遇到 `done` 后，为所有供应商补齐剩余调用的 skipped 结果，保持原退出时机与动作执行顺序。
- 压缩边界统一到 `_summarize_old_trace()`：移除供应商条件和 `complete_groups` 开关，旧供应商也向前扩展最近窗口以保留完整工具组。
- `serialize_assistant_message()` 同时支持原字符串和带元数据的回复，CaP-X、CodeAct 无需自行判断两种保存方式。
- 移除 `scripts/lib/model_config.sh`。三个已有脚本各自包含一小段启动配置。这里接受少量重复，换取入口独立、无需新增脚本库的结构；没有引入新的启动框架。

网络响应结构、完整工具配对、推理字段、有限重试和 API 故障分类继续保留。各执行入口不再增加按供应商区分的参数 schema 门槛，工具自身的检查、转换和错误处理继续沿用原实现。职责仍在原模块，业务代码不依赖独立的供应商兼容模块。

## 4. 可调整的 API 参数

| 变量 | 默认 / 含义 |
|---|---|
| `ROBORSI_API_REASONING_EFFORT` | 未设置时继承 `ROBORSI_REASONING_EFFORT`，再回退 `high` |
| `ROBORSI_API_{TOOLS,TEXT,PERCEPTION,REVIEWER}_REASONING_EFFORT` | 指定用途的优先级高于全局值；大括号表示四个独立变量 |
| `ROBORSI_DEEPSEEK_THINKING` | `enabled`；可显式设 `disabled`，不适用于 K3 |
| `ROBORSI_API_MAX_OUTPUT_TOKENS` | 全用途覆盖值；未设置时 tools/text/reviewer 为 16384，perception 为 8192 |
| `ROBORSI_API_{TOOLS,TEXT,PERCEPTION,REVIEWER}_MAX_OUTPUT_TOKENS` | 指定用途覆盖全局输出预算 |
| `ROBORSI_API_TIMEOUT_S` | 120；单次 SDK 网络超时设置 |
| `ROBORSI_API_ATTEMPTS` | 3；包含第一次请求的最大次数 |
| `ROBORSI_API_DEADLINE_S` | 300；请求、退避及完整响应返回时检查的重试时间预算 |

DeepSeek 配置把 `minimal→low`、`medium/xhigh→high`、`ultra→max`，`none` 表示关闭思考。K3 仅接受 `low/high/max`，不静默映射或关闭。输出预算包括推理和最终输出，不再沿用感知旧路径的 512 / 2048 小预算。

`DEADLINE_S` 不是强制中止底层 socket 的硬墙钟定时器；底层 IO 由 SDK 超时控制，超过预算的完整响应也不会再交给机器人执行。请求体超限时明确报错，不额外删最近推理、不改变图片或工具预算。

## 5. 验证记录及其边界

2026-10-22 本轮统一修正明确风险后的完整离线回归：**322 passed（58.26 秒）**。新增覆盖真假值、非有限评分、JSON 字符串内括号、图片真实编码、各异常退出的钩子恢复、Reviewer 轮次与范围、gate 有效测量及原发布保护。此前统一原版行为阶段为 **229 passed（54.60 秒）**。覆盖新旧供应商的宽松执行、OpenETA 首调用历史、感知配置优先级与线程继承、OpenAI Chat/Responses 收尾请求、原图片/压缩流程和 CLI 角色。在此前统一原版处理阶段，恢复 Reviewer JPEG 读取、将指纹升级为 3 后，曾对受影响范围另做 **10 项针对性复核，全部通过（1.47 秒）**。此前图片筛选版本为 222 passed（54.41 秒）。以下命令可重跑当前这组 322 项回归。

此前图片交付统一后的完整回归：**187 passed（55.74 秒）**，其中接入测试为 122 项，另有 65 项原有回归用例。本轮扩展了其中的多供应商场景，并新增了视觉语义、状态标记和物理钩子的行为验证。

此前恢复宽松参数解析、统一补齐 done 历史后的运行结果：**155 passed（55.03 秒）**。其中接入测试文件包含 90 项参数化用例，另有 65 项原有回归用例。此次图片修复的验证结果见本节下方补充记录。

此前结构重构时为 125 项，统一压缩边界后为 131 项，统一模型路由后为 151 项；宽松参数与 done 配对调整时为 155 项。本次在这些回归上增加图片交付验证。

测试调用真实 Python SDK 构造请求，通过 `httpx.MockTransport` 检查 HTTP JSON。没有调用付费模型，也没有启动机器人仿真。LiteLLM 使用本地模型价格表，避免测试期间尝试更新远端价格元数据。

```bash
LITELLM_LOCAL_MODEL_COST_MAP=True \
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/tmp/roborsi-test-deps \
ROBORSI_HOME=/tmp/roborsi-refactor-tests TMPDIR=/tmp \
  "$ROBORSI_REPRO_ROOT/venv/bin/python" -m pytest -q -p no:cacheprovider \
  tests/test_model_api_adaptation.py \
  tests/test_openai_responses_transport.py \
  tests/test_agent_baselines.py \
  tests/test_evaluation_suite.py \
  tests/test_provider_retry.py \
  tests/test_azure_openai_provider.py \
  tests/test_provider_factory.py \
  tests/test_libero_perception_fallback.py \
  tests/agents/test_libero_runtime_routing.py \
  tests/test_evaluation_audit.py \
  tests/test_release_hardening.py
```

`PYTHONPATH=/tmp/roborsi-test-deps` 是本机临时测试依赖目录，不是生产运行必需配置。相关 Python 文件通过 Python 3.12 语法检查，修改代码通过未定义名称检查，测试通过 Ruff，三个脚本通过 `bash -n`，`git diff --check` 通过；另用 AST 核对了 OpenAI Chat、Responses、Anthropic 的三个原核心请求函数，均与原始提交一致；另对 rollout 做 AST 比对，确认原循环主体除了把局部 ProviderError 清理移入 finally 外保持一致。测试所用版本沿用上一轮：Python 3.12.5、OpenAI SDK 2.54.0、httpx 0.28.1、jsonschema 4.26.0、LiteLLM 1.105.0、pytest 9.1.1、Anthropic SDK 1.14.0。没有重装已有复现虚拟环境。

另外检查了全部修改 Python 文件的语法、三个脚本各自的 `bash -n`、未定义名称、测试文件 Ruff、`git diff --check` 和文档本地链接。运行代码、脚本、测试已不存在对移除模块的引用。

对原始提交的静态核对还确认：

- `roles/planner.py`、`roles/reviewer.py`、`sessions/persistent_agent.py` 文件内容与基线相同。
- `_dispatch_with_timeout()` 与基线相同。图片修复在 `_dispatch()` 的原 `handler(state, args)` 返回后捕获图片，并按需统计物理步进；工具选择、参数传递和控制实现继续沿用原代码。
- `run_rollout()` 内的参数解码 `try/except` 已恢复为原版，其 AST 与基线相同。
- OpenAI 客户端、Chat/Responses 核心调用、Responses 转换，以及 Anthropic 客户端、原生工具调用和原重试函数的 AST 与基线相同。
- 原 `_initial_messages()`、Anthropic 消息转换和配对清理函数的 AST 与基线相同。

AST 核对只说明这些函数的程序结构相同；配合回归测试可以降低旧路径受影响的风险，不能证明所有真实外部服务已经验收。

2026-10-21 图片统一修复另增加了：五种模型前缀的两个视角、同路径刷新、同轮覆盖和未刷新不重复附图；done/预算耗尽时保留最后观察；文件时间戳相同时识别内容变化；图片与完整工具组一起压缩；OpenETA 连续观察、CodeAct 多图及裁剪缓存；图片策略变化阻止混用旧实验指纹。执行循环测试使用真实 `look()`、`_dispatch()` 和 `run_rollout()`，只替换模型回复与环境观测，不调用付费 API 或 MuJoCo。

本轮进一步验证：同阶段同视角只留最后一张；动作前后不混入同一条新增观察；相机默认值和别名按实际返回值统一；已发送的旧图片消息保持原样；真实 `zoom_in`、网格标注、orbit 拼图和点标注的输出仍可用；旧图裁剪继承旧状态；显式观察即使像素/时间戳相同也更新状态；复合工具中的真实步进、已移动后失败与未移动的拒绝能够区分；既有物理采样钩子复用，临时钩子在正常返回和异常时恢复。物理环境使用替身，未启动真实 RoboTwin/LIBERO 仿真。

尚需实际完成：两家 API 的文字/图片/工具多轮请求、一个 LIBERO frozen 任务、一次完整技能更新及 gate，然后再运行完整 benchmark。更换模型可能改变成功率，本文不将协议兼容等同于复现原论文成绩。

<a id="code-changes"></a>

## 6. 与最初未修改的 RoboRSI 逐项对照

本节“修改前”统一指提交 `580876f747dea67d6bc5b3cf0569f46d0d5a6c43`；“修改后”指本轮结构重构后的工作区。上一版独立兼容模块只用于说明重构过程，不充当原始基线。

代码块是关键节选，省略号或注释表示未展示的原逻辑，不能整块当作可运行程序。完整差异可以用第 7 节的只读命令查看。

相对原始提交，修改 **18 个原有代码/配置/脚本文件**，另更新 1 个原测试文件。下表为代码/配置/脚本的 `git diff --numstat`，不包含测试和文档。

| 原有文件 | 新增 / 删除行 | 修改用途 |
|---|---:|---|
| [pyproject.toml](../pyproject.toml) | +1 / −0 | 增加工具定义 schema 检查依赖 |
| [roborsi/agents/baselines.py](../roborsi/agents/baselines.py) | +17 / −14 | 基线消息保存、统一观察队列和执行范围内的感知继承；首调用策略沿用原版 |
| [roborsi/agents/evolution/episode_gate.py](../roborsi/agents/evolution/episode_gate.py) | +35 / −6 | 独立 gate 模型、已提交入口检查、统一有效测量及种子核验 |
| [roborsi/agents/long_horizon/lh_executor.py](../roborsi/agents/long_horizon/lh_executor.py) | +8 / −0 | API 故障单独记录并上抛 |
| [roborsi/agents/roles/engineer.py](../roborsi/agents/roles/engineer.py) | +4 / −12 | 感知继承和演化模型配置检查 |
| [roborsi/agents/tools/reviewer.py](../roborsi/agents/tools/reviewer.py) | +20 / −16 | API Reviewer 收尾协议、真实图片编码与证据索引修复 |
| [roborsi/channels/core/agent.py](../roborsi/channels/core/agent.py) | +36 / −25 | 可选 API 聊天路径的消息与工具衔接 |
| [roborsi/embodied/agent_loop/messages.py](../roborsi/embodied/agent_loop/messages.py) | +403 / −33 | 原字段保存、完整组压缩、图片快照与本轮状态/视角筛选、视觉用途和来源记录 |
| [roborsi/embodied/agent_loop/rollout.py](../roborsi/embodied/agent_loop/rollout.py) | +283 / −248 | 保留原参数解码、图片观察和 done 历史；统一所有异常退出时的采样清理 |
| [roborsi/embodied/agent_loop/vlm_io.py](../roborsi/embodied/agent_loop/vlm_io.py) | +493 / −56 | 原 I/O 文件统一前缀路由，增加供应商配置、请求和错误处理 |
| [roborsi/embodied/sim/robotwin/codeact_runtime.py](../roborsi/embodied/sim/robotwin/codeact_runtime.py) | +21 / −26 | CodeAct 路由、历史、观察队列、物理状态计数和异常衔接 |
| [roborsi/embodied/skills/_lib/judging/progress_score/policy.py](../roborsi/embodied/skills/_lib/judging/progress_score/policy.py) | +44 / −40 | 可选评分模型与请求适配；统一真假值、有效评分和真实图片编码 |
| [roborsi/embodied/skills/base/_lib/libero/_perception.py](../roborsi/embodied/skills/base/_lib/libero/_perception.py) | +6 / −0 | 感知用途标记和 API 错误上抛 |
| [roborsi/evaluation/atomic.py](../roborsi/evaluation/atomic.py) | +3 / −0 | 基础设施与配置/协议故障分类 |
| [roborsi/evaluation/suite.py](../roborsi/evaluation/suite.py) | +74 / −0 | 在原评测模块记录和比较 API 配置、观察及执行策略 |
| [scripts/reproduce_libero_plus.sh](../scripts/reproduce_libero_plus.sh) | +44 / −8 | 原入口的分角色模型与密钥检查 |
| [scripts/reproduce_libero_pro.sh](../scripts/reproduce_libero_pro.sh) | +40 / −4 | 原入口的分角色模型与密钥检查 |
| [scripts/run_libero_pro_matched_pass1.sh](../scripts/run_libero_pro_matched_pass1.sh) | +41 / −5 | 分角色启动实际 eval-suite |

改动覆盖多个入口，是因为原项目在 Engineer、基线、CodeAct、Reviewer 等处分别保存历史和调用模型；这些位置仍需要少量衔接。核心协议实现集中在两个原文件。


<a id="model-routing"></a>

### 6.1 `vlm_io.py`：统一前缀识别，再分派到对应请求实现

原 `_call_vlm_tools()` 的分支：

```python
if model.startswith(("openai/", "azure/", "azure-openai/", "gpt-", "o3", "o4")):
    return _retry_litellm(
        lambda: _openai_call_with_tools(model, messages, tools)
    )
return _retry_litellm(lambda: _anthropic_call_with_tools(
    model, messages, tools, thinking_budget=thinking_budget,
    tool_choice_override=tool_choice, prefill=prefill))
```

初版接入在原判断之前增加 `if is_compatible_model(model)`。这能保持旧路径，但供应商识别分散在工具入口、纯文本入口和图像入口，不利于继续维护。现已将前缀集中到原文件中的同一张表：

```python
_MODEL_ROUTES = (
    (("openai/", "azure/", "azure-openai/", "gpt-", "o3", "o4"), "openai"),
    (("anthropic/", "claude-"), "anthropic"),
    (("deepseek/", "moonshot/", "kimi/"), "chat"),
)

def _model_route(model: str | None) -> str:
    for prefixes, route in _MODEL_ROUTES:
        if model and model.startswith(prefixes):
            return route
    return "anthropic"
```

`_call_vlm_tools()` 先确定一个路由，再在同一层分派：

```python
route = _model_route(model)
match route:
    case "openai":
        effective_tools = [] if tool_choice == "none" else tools
        return _retry_litellm(
            lambda: _openai_call_with_tools(model, messages, effective_tools)
        )
    case "anthropic":
        return _retry_litellm(lambda: _anthropic_call_with_tools(
            model, messages, tools, thinking_budget=thinking_budget,
            tool_choice_override=tool_choice, prefill=prefill))
    case "chat":
        return _chat_call_with_tools(
            model, messages, tools, tool_choice=tool_choice,
            purpose=request_kind, prefill=prefill,
        )
raise ValueError(f"Unknown model route: {route}")
```

| 路由 | 模型名示例 | 具体请求实现 |
|---|---|---|
| `openai` | `gpt-5.4`、`openai/gpt-5.4`、`azure/部署名` | 原 OpenAI / Azure 客户端，继续按配置选择 Chat Completions 或 Responses |
| `anthropic` | `anthropic/claude-sonnet-4-6`、`claude-sonnet-4-6` | 原 Anthropic Messages、消息转换和流式工具调用 |
| `chat` | `deepseek/deepseek-flash`、`moonshot/kimi-k3`、`kimi/kimi-k3` | 专用 Chat 请求实现，内部再解析供应商密钥、端点、型号能力和参数 |

`chat` 是本地请求实现的名称，不是一家服务商，也不表示 OpenAI 不使用 Chat Completions。前缀决定走哪条实现；OpenAI/Azure 的实际地址仍由原配置解析，DeepSeek/Moonshot 则使用各自的配置。

**为什么保留三个请求实现：**代码中的 OpenAI Responses 输入格式、Claude `tool_use/tool_result` 格式，以及新供应商的推理字段、输出预算和重试逻辑存在差异。统一前缀识别可以消除入口中的重复判断；这些请求差异仍由各自函数处理。原 OpenAI、Anthropic 核心请求函数没有因这次路由重构而重写。

其他入口的同步调整：

- `_call_vlm_no_tools()` 统一执行 `_call_vlm_tools(model, messages, tools=[], request_kind=request_kind)`，不再判断新旧供应商。
- `_call_vlm_image_impl()` 按最终选定的感知模型调用 `_model_route()`；`ROBORSI_VLM_PROVIDER` 不再覆盖感知模型的路由，避免明确指定 Claude 却被旧变量转到 OpenAI。CodeAct 原文本入口的旧 provider 行为未顺带修改。
- `is_compatible_model()` 为现有调用方保留，但内部只是 `_model_route(model) == "chat"`，不再维护单独的前缀集合。其他模块仍可据此启用相应的字段和能力检查。
- Chat 路径不支持 `prefill` 的检查移到 `_chat_call_with_tools()` 内，仍在网络请求之前报配置错误。入口只负责分派。

为兼容原有自定义模型名，**未匹配前缀仍回退 Anthropic**，这不表示任意未知模型都可用。已匹配 `deepseek/`、`moonshot/`、`kimi/` 的未知型号仍在能力表校验时明确报错。需要指定服务时，应使用明确的供应商前缀。

`request_kind` 是新增可选参数，默认 `tools`，用于区分输出预算。原两条供应商分支继续使用原参数与重试函数；CLI 角色的后端选择不在这里改变。

### 6.2 `vlm_io.py`：客户端、参数差异和重试仍在模型 I/O 层

新增的 `_CHAT_MODELS` 是本文件内部的小型能力表，记录具体型号的视觉支持、输出字段及容量限制。`_ChatModel`、`_ChatConfig` 只是内部数据记录，没有注册器、插件接口或新的 Agent 框架。

`resolve_api_config()` 读取供应商自己的密钥、端点和用途预算：

```python
prefix = "DEEPSEEK" if profile.provider == "deepseek" else "MOONSHOT"
key = os.environ.get(f"{prefix}_API_KEY", "")
default_url = (
    "https://api.deepseek.com" if prefix == "DEEPSEEK" else "https://api.moonshot.ai/v1"
)
base_url = os.environ.get(f"{prefix}_BASE_URL", default_url).rstrip("/")
```

`_chat_request()` 先让消息层检查通用结构，再构造供应商请求：

```python
prepared = prepare_chat_messages(
    messages, require_reasoning=config.thinking and (
        bool(tools) or config.profile.provider == "moonshot"),
    vision=config.profile.vision,
    image_prefixes=("data:image/", "ms://") if config.profile.provider == "moonshot" else None,
)
prepared_tools = prepare_chat_tools(tools) if tools else []
```

```python
kwargs = {
    "model": config.profile.model_id,
    "messages": prepared,
    config.profile.output_field: config.max_output_tokens,
    "stream": False,
}
if config.profile.provider == "deepseek":
    kwargs["extra_body"] = {"thinking": {"type": "enabled" if config.thinking else "disabled"}}
if config.thinking:
    kwargs["reasoning_effort"] = config.reasoning_effort
if tools:
    kwargs.update(tools=prepared_tools, tool_choice=choice)
```

`extra_body` 是 SDK 参数；HTTP JSON 中的 `thinking` 在顶层。其他供应商的 `temperature`、预算字段或 `thinking_budget` 不会原样套到这条分支。

**为什么修改：**两家服务虽然接受 Chat Completions 形式，请求参数和能力仍有差异。这些差异集中在 `vlm_io.py`，调用机器人工具的代码无需知道端点和密钥。

新增网络调用直接复用本文件已有统计函数：

```python
client = OpenAI(
    api_key=config.api_key, base_url=config.base_url, max_retries=0, timeout=config.timeout_s
)
# 下列代码位于有限重试循环中。
with _track_vlm_call():
    response = client.chat.completions.create(
        **kwargs, timeout=min(config.timeout_s, remaining)
    )
    _log_tokens(response)
```

保留一层有次数和时间限制的重试，并在结束时关闭客户端。模型请求重试不会执行机器人动作。未知型号、缺少密钥、不支持的图片输入或请求超限，会在发送前明确报错。

### 6.3 `messages.py`：统一处理回复，保持原执行接口

原 GPT Chat 回复本身提供 `.content/.tool_calls`，Anthropic 和 Responses 则在原代码中转换。新 Chat 分支也维持这套接口，规范化逻辑位于 `normalize_chat_response()`。

它只取出可回传的 assistant 字段：

```python
native = {
    key: copy.deepcopy(raw[key])
    for key in ("role", "content", "reasoning_content", "tool_calls")
    if key in raw
}
native["role"] = "assistant"
native.setdefault("content", None)
```

返回对象的主要字段：

```python
return SimpleNamespace(
    content=native["content"], tool_calls=calls or None,
    request_message=native, reasoning_content=native.get("reasoning_content"),
    finish_reason=finish, usage=getattr(response, "usage", None),
    model=getattr(response, "model", None),
)
```

**为什么修改：**工具执行需要原 `.function.name/.arguments` 接口；下一轮请求还需要原始推理和工具字段。`request_message` 连接这两个需求，但不会把响应级统计字段塞进对话历史。

回复截断、缺少调用 ID、重复调用 ID、禁止工具时仍返回工具等情况，在分派动作前被拒绝。消息层抛出 `MessageError`，`vlm_io.py` 转为带分类的 `ProviderError`，业务层处理模型故障时仍只有一个出口。

### 6.4 `messages.py`：保存 assistant 时保留最近推理

原 Engineer 的无工具回复保存：

```python
convo.append({"role": "assistant",
              "content": getattr(msg, "content", "") or " "})
```

修改为：

```python
convo.append(serialize_assistant_message(msg))
```

辅助函数的完整主体：

```python
def serialize_assistant_message(msg: Any, *, empty: str = " ",
                                legacy_content: str | None = None) -> dict[str, Any]:
    if hasattr(msg, "request_message"):
        return copy.deepcopy(msg.request_message)
    content = (legacy_content if legacy_content is not None
               else msg if isinstance(msg, str) else getattr(msg, "content", ""))
    return {"role": "assistant", "content": content or empty}
```

原 `_assistant_tool_calls_msg()` 对带 `request_message` 的回复保留原正文、推理字段，但 `tool_calls` 严格取调用者传入的子集；OpenETA 传 `[tc]` 时只保存该调用。后面的旧序列化保留。外层会话用 `serialize_assistant_message(reply, legacy_content=text, empty="")`，使旧模型仍保存原先拼接后的正文，新模型则保留 API 原字段。

**为什么修改：**原代码手动重建 `content/tool_calls` 会漏掉推理字段；有工具和无工具回复都需要正确保存。没有新增字段的原供应商继续按原方式保存。

`_call_vlm_no_tools()` 仍返回字符串接口；新分支使用 `TextReply(str)` 附带原 assistant 消息。CaP-X 提取代码、CodeAct 修复代码时都能继续把它当字符串，同时统一用上述函数保存历史。CodeAct 传 `empty=""`，保留其原空字符串语义。

<a id="unified-compaction"></a>

### 6.5 原压缩函数：所有供应商统一保留完整工具组

原 `_summarize_old_trace()` 已经执行以下提取：

```python
arg_str = ", ".join(f"{k}={str(v)[:40]}" for k, v in (args or {}).items())[:120]
tool_summary.append(f"  - {name}({arg_str})")
snippet = content[:150].replace("\n", " ")
tool_summary.append(f"    → {snippet}")
summary_msg = {"role": "user", "content": summary_text}
return head + [summary_msg] + recent
```

这些摘要规则继续使用。旧 assistant 普通正文和旧推理不参与摘要；原 system、首次 user 和最近窗口另行保留。摘要是字符串提取，不调用另一个 LLM。

**原始 RoboRSI 的边界：**先取最近 12 条；如果第一条是工具结果，就向后跳过结果消息，将对应工具组划入旧记录摘要。这样不会留下开头的孤立结果，但会少保留一些近期的原始调用与结果。

**现在的统一边界：**仍从最近 12 条开始，用 `tool_call_id` 找到对应的 assistant；切口落在工具组内时，向前移动到该 assistant，保留整个组。未完成组也从其 assistant 开始保留；如果已经退到首次任务之后、没有更早工具记录可压缩，就返回原历史。

这里的“组”是**一条 assistant 发出的全部 `tool_calls` 及其对应结果**，不是整个机器人任务。一个组可以包含多个工具调用，实际保留的消息数可以超过 12。

原 `messages.py` 内直接执行以下算法，已移除 `complete_groups` 可选参数：

```python
split = len(convo) - keep_recent
pending: dict[str, int] = {}
for index, message in enumerate(convo):
    if message.get("role") == "assistant":
        for call in message.get("tool_calls") or []:
            pending[call["id"]] = index
    elif message.get("role") == "tool":
        start = pending.pop(message.get("tool_call_id"), None)
        if start is not None and start < split <= index:
            split = start
if pending:
    split = min(split, *pending.values())
if split <= 2:
    return convo
```

执行循环不再区分供应商，恢复为同一个调用：

```python
if len(convo) > SUMMARIZE_AT_MSGS:  # 30 条消息，不是 30 次模型调用
    convo = _summarize_old_trace(convo, keep_recent=12)
```

例如切口落在 `tool(A)` 上：

```text
……更早历史……
assistant: tool_calls=[A, B]  ← 新边界移到这里
tool(A): 结果                ← 最近 12 条原本从这里开始
tool(B): 结果
……后续消息……
```

现在会保留 A、B 的调用和两个结果原文，仅把该 assistant 之前的旧工具记录提取成摘要。原版则向后跳过 A、B 的结果，把这一组也放入旧摘要。两者都试图避免孤立结果；统一后的算法同时保留了切口附近的完整原始证据。

**为什么可以统一：**所有供应商在 `rollout.py` 内都使用 `assistant.tool_calls + role=tool` 的内部历史。Claude 的 `tool_use/tool_result`、OpenAI Responses 的 `function_call/function_call_output` 是之后在 API 边界转换出来的，因此压缩函数无需知道供应商，也无需新增模块。

此规则适用于使用该压缩函数的执行循环，包括原 GPT、Azure、Claude 和新增 DeepSeek/Kimi。API Reviewer 和 Codex CLI 仍采用各自原有的上下文管理；此次没有给它们另加 `30 / 12` 压缩。

边界算法保证压缩时不拆分已有工具组；对输入中原本缺少的工具结果，它只保留待完成组，不补造执行结果。

这仍是用户确定的压缩策略：新摘要放 `user.content`，不构造假的 `reasoning_content`；最近保留的 assistant 推理原样回传。反复压缩时，原算法不会再提取旧 user 摘要，因此更早摘要可能丢失；此次保留这一原行为，没有另加长期摘要记忆系统。

<a id="rollout-tool-policy"></a>

### 6.6 `rollout.py`：恢复原版宽松参数解析，统一补齐 done 后的历史

**最新决定：这部分对所有供应商保留原版参数容错；统一补齐 `done` 后结果，并对所有异常退出执行清理。** 此前 DeepSeek/Kimi 在本处使用 `tool_arguments(..., strict=True)` 的方案已撤回。

现在的参数解码与原始提交相同，直接放回 `rollout.py`：

```python
try:
    args = json.loads(tc.function.arguments or "{}")
except (json.JSONDecodeError, TypeError):
    args = {}
```

因此本循环不再根据供应商决定是否严格校验，也不再因 schema 类型、必需字段或工具名检查而提前拦截调用。解析成功的值继续进入原执行逻辑；空参数及 JSON 解析错误仍按原方式处理为 `{}`。例如 `{"x":"80"}` 不会在这里被强制改写或拒绝，缺省值和类型转换仍由具体工具决定。

| 情况 | 原始 RoboRSI | 当前统一处理 |
|---|---|---|
| 工具存在、参数符合 schema | 执行工具 | 继续执行原工具 |
| JSON 损坏 | 使用 `{}` 继续原流程 | 相同，所有供应商一致 |
| 参数类型错误、缺少必需字段 | 由原执行逻辑和工具处理 | 相同，不增加 schema 拦截 |
| `done` 后还有其他调用 | 退出，剩余调用可能没有结果 | 退出，并补齐未执行结果 |

**宽松容错的边界：**这是恢复原版参数解码行为，不是保证所有错误参数都能成功执行。工具自身仍可能拒绝参数或抛错；解析结果为数组、`null` 等值时，后续原代码也可能报错。API 返回的调用 ID、`arguments` 字段类型等响应结构检查属于模型协议处理，继续保留。

程序会先把整组 assistant 工具调用写入历史，再逐个执行。GPT/Claude 和新模型都可能在 `done` 后还有尚未执行的调用，因此现在对所有供应商统一追加：

```python
# 在原 done 分支内，返回确认后、break 之前执行；没有 if compatible。
convo.extend(skipped_results(tool_calls[call_index + 1:], "episode ended by done"))
```

这些 tool 结果使用各自原调用 ID，明确 `ok:false`、`executed:false`、`skipped:true` 及跳过原因。原 `done` 的结束时机、成功判定和前序动作保持原逻辑；后续动作不执行。没有剩余调用时，追加的是空列表。

例如 `[A: move, B: done, C: move, D: move]` 仍只执行 A，并确认 B；现在为 C、D 分别保存跳过结果。这样返回的 `prior_messages` 可以完整续接，也不会让已统一的压缩算法把 C、D 一直视为未完成调用。

本节范围是 `rollout.py` 的上述参数与结束行为。消息层的严格参数校验函数仍供 API Reviewer、基线和外层 API 聊天等原有调用方使用；图片交付的后续统一修改见下一节。

原先只有正常结束和两个 `ProviderError` 捕获点执行 `unhook()`。现在在钩子安装后的原对话初始化、工具构造与执行循环外统一加 `try/finally`：

```python
try:
    # 原初始化和执行循环，动作顺序与参数解码不变
    ...
finally:
    unhook()
```

删除两个局部 `except ProviderError: unhook(); raise`，避免重复清理。模型、工具、图片处理抛普通异常或 KeyboardInterrupt 时也恢复本次钩子，原异常继续上抛。该处大部分 diff 只是原循环缩进一层；没有新增执行框架。正常结束时仍在持久化、最终判定之前清理。

<a id="observation-delivery"></a>

### 6.7 图片：本轮取最新观察阶段，各视角保留最后一张，历史不另删

以“拿起奶油奶酪，放进篮子”为例。原版 LIBERO `look(head)` 总是写入 `look_head_camera.jpg`，并更新 `state.last_image_path`；`run_rollout()` 下一轮只在这个路径不同于 `_attached_image_path` 时附图。

**本轮确认的范围：**保留主动调用观察工具的策略。下面的筛选只作用于 `_pending_images`，即本轮工具执行后、尚未加入对话的图片。已经在 `convo` 中的旧图片不会因此被删除、替换或重新排序，仍按原来的超过 30 条触发、最近 12 条为基础并保留完整组的规则压缩。因此，“本轮新增一张图”不表示整个 API 请求中只有一张图。

上一版统一修复会把所有捕获的图片按顺序追加。本轮在它的基础上增加状态与视角筛选，保留即时读取图片字节的机制。

| 调用场景 | 原版结果 | 当前版本本轮新增的观察 |
|---|---|---|
| 同轮 `look(head)` → `look(wrist)`，中间无动作 | 两张文件都存在，但只附最后一张 wrist 图 | head、wrist 一并发送 |
| 同轮 `look(head A)` → `look(wrist B)` → `look(head C)`，中间无动作 | 通常只能附最后一张 head 图 | B、C；同一相机只留最后一次观察，按保留下来的采集顺序排列 |
| 分轮 `look(head A)` → 抓取 → `look(head C)` | 文件已变，路径没变，新图被跳过 | 新增 C；已发送的 A 继续留在历史中 |
| 同轮 `look(head A)` → 抓取 → `look(head C)` | 同名文件覆盖，旧画面可能在读取前消失 | 捕获 A、C 后筛选为 C，不发送本轮较早状态的 A |
| 同轮 `look(head A)` → 抓取 → `look(wrist D)` | 附最后一张 wrist 图 | 只新增 D；不会把 A 当作抓取后的头部图，也不自动补拍 |
| 同轮 `look(head A)` → 抓取，没有再观察 | 可能附抓取前的图，但没有明确状态标签 | 保留最近已知的 A，明确标记环境已推进、图片未刷新 |
| 只有动作，没有工具发布新图片 | 保留旧图历史 | 保留旧图历史，不把未变化的缓存图片再追加一次 |

这里的“同轮”指同一次模型返回后的工具执行批次。`look` 每次只选择一个相机；模型返回多个调用时，分派器依次执行。观察阶段不等于工具调用次数，也不由工具名字推断，而是依据环境状态是否推进区分。

这次继续使用原目录，没有新增运行模块，也没有逐个改写技能。运行代码修改在原 `messages.py`、`rollout.py`、`codeact_runtime.py` 中；实验指纹复用原 `evaluation/suite.py` 的入口。

**① `rollout.py`：在原分派器返回处收集图片。**

原来：

```python
return handler(state, args)
```

现在：

```python
result = handler(state, args)
_capture_tool_image(state, name, args, result[0])
return result
```

上面是收集的核心部分；完整实现还在需要时安装并清理物理步进计数钩子。增加 `result[0]` 是为了使用工具返回的实际 `camera`，而不是仅凭调用参数或文件名判断视角。例如 LIBERO 的 `head`、省略相机参数和 `agentview` 最终都返回 `head_camera`；RoboTwin 的 `look(right_camera)` 与 `scan_wrist(right)` 都属于 `right_camera`。

`DispatchContext` 保留 `_image_fingerprint`、`_pending_images`、`_image_sequence`，新增最近图片的来源记录和物理步进计数。`last_image_path` 保留，已有工具仍按原方式设置这个字段，裁剪工具仍能读取最新文件。

**为什么放在这里：**两个 `look` 即使来自同一条 assistant 回复，也会依次通过分派器。每次工具返回就读取图片，避免等全部工具执行完才发现前面的路径已被覆盖。新编写的技能沿用 `last_image_path` 接口即可接入。

**② `messages.py`：保存不可变图片数据，以及相机、状态和用途。**

`_capture_tool_image()` 读取工具发布的文件，使用以下信息检测刷新：

```python
fingerprint = (
    str(path.resolve()),
    path.stat().st_mtime_ns,
    hashlib.sha256(raw).hexdigest(),
)
```

路径相同而内容不同会被识别；内容相同但文件正常重写也可通过修改时间识别。成功的显式 `look` / `scan_wrist` 即使像素和文件时间戳都相同，也记录为一次新的观察，以保留正确的状态版本；无关动作不会让未变化的缓存文件重复入队。哈希不是图像压缩，只用于判断文件是否变化。

通过 `image_block(path, data=raw)` 当场生成包含原始图片字节的 Base64 内容块。后续工具覆盖同名文件，不会改变这些内容块。Pillow 读取真实编码，因此 PNG 即使误命名为 `.jpg`，请求仍声明 `image/png`。

`_pending_images` 从直接排列文字与图片块，改为保存结构化记录，发送时才展开为原有的 API 消息格式：

```python
{
    "sequence": 3,
    "tool": "look",
    "file": "look_head_camera.jpg",
    "camera": "head_camera",
    "kind": "camera",
    "generation": (1, 80),
    "source": None,
    "views": None,
    "image": image_block(path, data=raw),
}
```

`_observation_generation()` 优先读取环境已有的 `sensor_generation()`。LIBERO 返回 `(reset_generation, observation_generation)`，每次 reset 和成功物理步进更新，不需要为此再读取相机。

没有该接口的本地后端使用 `(0, 本次执行的物理步数)`。主 `run_rollout()` 和 CodeAct 在已有物理采样钩子中计数；独立基线的分派器在工具执行期间使用已有 `hook_physics_step()` 接口临时计数，并在 `finally` 恢复。嵌套技能共用计数，避免重复安装。计数本身不采图、不改变物理动作、不增加工具调用。

**为什么按实际推进区分：**名为“感知”的复合工具也可能移动机器人，而动作工具可能在参数检查或规划失败后根本没有移动。实际物理步进能区分这两种情况，不依赖一张容易遗漏的动作工具名列表。

**③ 保留视觉工具的不同语义。**

| 图片用途 | 记录和筛选方式 |
|---|---|
| `look`、`scan_wrist`、已知的动作后相机观察 | 记录真实相机；同阶段同视角只保留最后一张 |
| `observe_orbit(view=...)` 单视角 | 用 `orbit/视角名` 标识，和普通相机区分；同阶段同视角只留最后一张 |
| `zoom_in` | 标记为 `crop`，继承源图片相机、状态和观察序号；不被同相机的普通图替代 |
| `label_points_grid`、`mark_orbit_point` | 标记为 `annotation`，保留各次标注，避免不同目标点互相覆盖 |
| `observe_orbit()` 多视角拼图 | 标记为 `contact_sheet`，记录返回的视角列表，不把整张拼图当作单个 head/wrist 视角 |
| `preview_move_to_pose` | 标记为 `preview`，不被普通相机图替代 |
| 尚未识别的新视觉技能 | 保留为独立工具图，不猜测为普通相机帧；明确状态版本是发布时刻，采集状态未确定 |

上述专用图片也参与本轮观察阶段筛选；“保留语义”指最新阶段内不因相机相同而互相覆盖，并非把所有阶段的中间图都发送。例如 `look → zoom_in → 同状态的新 look` 保留裁剪图和最后一张原图；如果随后发生动作并获取新状态的观察，旧阶段的待发送裁剪图也不会混入新阶段。

特别是 `look(A) → 动作 → zoom_in(A)`：放大工具裁剪的是缓存 A，不会重新拍摄。裁剪记录继承 A 的旧状态，发送时标记“环境已推进，这不是新观察”。最近图片来源记录不会因发送队列被清空而丢失，所以跨请求调用放大工具同样正确。

**④ `_append_pending_images()`：筛选本轮队列，在完整工具结果后统一追加。**

处理顺序是：

```text
本轮捕获的图片记录
    → 选取最新已观察阶段
    → 普通相机 / 单个 orbit 视角各留最后一张
    → 保留同阶段的裁剪、标注、拼图、预览等独立输出
    → 按原采集顺序生成带来源标签的文字 + 图片块
    → 追加一条 user 观察消息
    → 清空本轮队列
```

如果最后观察之后又执行了动作，但没有新的观察，仍发送最近已知观察，并将观察状态和当前状态一同注明。不会凭空获取动作后图像。

例如模型同轮请求头部和腕部相机，修改后的内部历史为：

```text
assistant：tool_calls = [H: look(head), W: look(wrist)]
tool(H)：原文字/JSON 结果
tool(W)：原文字/JSON 结果
user：
    [TOOL OBSERVATIONS]
    本批最新观察状态：(1, 80)；当前状态：(1, 80)
    Observation 1：tool=look，camera=head_camera，kind=camera，state=(1, 80)
    [图像 A]
    Observation 2：tool=look，camera=wrist，kind=camera，state=(1, 80)
    [图像 B]
```

这由 `_append_pending_images()` 完成，不再判断供应商。工具结果保持字符串；观察消息含非空文字和图片，现有 OpenAI Chat/Responses、Anthropic、DeepSeek/Kimi 转换路径均能保留这些图片。旧版落在 tool.content 中的图片在部分转换路径被丢弃或文本化的问题，也从新生成的历史中消除。

发布后只清空待发送队列，保留 `last_image_path`、最后图片来源和图片指纹。`done` 的未执行调用结果先补齐，再追加筛选后的观察；预算耗尽时也执行同样筛选并保存在返回历史中，不额外请求模型或执行动作。原工具文字结果、调用 ID 和最近 assistant 的 `reasoning_content` 不作改写。

**⑤ 历史压缩不变，其他执行入口共用筛选。**

- 本轮没有修改 `_summarize_old_trace()`。它仍将紧跟工具结果的观察消息纳入同一个组；被压缩的旧组只提取工具名、参数和文字结果，不另行总结图像或旧推理。观察消息识别标记和 `COMPRESSION_POLICY` 也保持原样。
- 筛选函数只追加消息，不扫描和删除历史图片。例如前一轮已发送 A，本轮发送 C，完整请求仍含 A、C；本轮只保证新追加的观察来自最新阶段。
- OpenETA 每次强制 `look()` 后发送队列；CaP-X 在初次代码请求和修复请求前发送队列。
- CodeAct 原有的初始图、脚本结束图获取时机不变；待发送的脚本内观察和结束图使用同一筛选。例如同阶段的脚本结束 head 图会替代本轮较早的 head 图，但不会删除历史中的初始图，也不清空裁剪缓存。
- `OBSERVATION_POLICY` 升为 `tool_boundary_images_v2:latest_pending_state,last_per_view,semantic_images,history_unchanged,all_providers`，通过已有 `evaluation/suite.py` 逻辑进入实验指纹，避免恢复实验时混用不同观察策略。

**范围与代价：**收集的是工具通过 `last_image_path` 发布的观察，不是磁盘上的全部图片或每个物理帧。内部 VLM 定位、SAM 调试图片继续走原感知流程。若一个复合工具内部直接覆盖多次文件、直到最后才返回，也没有经由 `_dispatch_tool()` 产生子调用边界，分派器只能捕获其最终发布的图片；本次没有改变这种工具内部的语义。

物理步进计数能覆盖现有控制和复合技能中的正常步进；自定义工具若直接修改世界状态而不经过环境状态版本或物理步进，需要提供相应状态标记。未知工具图不会被宣称为确定的新相机观察。

相比上一版发送所有中间图，本轮减少了同阶段同视角重复图，以及本轮动作前的中间图；由于保留历史策略，不能据此声称整个请求只有最新图片。原有模型视觉能力、容量限制仍由 `vlm_io.py` 决定；这项统一不会让文本模型获得视觉能力。

### 6.8 感知：显式设置优先，否则跟随本次 Engineer

原版 Engineer 的 `_configure_perception_backend()` 使用 `os.environ.setdefault()` 给 LIBERO 设置默认感知模型，部分分支也写入 `ROBORSI_VLM_PROVIDER`。此前只对新模型避开写入；现在删除该函数及两个调用点，对所有模型采用同一规则：

```python
def resolve_perception_model(fallback: str | None = None) -> str:
    return (
        os.environ.get("ROBORSI_PERCEPTION_MODEL")
        or _PERCEPTION_DEFAULT.get()
        or fallback
        or "anthropic/claude-sonnet-4-6"
    )
```

`with_perception_default` 在实际执行入口绑定模型：`run_rollout()`、OpenETA、CaP-X、CodeAct。Engineer / 基线已经传入实际选定的模型；省略模型时沿用该执行入口的原默认值，并把解析结果传入函数和远程执行参数。`ContextVar` 在退出或异常后恢复；原工具线程通过 `copy_context()` 继承，无须改环境变量。

例：没有显式感知配置时，先运行 GPT Engineer，感知用 GPT；下一任务改用 Kimi，感知随即用 Kimi。若显式 `export ROBORSI_PERCEPTION_MODEL=deepseek/deepseek-flash`，两次任务的感知都使用这个明确指定的模型。

三个复现脚本也移除自动 `export ROBORSI_PERCEPTION_MODEL=...`。已有显式配置仍被读取；不会自动清除用户设置。图像请求的路由仅由最终选中的模型决定，旧 `ROBORSI_VLM_PROVIDER` 不再覆盖它。独立仿真服务进程仍需要自己的显式配置；调用传入的执行模型会在 worker 内建立本次默认值。

**范围：**没有执行上下文的独立感知调用保留传入模型或原独立默认值；Judge、SkillSelector 等独立角色继续使用自己的配置，不自动改成 Engineer。继承模型不会赋予文本模型视觉能力。

`_call_vlm_image_impl()` 将新模型图片请求送入 `_call_vlm_no_tools(..., request_kind="perception")`。LIBERO 商品包装文字分类同样标记为感知用途；`localize_precise()` 的 API 错误上抛继续保留。

### 6.9 API Reviewer：原宽松参数，内部传定义，适配层决定请求

主流程 Reviewer 的 CLI 分工保留；本节针对 `tools/reviewer.py::run_review_agent()` 的 API 路径，gate 原来就使用该路径。

原版收尾给内部函数传空工具列表；此前按供应商决定传完整列表还是空列表。现在统一：

```python
return _text(_call_vlm_tools(model, convo, TOOL_SPECS,
                            tool_choice="none", request_kind="reviewer"))
```

| 适配层 | 实际请求处理 |
|---|---|
| OpenAI Chat / Responses | 在 `_call_vlm_tools()` 的 OpenAI 路由把工具列表置空，继续调用原请求函数；实际请求效果与原收尾一致 |
| Anthropic | 原请求函数已经在 `none` 时移除工具定义，继续沿用 |
| DeepSeek / Kimi | 保留定义，并发送 `tool_choice="none"`；保留新模型推理历史和回复协议检查 |

这统一了调用含义，没有强求三种协议发送相同 HTTP 请求体。原本无工具的纯文本调用仍传 `[]`。

API Reviewer 的参数也恢复原实现：

```python
try:
    args = json.loads(tc.function.arguments or "{}")
except (json.JSONDecodeError, TypeError):
    args = {}
result, image = _dispatch(workspace_root, tc.function.name, args)
```

例如 `read_steps({"round":"1"})` 仍由 `_dispatch()` 将 round 转整数、补上 start/end 默认值；不再因缺少 schema 必需字段而提前拒绝。普通工具轮的顺序、预算和最终 verdict 提取继续沿用原流程。Reviewer 仍读取原有 `tick_*.jpg` / `final_view_*.jpg` 工件，但所有模型都复用 `image_block()` 读取真实编码；不再仅凭文件名声明 JPEG。主 rollout 的观察队列策略继续保留。

`_dispatch()` 内统一限制轮次为 1..N，避免 `round=0` 意外读最后一轮；无法转换的 start/end/index 返回工具错误，负 end 不会反向读入步骤。数字字符串、原 start/end 默认值和 `view_frame(index=-1)` 的最后一帧语义继续支持。JSON 解码本身不增加 schema 门槛。

### 6.10 gate：显式模型配置、已提交代码和完整测量

原 gate 取 `ROBORSI_EVAL_MODEL`，再回退 Manager 模型。现在先取 `ROBORSI_GATE_MODEL`，其余回退保留；新供应商的演化路径要求明确配置 gate。

```python
model = os.environ.get("ROBORSI_GATE_MODEL") or os.environ.get(
    "ROBORSI_EVAL_MODEL") or os.environ.get("ROBORSI_MANAGER_MODEL", "gpt-5.6-sol")
```

**原因：**Manager 的 GPT 名称可能用于 CLI，而 gate 使用 API。显式配置避免隐式选择 GPT API。

原 gate 已用 `git worktree add --detach ... HEAD`。由于不再新增适配文件，现在检查原 `vlm_io.py` 是否已经包含新请求入口：

```python
api_source = tree / "roborsi/embodied/agent_loop/vlm_io.py"
if api_config and (not api_source.is_file() or
                   "def _chat_call_with_tools(" not in api_source.read_text(encoding="utf-8")):
    raise ProviderError("Episode gate uses committed HEAD; commit the API changes before running skill evolution",
                        category="configuration")
```

这只检查入口存在，不保证整个 worktree 与工作区版本相同。因此正式运行仍需提交完整修改；此次没有自动提交用户仓库。

另外，**所有供应商**的 gate 现在要求终局种子集合与预期集合完全一致，而且子进程正常退出：

```python
if set(verdicts) != set(DEV_SEEDS) or proc.returncode != 0:
    # overall_pass=False、complete=False、passes=None
    # observed_passes 仅供排查，不作为正式得分
    ...
```

完整测量仍使用原成功阈值，完整的真实失败可记 `passes=0`；未完成实验不能与零成功率混淆。跨任务比较要求 candidate/baseline 都有有效测量，之后才比较原成功数；`min_pass=0` 不绕过完整性。当前 eval-suite 仅支持 seed-start 加数量，故启动前要求种子列表非空、递增连续，阈值位于 0..种子数；避免配置 21,23 却实际测量 21,22。

### 6.11 故障分类：模型错误不作为机器人失败经验

`ProviderError` 现在定义在 `vlm_io.py`。网络/限流可有限重试；认证、配置和协议错误明确退出，不切换供应商。

长任务 `lh_executor.py` 在原通用异常分支前记录：

```python
{"attempt": attempt_n, "outcome": "api_error",
 "success": None, "category": exc.category, "exception": str(exc)}
```

随后上抛。原先会把异常统称为尝试失败，并提示 Engineer 修复工具参数，这不适合 API 服务故障。

`evaluation/atomic.py` 新增：

```python
if isinstance(exc, ProviderError):
    return "infra" if exc.category in {"transport", "rate_limit"} else "implementation_error"
```

**为什么修改：**基础设施重试、配置错误和真实任务成败需要区分。仿真成功判定和原任务评价条件继续沿用原实现。

### 6.12 其他原 API 入口需要的少量衔接

| 路径 | 原行为与新增处理 | 修改原因 |
|---|---|---|
| OpenETA | 只保存、执行首个调用，原宽松参数解析；不再记录其余调用或补 skipped | 恢复原版一轮一个操作的历史与执行规则 |
| CaP-X | 原保存纯字符串；现在调用 `serialize_assistant_message(reply)` | 代码修复轮次保留原推理 |
| RoboTwin CodeAct | 原按 OpenAI/Anthropic 分流；新前缀进入同一 `_call_vlm_no_tools()` | 复用 I/O；保持原 Python 执行接口 |
| 进度评分技能 | 各模型共用 `_normalize_result()`，识别明确真假值并检查有限评分；保留原请求预算 | 修复 `bool("false")` 错判和无效分数进入判定的问题 |
| 外层聊天的可选 API 分支 | 原参数解析与 `_exec_tool()`，移除新增 schema 门槛；内部收尾统一传工具定义 | 保留原工具容错，新模型仅补必需协议字段；默认 CLI 路径保留 |
| 技能选择器 | 没有直接修改，其已有 `_call_vlm_tools()` 入口 | 公共模型路由已覆盖 |

OpenETA 仍是以下实际路径，而不是保存全部调用后给其余调用补结果：

```python
tc = tool_calls[0]
convo.append(_assistant_tool_calls_msg(msg, [tc]))
try:
    args = json.loads(tc.function.arguments or "{}")
except (json.JSONDecodeError, TypeError):
    args = {}
```

新模型的保存函数先复制原 assistant 字段，再按传入调用 ID 取子集：

```python
message = serialize_assistant_message(msg)
selected = {tc["id"]: tc for tc in message.get("tool_calls", [])}
message["tool_calls"] = [selected[tc.id] for tc in tool_calls]
return message
```

例如返回 `[A: move, B: look]`，OpenETA 的历史只包含 `assistant.tool_calls=[A]` 和 `tool(A)`，实际也只执行 A。原 `reasoning_content` 保持原文，可能仍提及 B；不对推理改写，也不声称这份历史保留了完整原回复。主 rollout 的 `done` 后补齐结果是另一条已确认规则，继续保留。

外层会话的三个执行位置直接恢复 `_exec_tool(tc.function.name, args, target_chat_id, channel=channel, ctx=ctx)`，删除额外的 `execute_call()` 校验包装。每处原有 `json.loads` 和异常回退保持原样；回复的正文拼接、强制提案和收尾时机没有合并成新的执行框架。

进度评分接受 JSON 布尔值、明确的 true/false 字符串和 0/1；数字字符串评分仍可转换。缺失值沿用原 False/0.0 默认值，歧义的 done、NaN、Infinity、溢出、超出 [0,1] 的 score 则使用已有 `ProviderError(category="response")` 报错，不伪装成有效任务反馈。原 `_extract_first_json()` 复用 `vlm_io._parse_json()`；后者改用 `JSONDecoder.raw_decode()`，正确处理 reason 字符串里的括号和转义，同时兼容代码围栏。

辅助图片也统一复用 `messages.image_block()`：感知、Reviewer、进度评分按真实字节识别 JPEG/PNG 等，不再按供应商或后缀选择编码。图片字节不重新编码，观察策略不变。Anthropic 仅在请求构造时把同一块转换成其原生 image/source 格式；原预算、thinking 与请求客户端不变。

CaP-X、CodeAct 的代码执行块中，对 `ProviderError` 提前上抛，避免把技能内的模型服务失败当作代码错误继续修复。独立 judge/selector 仍需按第 1 节明确选模型，没有扩大为全角色自动迁移。

### 6.13 原复现脚本：分角色传参，不新增脚本库

原脚本要求 `OPENAI_API_KEY`，并把同一个 `ROBORSI_EVAL_MODEL` 传给三个角色。现在在原脚本中解析：

```bash
export ROBORSI_PLANNER_MODEL="${ROBORSI_PLANNER_MODEL:-${ROBORSI_EVAL_MODEL:-}}"
export ROBORSI_ENGINEER_MODEL="${ROBORSI_ENGINEER_MODEL:-${ROBORSI_EVAL_MODEL:-}}"
export ROBORSI_REVIEWER_MODEL="${ROBORSI_REVIEWER_MODEL:-${ROBORSI_EVAL_MODEL:-}}"
```

启动命令修改为按角色传入。以 PLUS 为例：

```diff
- --planner-model "${ROBORSI_EVAL_MODEL}" \
- --engineer-model "${ROBORSI_EVAL_MODEL}" \
- --reviewer-model "${ROBORSI_EVAL_MODEL}" \
+ --planner-model "${ROBORSI_PLANNER_MODEL}" \
+ --engineer-model "${ROBORSI_ENGINEER_MODEL}" \
+ --reviewer-model "${ROBORSI_REVIEWER_MODEL}" \
```

启动块检查实际 API 角色需要的密钥。CLI Planner/Reviewer 不要求 OpenAI API Key，也不能填入 DeepSeek/Kimi API 名称；`ROBORSI_ROLE_SESSION=0` 时则检查它们的 API 密钥。

新模型 Engineer 的默认 effort 为 `high`，原路径默认仍为 `medium`；显式配置优先。所有模型的感知缺省值都在 Python 执行范围内跟随实际执行模型，脚本不再自动导出感知默认值。三个脚本各自保留这段小配置块，Python 中的详细参数和能力检查仍只有 `vlm_io.py` 一处。

**为什么修改：**让原复现入口能够实际传入“CLI 角色 + 国产 API Engineer”配置，不需要另起评测流程。`pyproject.toml` 只增加 `jsonschema>=4.20.0,<5.0.0`，用于工具定义的 schema 检查，不检查返回的调用参数。

### 6.14 实验指纹归入原 `evaluation/suite.py`

`_runtime_fingerprint()` 原有版本、Python、后端信息保留，增加：

```python
"api_adapter": _api_runtime_settings(),
```

同文件的 `_api_runtime_settings()` 记录配置白名单、依赖版本、默认预算与压缩策略。API Key 不进指纹，端点记录去除凭据和查询参数。

恢复原版参数容错后，新增记录 `rollout_tool_policy={"arguments":"legacy_json","done_results":"complete"}`，明确本次执行策略，供实验恢复检查使用。此字段仅记录行为，不是额外的运行开关。

当前压缩标识为 `tool_trace_only_v3:threshold=30,recent=12,complete_groups_and_observations,all_providers`，观察标识由同一指纹函数记录。2026-10-22 的感知继承、辅助工具解析和 OpenETA 历史规则调整后，`adapter_version` 先更新为 3；随后统一修正评分、清理、gate、图像编码和证据索引，当前升级为 4，区分本轮行为与旧实验；没有改变原评测调度。

新供应商 campaign 恢复时，即使是允许技能提交变化的 evolve 模式，也拒绝混入不同 API 设置。当前 `adapter_version=4`，因此旧版本 campaign 应使用新输出目录。

**为什么修改：**实验记录属于评测层，模型 I/O 不必知道如何恢复 campaign；同一模型名称下改预算或端点也应被记录。这个指纹不锁定服务商背后的模型权重版本。

### 6.15 测试与程序修改的对应关系

新增测试仍在 [test_model_api_adaptation.py](../tests/test_model_api_adaptation.py)，已经改为直接测试原 `vlm_io.py`、`messages.py`、`evaluation/suite.py` 的入口。

| 修改内容 | 代表性测试函数 |
|---|---|
| 前缀 / 别名统一分派，原请求参数和重试边界 | `test_model_prefix_routes_tools_and_text_with_original_request_options` |
| 明确指定的感知模型决定图像路由 | `test_explicit_perception_model_determines_image_route` |
| Chat prefill 检查移入请求实现，发送前拒绝 | `test_chat_prefill_rejected_before_request` |
| 回复原字段最终进入 HTTP JSON | `test_wire_preserves_reasoning_raw_arguments_and_usage` |
| 连续压缩和对话延续 | `test_two_compactions_and_continuation_reach_wire` |
| 旧推理丢弃、完整工具组保留 | `test_compaction_discards_old_reasoning_and_preserves_complete_groups` |
| 大于 12 条的完整组和未完成组，保留更早记录的摘要 | `test_compaction_keeps_large_groups_and_summarizes_only_older_groups` |
| GPT / Azure / Claude 执行循环统一压缩，Responses / Anthropic 转换后 ID 配对 | `test_legacy_rollout_compaction_preserves_groups_through_conversion` |
| DeepSeek/Kimi 宽松参数、推理保存、done 配对及续接 | `test_rollout_keeps_legacy_args_reasoning_and_done_pairing` |
| GPT / Azure / Claude 保留宽松参数、补齐多条 skipped 结果及续接 | `test_legacy_rollout_keeps_lax_args_and_completes_done_history` |
| Reviewer / OpenETA / 外层会话的原宽松行为 | `test_reviewer_preserves_tool_defaults_and_passes_definitions_to_adapter`、`test_openeta_keeps_only_first_call_with_original_argument_decoding`、`test_outer_chat_retains_reasoning_and_delegates_lax_args` |
| 所有模型的感知继承、异常恢复与线程传播 | `test_execution_model_is_inherited_without_environment_writes`、`test_perception_model_reaches_the_actual_tool_dispatch_thread` |
| OpenAI Chat/Responses 收尾移除工具定义 | `test_openai_final_answer_omits_tools_at_transport_only` |
| 图片编码及感知继承 | `test_image_is_a_user_message_with_actual_mime`、`test_perception_inheritance_is_scoped_and_explicit_override_wins` |
| 五种供应商路径：本轮按阶段/视角筛选，相机别名统一，历史图片不额外删除 | `test_rollout_preserves_observations_across_providers` |
| 像素与时间戳相同的显式观察仍更新状态，动作不自动触发观察 | `test_successful_look_updates_state_even_when_pixels_and_mtime_are_identical` |
| 放大图、标注图不被原图覆盖；裁剪继承源图状态 | `test_latest_camera_does_not_replace_requested_crops_and_markers`、`test_crop_inherits_source_state_instead_of_becoming_a_new_observation` |
| Orbit 拼图、不同标记点、预览和未知技能图保留各自语义 | `test_orbit_views_keep_contact_sheet_and_distinct_point_annotations`、`test_preview_and_unfamiliar_visual_outputs_are_not_deduplicated_as_camera_frames` |
| 复合工具实际步进、失败但已移动、未移动的拒绝、钩子恢复及复用 | `test_physics_fallback_tracks_nested_tools_and_preserves_existing_hook`、`test_observation_step_hook_is_restored_when_tool_raises`、`test_rollout_reuses_physics_capture_hook_for_observation_stages` |
| done/预算结束仍保留图片，磁盘覆盖不影响图片快照 | `test_final_tool_batch_keeps_images_and_complete_results`、`test_image_snapshot_survives_overwrite_with_unchanged_mtime` |
| 压缩时图片与完整工具组一起保留 | `test_compression_keeps_tool_results_and_observations_together` |
| OpenETA、CodeAct 使用共同观察流程 | `test_openeta_receives_fresh_image_each_cycle`、`test_codeact_keeps_each_view_and_preserves_crop_cache` |
| API Reviewer 收尾 | `test_reviewer_final_request_keeps_tools_but_forbids_execution` |
| CLI 与旧 Claude 路由 | `test_cli_roles_remain_cli`、`test_legacy_claude_still_uses_native_stream` |
| 基线与代码修复历史 | `test_baseline_history_keeps_selected_call_or_code_repair`、`test_codeact_completion_uses_adapter_and_keeps_reasoning` |
| gate 子进程及不完整测量 | `test_gate_uses_api_model_in_child_and_does_not_score_missing_episodes` |
| 已提交代码检查 | `test_gate_rejects_uncommitted_adapter_before_model_call` |
| 模型失败不伪装视觉证据 | `test_api_errors_are_not_negative_visual_observations` |
| 真假值、有限评分及 JSON 字符串括号 | `test_progress_judge_never_turns_invalid_evidence_into_success` |
| 各类异常和用户中断恢复物理钩子 | `test_rollout_restores_physics_hook_on_every_exception` |
| 错误轮次/范围不读错证据，数字字符串和负帧索引继续可用 | `test_reviewer_indices_do_not_select_the_wrong_round_or_abort_review` |
| 错种子/非零退出不计分，跨任务比较须有效测量 | `test_gate_uses_api_model_in_child_and_does_not_score_missing_episodes`、`test_cross_task_gate_requires_two_valid_measurements` |
| 不可表示的种子列表在启动前报错 | `test_gate_rejects_unrepresentable_seeds_and_invalid_threshold_before_launch` |
| 三个复现脚本的混合模型配置 | `test_reproduction_script_accepts_mixed_cli_and_api_without_openai_key` |
| 评测配置漂移 | `test_evolve_resume_rejects_api_configuration_drift` |

测试用例名称中的 `adapter` 表示协议适配能力，不代表仍存在独立的适配模块。本轮执行循环测试按用户确定的新行为调整：损坏 JSON 对应 `{}`、字符串数字和缺少必需字段的参数进入原分派器；`done` 后的动作不进入分派器，并保留完整结果配对。此前新增的参数严格校验测试已移除，改为验证各入口确实恢复原分派器与默认参数；协议字段校验测试继续保留。

## 7. 自己查看原版差异，以及后续维护

在 RoboRSI 子仓库执行；这些命令只读取差异，`--no-pager` 避免进入需要按 `q` 退出的分页器：

```bash
cd /home/user/hyn/agentrl/S2AC/RoboRSI
git --no-pager diff --stat 580876f747dea67d6bc5b3cf0569f46d0d5a6c43
git --no-pager diff 580876f747dea67d6bc5b3cf0569f46d0d5a6c43 -- \
  roborsi/embodied/agent_loop/vlm_io.py \
  roborsi/embodied/agent_loop/messages.py \
  roborsi/embodied/agent_loop/rollout.py
git status --short
```

查看原始文件，例如：

```bash
git --no-pager show 580876f747dea67d6bc5b3cf0569f46d0d5a6c43:roborsi/embodied/agent_loop/vlm_io.py
```

`git diff` 不显示未跟踪的新文件内容，所以新测试和本文需结合 `git status` 查看。对照无需切换分支或回退工作区。

本轮没有修改 `HEAD`、提交或推送。原始提交仍可直接查看；正式评测和技能 gate 需要用户将最终修改完整提交后运行。

后续增加模型时，沿用现有供应商前缀的型号优先修改 `vlm_io.py` 的能力表和请求参数；增加服务商或别名时再更新 `_MODEL_ROUTES` 和相应配置。随后用现有消息、路由与循环测试验证。只有出现原结构无法表达的新协议时才考虑扩展接口；S2AC 的 Actor/Critic 或 skill 更新算法应另做独立修改，便于与这次模型接入区分。
