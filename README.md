<h1 align="center">RoboRSI</h1>

<p align="center">
  <strong>Stable, efficient, and reusable robot self-evolution in complex real-world environments.</strong>
</p>

<p align="center">
  English | <a href="README_zh.md">简体中文</a>
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2610.12424"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2610.12424-b31b1b"></a>
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

Paper: <https://arxiv.org/abs/2610.12424> · Project page: <https://lab.noematrix.ai/blog/2-roborsi/>

<p align="center">
  <img src="assets/real-world-demo.gif" alt="Real-robot full task chain, 16x timelapse" width="900"><br>
  <em>Real-robot full task chain (16× timelapse) — <a href="https://lab.noematrix.ai/blog/2-roborsi/">full video with exact tool traces on the project page</a></em>
</p>

<p align="center">
  <img src="https://lab.noematrix.ai/assets/roborsi/figures/roborsi-self-evolution-architecture-v35.svg" alt="RoboRSI self-evolution loop" width="900">
</p>

## Results

Simulation results from Table 1 of the [paper](https://arxiv.org/abs/2610.12424).
All methods use GPT-5.6-SOL. Success is successful/valid episodes; coverage
counts each task once after its first success.

| Benchmark | Protocol | Success | Coverage | Strongest baseline (OpenETA) |
|---|---|---:|---:|---:|
| LIBERO | 30 tasks × 5 episodes, improving online | **84/150 (56.0%)** | 24/30 | 76/150 (50.7%) |
| LIBERO-PRO | 120 tasks × 5 episodes, improving online | **297/600 (49.5%)** | 102/120 | 231/600 (38.5%) |
| LIBERO-Plus | 840 perturbation instances of 30 tasks, LIBERO library held fixed | **354/840 (42.1%)** | 28/30 | 306/840 (36.4%) |
| RoboTwin | 50 tasks, first 154 episodes of the online run | **37/154 (24.0%)** | 26/50 | 32/150 (21.3%) |

Ablations in the paper:

| Study | Result |
|---|---|
| Role separation (RoboTwin, full online run) | Planner + Engineer + Reviewer solve 36/50 tasks; a single Engineer solves 9/50 |
| Code consolidation (120 LIBERO-Plus tasks × 5 seeds, matched pairs) | Code on 174/600 (29.0%) vs Code off 129/600 (21.5%); median tokens −29.4%, model calls −27.2%, wall time −17.0% |
| Policies from execution data (ACT, 10,000 steps) | Chunk-relative joint targets: click_bell 4/10, click_alarmclock 6/7 on training layouts and 2/3 on unseen layouts; absolute targets solve none |

Videos and exact tool traces are on the
[project page](https://lab.noematrix.ai/blog/2-roborsi/).

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
git clone --recursive https://github.com/nssmd/RoboRSI.git && cd RoboRSI
export OPENAI_API_KEY="..."            # any OpenAI-compatible Responses endpoint
export ROBORSI_EVAL_MODEL="..."        # the paper uses GPT-5.6-SOL
scripts/reproduce_paper.sh libero       # LIBERO, 30 tasks x 5 episodes, evolve
scripts/reproduce_paper.sh libero-pro   # LIBERO-PRO, 120 tasks x 5 episodes, evolve
scripts/reproduce_paper.sh libero-plus  # LIBERO-Plus, 840 instances, LIBERO library frozen
```

Each command creates an isolated environment, installs RoboRSI, clones
LIBERO-PRO, downloads the official perturbation assets from
[`zhouxueyang/LIBERO-Pro`](https://huggingface.co/datasets/zhouxueyang/LIBERO-Pro),
health-checks the backend, starts the PyRoKi IK/trajectory service (and
GraspGen if configured), runs the paper protocol, audits the journal, and
prints episode success and coverage. `libero` and `libero-pro` evolve while
they are evaluated, on a copy of the skill library under the output
directory; `libero-plus` evaluates the library and wiki produced by the
`libero` run without changing them, so run `libero` first. Every command is
resumable. Because the system learns during the run, results vary with the
model and the run and are not expected to match the table exactly.

`scripts/reproduce_libero_pro.sh` runs a different, stricter measurement:
the current release frozen, one episode per LIBERO-PRO task (Pass 1). It is
not an experiment in the paper. See [docs/EVALUATION.md](./docs/EVALUATION.md).

RoboTwin runs in its own conda environment; see
[docs/sim-robotwin.md](./docs/sim-robotwin.md).

Recommended model, GPU, parallelism and budget settings:
[docs/EVALUATION.md#recommended-setup](./docs/EVALUATION.md#recommended-setup).

### Manual installation

See [docs/INSTALLATION.md](./docs/INSTALLATION.md) and
[docs/DOCKERINSTALLATION.md](./docs/DOCKERINSTALLATION.md).

Connecting your own robot: see [roborsi/embodied/embodiment/README.md](./roborsi/embodied/embodiment/README.md).

```bash
pip install -e ".[libero]"
git clone --depth 1 https://github.com/Zxy-MLlab/LIBERO-PRO.git
hf download zhouxueyang/LIBERO-Pro --repo-type dataset --local-dir ./LIBERO-PRO-assets
roborsi libero configure \
  --root ./LIBERO-PRO \
  --bddldir ./LIBERO-PRO-assets/bddl_files \
  --initdir ./LIBERO-PRO-assets/init_files
roborsi libero doctor --backend libero --task libero_object/0 --reset
roborsi web   # evolution dashboard :8787
```

## Community

Scan to join the WeChat or Feishu user group (the QR codes are refreshed periodically):

<p align="center">
  <img src="assets/wechat-group-20261007.jpg" alt="RoboRSI WeChat user group QR code" width="300">
  &nbsp;&nbsp;
  <img src="assets/feishu-group.jpg" alt="RoboRSI Feishu user group QR code" width="300">
</p>
<p align="center"><em>WeChat group &nbsp;·&nbsp; Feishu group</em></p>

## Citation

```bibtex
@article{wen2026roborsi,
  title   = {RoboRSI: Stable, Efficient, and Reusable Robot Self-Evolution in Complex Real-World Environments},
  author  = {Wen, Zimo and Chen, Yijin and Cao, Yuxuan and Chen, Wendi and Zou, Yanwen and Yu, Wenye and Kuang, Fuhang and Xue, Han and Lv, Jun and Wen, Chuan and Lu, Cewu},
  journal = {arXiv preprint arXiv:2610.12424},
  year    = {2026}
}
```
