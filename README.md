<h1 align="center">RoboRSI</h1>

<p align="center">
  <strong>Stable, efficient, and reusable robot self-evolution in complex real-world environments.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-open_source-1f6feb"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-3776ab">
  <img alt="LIBERO 120 tasks" src="https://img.shields.io/badge/LIBERO-120_tasks-16845b">
  <img alt="Frozen evaluation" src="https://img.shields.io/badge/evaluation-frozen%2C_append--only-171917">
</p>

RoboRSI is a multi-agent harness for robot self-evolution. A Manager
decomposes tasks, a Planner writes executable plans, an Engineer drives
skills against the live environment, and an independent Reviewer diagnoses
the visible trace. Top-down Skill Refinement (TSR) keeps every capability in
a task–skill tree with clear ownership: online exploration finds a solution,
stable workflows consolidate into code, execution data can train a
learning-based policy, and failures return to the earliest responsible node.

Project page: <https://robo-rsi.com/>

## Three Core Features

1. **Multi-Agent operation.** Manager, Planner, Engineer, and Reviewer share
   one workspace; roles stay separated so failures can be attributed and
   revised at a specific node.
2. **Top-down Skill Refinement.** A Task Family fixes the high-level task
   structure; Atomic Tasks give verifiable scope; Base Skills wrap direct
   robot interaction. Stable paths consolidate into code
   (`SKILL.md + policy.py`); gated trajectories can train a learning-based
   policy that returns to the toolchain.
3. **Evidence-gated evaluation.** Plans, tool traces, videos, trajectories,
   token/time cost, and the final simulator verdict stay attached to every
   episode in an append-only journal; success is decided only by the
   simulator predicate after the agent loop ends.

## Applications

1. Real-world mobile manipulation (scene search, approach, grasp, place).
2. Zero-shot task adaptation from Base Skills.
3. Repeated-task automation through consolidated code.
4. Data flywheel and learning-based policy training.
5. Perturbation-robust manipulation.

## Evidence Tracks

| Metric | Result | Scope |
|---|---:|---|
| LIBERO cumulative task pass rate | **95/120** | Cross-release adaptive coverage; ten sequential rounds moved 32/120 → 83/120 |
| LIBERO-PRO cumulative task pass rate | **80/120** | Five adaptive releases, 43 → 80 cumulative task coverage |
| Strict Standard-130 task-level Pass@10 | **67/130** | Final simulator verdict only; no agent-visible checker or latch |
| LIBERO-Plus perturbation-instance pass rate | **398/840** adaptive Pass@2 | 840 = 7 perturbation categories × 120 instances; fixed release 261/840; +16.3 points |
| Matched Code-on / Code-off episode pass rate | **174/600 vs 129/600** | 120 tasks × 5 initial layouts per group; +7.5 points |
| Matched efficiency panel (118 tasks) | tokens **−29.4%** · VLM calls **−27.2%** · wall time **−17.0%** | Median Code-on vs Code-off |
| Historical RoboTwin coverage | 36/50 task-level pass@k | Episode-level 104/422; packaged as evidence, no released RoboTwin runtime |
| Corrective learning-based policy case | 1 matched task success | 304-frame corrective trajectory → 2,432 samples → 1,000-step fine-tune |

Cumulative task pass rates count tasks passed at least once across evolving
releases. They are **not** frozen-policy scores, single-release results, or
conventional fixed-method Pass@k. The strict, adaptive, matched, LIBERO-Plus,
learning-based-policy, and historical RoboTwin tracks stay separate in the
data and the UI.

## How It Works

### The task–skill tree

```
long_horizon/<task>/   task family: user instruction → ordered atomic sequence
        ▼
atomic/<task>/         atomic task: clear scope, verifiable outcome; stable
        ▼              paths consolidate into code (e.g. visual_pick_place)
base/<robot>/<prim>/   base skills: perception, motion, grasp, place —
                       callable by atomics and exposed to the agent as tools
```

### The agent chain

```
Manager ──► Planner ──► Engineer ──► Reviewer
   │           │            │            │
task queue   plan.md    per-step      structured verdict:
approval     strategy   tool loop     root cause, next action,
versions     and reuse  on live env   proposal when warranted
```

### Self-improving skills

```
①  fail        Reviewer locates the earliest responsible node
②  propose     propose_new_skill / propose_skill_update
③  gate        no-regression check on a real simulator task (never skipped)
④  apply       gate passes → committed → the next attempt uses the new skill
```

Every applied change is an ordinary, gated git commit, so the full history
stays auditable.

## Frozen Evaluation

`roborsi eval` runs the same Planner → Engineer → Reviewer path against a
frozen release. Evaluation keeps per-run plans, reviews, traces, videos,
timing, tool calls, and the final simulator verdict. It does **not** create
or apply proposals, register skills, update task memory, or place episodes in
the training data store. Campaign manifests pin the task panel, role models,
tool budget, and retry policy; infrastructure and implementation errors are
reported separately and never enter the task denominator.

```bash
# single task
roborsi eval libero_pick_place --backend libero --sim-task libero_object/0

# resumable full-panel campaign (frozen release, code-on)
scripts/run_libero_pro_matched_pass1.sh ~/.roborsi/evals/suites/my-pass1

# independent audit: recompute the score from the append-only journal
roborsi eval-audit ~/.roborsi/evals/suites/my-pass1 --check-media --require-complete
```

A fresh campaign evaluates the current frozen release. It does not replay the
historical cross-release results in the table above; see
[docs/EVALUATION.md](./docs/EVALUATION.md) for that boundary.

## Integrity by Construction

- Success is judged by the simulator's own predicate, evaluated **only after**
  the agent tool loop ends — never by the model's own claim.
- Per-action rewards, success flags, and hidden object state are not exposed
  through the skill interface.
- No physics overrides: no expert replay, no force-attach or teleport.
- Generated policy code is capability-limited to the released public skill
  surface; it cannot read simulator internals, files, processes, or networks.
- Evaluation mode disables self-evolution and persistent write-back at two
  independent layers, covered by dedicated tests.

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
configures and health-checks the backend, sets up and starts the PyRoKi
IK/trajectory service, launches the frozen code-on Pass-1 campaign, and
audits the journal when the run completes. It is idempotent and resumable.

### Manual installation

- [Non-Docker installation](./docs/INSTALLATION.md)
- [Docker installation](./docs/DOCKERINSTALLATION.md)
- [Architecture deep-dive](./docs/architecture.md)

```bash
pip install -e ".[libero]"
git clone --depth 1 https://github.com/Zxy-MLlab/LIBERO-PRO.git
hf download zhouxueyang/LIBERO-Pro --repo-type dataset --local-dir ./LIBERO-PRO-assets
roborsi libero configure \
  --root ./LIBERO-PRO \
  --bddldir ./LIBERO-PRO-assets/bddl_files \
  --initdir ./LIBERO-PRO-assets/init_files
roborsi libero doctor --backend libero --task libero_object/0 --reset
```

Start the local interfaces after installation:

```bash
roborsi web   # evolution dashboard :8787 · Manager cockpit :8795
```

## Scope and Dependencies

This repository ships the LIBERO reference runtime. Historical RoboTwin
results are packaged as evidence only; the repository does not include a
released RoboTwin runtime, and RoboTwin reproduction is not claimed. The
PyRoKi solver runs as an isolated service (`scripts/pyroki_ik_server.py`);
SAM3 / GraspGen services are optional — without them perception falls back to
the built-in open-vocabulary detector, which changes absolute pass rates.
LIBERO, LIBERO-PRO, and PyRoKi retain their upstream licenses.

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
