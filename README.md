<h1 align="center">RoboRSI</h1>

<p align="center">
  <strong>Stable, efficient, and reusable robot self-evolution in complex real-world environments.</strong>
</p>

<p align="center">
  English | <a href="README_zh.md">简体中文</a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-open_source-1f6feb"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-3776ab">
  <img alt="LIBERO 120 tasks" src="https://img.shields.io/badge/LIBERO-120_tasks-16845b">
</p>

RoboRSI is a multi-agent harness for robot self-evolution. A Manager
decomposes tasks, a Planner writes executable plans, an Engineer drives
skills against the live environment, and an independent Reviewer diagnoses
the visible trace. Top-down Skill Refinement (TSR) keeps every capability in
a task–skill tree: online exploration finds a solution, stable workflows
consolidate into code, execution data can train a learning-based policy, and
failures return to the earliest responsible node.

Project page: <https://robo-rsi.com/blog/2-roborsi-research-preview/>

<p align="center">
  <img src="https://robo-rsi.com/assets/roborsi/figures/roborsi-self-evolution-architecture-v35.svg" alt="RoboRSI self-evolution loop" width="900">
</p>

## Applications

1. Real-world mobile manipulation (scene search, approach, grasp, place).
2. Zero-shot task adaptation from Base Skills.
3. Repeated-task automation through consolidated code.
4. Data flywheel and learning-based policy training.
5. Perturbation-robust manipulation.

## Results

| Metric | Result | Scope |
|---|---:|---|
| LIBERO cumulative task pass rate | **95/120** | Cross-release adaptive coverage; ten sequential rounds moved 32/120 → 83/120 |
| LIBERO-PRO cumulative task pass rate | **80/120** | Five adaptive releases, 43 → 80 cumulative task coverage |
| LIBERO-Plus perturbation-instance pass rate | **398/840** adaptive Pass@2 | 840 = 7 perturbation categories × 120 instances; fixed release 261/840; +16.3 points |
| Matched Code-on / Code-off episode pass rate | **174/600 vs 129/600** | 120 tasks × 5 initial layouts per group; +7.5 points |
| Matched efficiency panel (118 tasks) | tokens **−29.4%** · VLM calls **−27.2%** · wall time **−17.0%** | Median Code-on vs Code-off |
| RoboTwin cumulative task pass rate | **36/50** | Planner + Engineer + Reviewer; single-role baseline 9/50 |
| Corrective learning-based policy case | 1 matched task success | 304-frame corrective trajectory → 2,432 samples → 1,000-step fine-tune |

Cumulative task pass rates count tasks passed at least once across evolving
releases; they are not frozen-policy scores or fixed-method Pass@k. Full
calibers, videos, and exact tool traces are on the
[project page](https://robo-rsi.com/blog/2-roborsi-research-preview/).

## How It Works

```
long_horizon/<task>/   task family: user instruction → ordered atomic sequence
        ▼
atomic/<task>/         atomic task: clear scope, verifiable outcome; stable
        ▼              paths consolidate into code (e.g. visual_pick_place)
base/<robot>/<prim>/   base skills: perception, motion, grasp, place —
                       callable by atomics and exposed to the agent as tools
```

```
Manager ──► Planner ──► Engineer ──► Reviewer
task queue   plan.md    tool loop    root cause + revision proposal
```

When a task keeps failing, the Reviewer locates the earliest responsible
node and proposes a skill change; a no-regression gate on a real simulator
task must pass before the change is committed. Every applied change is an
ordinary git commit, so the history stays auditable.

Evaluation (`roborsi eval` / `eval-suite`) runs the same role chain against a
frozen release with self-evolution and persistent write-back disabled.
Success is decided only by the simulator's own predicate after the agent
loop ends; journals are append-only, and `roborsi eval-audit` recomputes
scores independently.

## Installation

### One-click reproduction

```bash
git clone https://github.com/nssmd/RoboRSI.git && cd RoboRSI
export OPENAI_API_KEY="..."   # any OpenAI-compatible Responses endpoint
scripts/reproduce_libero_pro.sh
```

The script creates an isolated environment, installs RoboRSI, clones
LIBERO-PRO, downloads the official perturbation assets from
[`zhouxueyang/LIBERO-Pro`](https://huggingface.co/datasets/zhouxueyang/LIBERO-Pro),
configures and health-checks the backend, starts the PyRoKi IK/trajectory
service, launches a frozen code-on Pass-1 campaign, and audits the journal.
It is idempotent and resumable. A fresh campaign evaluates the current
frozen release; it does not replay the cumulative results above
(see [docs/EVALUATION.md](./docs/EVALUATION.md)).

### Manual installation

See [docs/INSTALLATION.md](./docs/INSTALLATION.md) and
[docs/DOCKERINSTALLATION.md](./docs/DOCKERINSTALLATION.md).

```bash
pip install -e ".[libero]"
git clone --depth 1 https://github.com/Zxy-MLlab/LIBERO-PRO.git
hf download zhouxueyang/LIBERO-Pro --repo-type dataset --local-dir ./LIBERO-PRO-assets
roborsi libero configure \
  --root ./LIBERO-PRO \
  --bddldir ./LIBERO-PRO-assets/bddl_files \
  --initdir ./LIBERO-PRO-assets/init_files
roborsi libero doctor --backend libero --task libero_object/0 --reset
roborsi web   # evolution dashboard :8787 · Manager cockpit :8795
```

## Community

Scan to join the WeChat user group (the QR code is refreshed periodically —
open an issue if it has expired):

<p align="center">
  <img src="assets/wechat-group.jpg" alt="RoboRSI WeChat user group QR code" width="320">
</p>

- GitHub Issues: <https://github.com/nssmd/RoboRSI/issues>

## Citation

```bibtex
@misc{noematrix2026roborsi,
  author       = {{Noematrix Team}},
  title        = {RoboRSI: Stable, Efficient, and Reusable Robot Self-Evolution in Complex Real-World Environments},
  year         = {2026},
  month        = sep,
  howpublished = {Research Blog},
  url          = {https://lab.noematrix.ai/blog/2-roborsi-research-preview/}
}
```
