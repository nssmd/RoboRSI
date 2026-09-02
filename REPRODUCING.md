# Reproducing RoboRSI LIBERO

RoboRSI exposes three separate evaluation tracks. Keep them separate when
reporting results: they answer different questions and use different release
semantics.

## Track A: Replay The Reported Evidence

Use this path to verify the public `95/120` headline without a simulator, GPU,
or API key.

```bash
git clone https://github.com/nssmd/RoboRSI.git
cd RoboRSI

./reproduce.sh
```

Outputs:

```text
artifacts/reproduction/replay.json
artifacts/reproduction/dashboard.html
```

To run the same steps manually:

```bash
./setup.sh --core-only
./roborsi results replay \
  --manifest evidence/adaptive-coverage-v1/manifest.json \
  --json artifacts/replay.json
```

Expected result:

```text
Spatial    9/10
Object    10/10
Goal       9/10
LIBERO-90 67/90
Total     95/120 = 79.2%
```

Inspect the replay in a browser:

```bash
./roborsi web \
  --result artifacts/replay.json \
  --output artifacts/dashboard.html
```

The compact bundle contains one canonical final simulator-success record for
each solved task. It verifies:

- task identity;
- seed and public release-track identity;
- final simulator verdict;
- task-level cumulative coverage;
- suite-level coverage.

It intentionally omits most failed and infrastructure attempts. Do not use it
to estimate total historical campaign Token consumption or elapsed time.

## Track B: Run A New Adaptive Campaign

Use this path to evaluate the full 120-task catalog while allowing validated
skills to enter later passes.

### Requirements

- Linux;
- Python `3.10` through `3.12`;
- Git;
- an OpenAI-compatible Responses endpoint with `gpt-5.6-sol` access;
- an NVIDIA GPU for practical throughput.

### Install

```bash
export OPENAI_API_KEY="..."
./setup.sh
```

The setup script creates:

```text
.venv/                    RoboRSI and LIBERO runtime
.venv-pyroki/             isolated motion-planning service
.deps/LIBERO/             pinned LIBERO checkout
.deps/pyroki/             pinned PyRoKi checkout
.runtime/libero/           noninteractive LIBERO path configuration
roborsi.yaml               canonical non-secret configuration
```

It is idempotent: existing environments and checkouts are reused, pinned
revisions are rechecked, and editable packages are refreshed.

### Preflight

```bash
./roborsi eval libero-short --mode adaptive --dry-run
./roborsi doctor
```

The doctor must pass:

- canonical model and medium reasoning;
- 120-task catalog;
- LIBERO checkout and path configuration;
- writable result root;
- Responses provider access;
- PyRoKi readiness.

Optional segmentation and GraspGen services are reported separately and do not
silently change required checks.

### Launch

```bash
./roborsi eval libero-short --mode adaptive
```

The command creates a new append-only directory under `runs/` and starts the
supervisor in the background. Follow:

```bash
./roborsi runs list
./roborsi status <run-id>
./roborsi web --run <run-id>
tail -f runs/<run-id>/supervisor.log
```

`roborsi web` selects the latest campaign when `--run` is omitted. A served
running campaign refreshes every 15 seconds; `--output FILE` writes a static
HTML snapshot instead.

For every ordered seed, the supervisor schedules only tasks that have not
already succeeded. Visible failures may produce complete candidate skills. A
candidate is:

1. expressed as complete Compound Skill metadata plus a non-empty declarative
   `PROGRAM = [{"tool": ..., "args": ...}, ...]`;
2. restricted to published visible Base or Compound Skills and declared
   `$argument` placeholders;
3. written into an isolated campaign overlay;
4. loaded without modifying the active release;
5. run on two fixed validation seeds, using a distinct holdout when available;
6. promoted only when both runs return a final simulator-success verdict.

Arbitrary Python, simulator imports, hidden-state tools, empty programs, and
undeclared placeholders fail the static gate. Task failures reject the
candidate. Provider, transport, image, or resource interruptions leave the
candidate pending with the same release identity and validation seeds, so only
unfinished validation work is retried. Rejected code and failed runs remain on
disk.

### Inspect the top-down execution artifacts

For each attempted task, the Atomic Task profile selects its Task Family parent.
The Planner receives the visible LIBERO instruction and public skill
descriptions, then persists:

```text
runs/<run-id>/episodes/<run>/<task>/seed-<n>/shard-<n>/attempt-<n>/roles/
  plan.json
  plan.md
  summary.md
  review.md              written when the Reviewer completes
```

The machine-readable plan schema is `roborsi.top_down_plan.v1`:

```json
{
  "task_family": "libero_pick_place",
  "atomic_task": "libero_object_00",
  "steps": [
    {
      "id": "locate-and-grasp",
      "goal": "localize and pick the visible source object",
      "skills": ["find_pixel", "grasp_object"],
      "depends_on": []
    }
  ]
}
```

`skills` is ordered. Visible recovery tools may be used between listed calls,
but the runtime advances the planner step only after the complete listed
sequence succeeds. The Reviewer receives the visible plan, summary, and tool
trace; simulator predicates, rewards, hidden object state, and the final
post-hoc verdict are removed from its packet.

Generate a standalone interactive tree from the retained plan and journal:

```bash
./roborsi visualize skill-tree \
  --run <run-id> \
  --task libero_object/0 \
  --output artifacts/libero-object-0-skill-tree.html \
  --no-browser
```

The timeline shows Task Family, Atomic Task, ordered planner steps, selected
Base/Compound Skills, final journal verdicts, release identities, and retained
promotion records. Omit `--no-browser` to open it directly.

### Resume and promotion invariants

- A simulator-confirmed successful task/seed pair is never rerun.
- Normal Pass@10 scheduling stops a task after its first successful seed.
- Candidate validation may run an unsolved holdout seed after another
  validation seed succeeds, without weakening successful-pair protection.
- Infrastructure records remain retryable and excluded from task denominators.
- Validation seed selection and candidate release identity are retained in the
  proposal JSON so an interrupted gate resumes exactly.
- The active `workspace/` changes only after both validation seeds pass;
  immutable promoted content is also copied under `releases/<release-id>/`.

This is a new stochastic experiment. It does not promise the historical
`95/120`, which accumulated across a previous adaptive release lineage.

## Track C: Run A Fixed-Release Campaign

```bash
./roborsi eval libero-short --mode fixed --dry-run
./roborsi eval libero-short --mode fixed
```

Fixed mode disables skill proposal and code-inspection tools, keeps one release
identity, and reports `task_level_fixed_pass_at_10`.

Do not merge fixed-release results with adaptive cross-release coverage.

## Setup Options

```text
./setup.sh --core-only        install replay, Web, and visualization tools
./setup.sh --core-only --dev  also install test and build tooling
./setup.sh                    install the complete LIBERO reference runtime
./setup.sh --dev              complete runtime plus development tooling
./setup.sh --python PATH      choose the Python used to create environments
```

## Configuration Overrides

Generate a configuration without running full setup:

```bash
./roborsi configure \
  --output roborsi.yaml \
  --base-url https://api.openai.com/v1 \
  --api-key-env OPENAI_API_KEY \
  --gpus auto \
  --workers 8 \
  --yes
```

Paths in a loaded YAML file resolve relative to that file. This makes copied
configurations portable across checkouts.

## Optional GraspGen Compatibility

The public default fits a bounded top-down candidate from the segmented point
cloud. Historical score-producing releases also used an external GraspGen ZMQ
server for selected shapes.

For closer compatibility:

1. Install the upstream revision:
   <https://github.com/NVlabs/GraspGen/tree/2dd8852e1be60f5f9d277fafcc621835cdf59110>.
2. Obtain its Franka checkpoint:
   <https://huggingface.co/adithyamurali/GraspGenModels>.
3. Start the ZMQ service on port `5556`.
4. Set `services.graspgen_port: 5556` in `roborsi.yaml`.

GraspGen is an external non-commercial dependency and is not installed by
RoboRSI.

## Resource Planning

| Track | API | Simulator | Typical scale |
| --- | --- | --- | --- |
| Evidence replay | None | None | Seconds |
| Web console or Skill Tree | None | None | Seconds |
| Adaptive or fixed preflight | None | Configuration only | Seconds |
| Full Pass@10 campaign | Many model calls | Up to 1,200 task/seed episodes | Hours to days |

Full-campaign cost depends on early task success, worker concurrency, provider
latency, controller horizon, and recovery length. Each episode records:

- prompt, completion, and total tokens;
- metered and unmetered VLM calls;
- wall time;
- VLM, perception, action, and recovery time.

Use retained episode records for cost analysis. Tool-call count alone is not a
Token or time estimate.

## Result Comparison Checklist

Before comparing two runs, match all of the following:

- the exact 120-task catalog;
- ordered seed set;
- adaptive versus fixed mode;
- model and reasoning effort;
- image size and horizon;
- controller and optional services;
- final simulator predicate as the success source;
- infrastructure exclusion;
- successful task/seed resume protection.

If any field differs, report the run as a separate experiment instead of
extending or overwriting an existing headline.

## Clean-Checkout Verification

Core-only verification:

```bash
./setup.sh --core-only --dev
source .venv/bin/activate

pytest -q -m "not runtime"
ruff check src/roborsi/libero tests scripts
python scripts/release_check.py
./reproduce.sh --skip-setup --output-dir /tmp/roborsi-reproduction
python -m build
```

Complete runtime verification:

```bash
./setup.sh --dev
source .venv/bin/activate
pytest -q
python scripts/check_libero_gt_leak.py
```

CI installs the complete runtime dependency set and executes both the full test
suite and the replay/build checks. All applicable commands must complete before
publishing a release.
