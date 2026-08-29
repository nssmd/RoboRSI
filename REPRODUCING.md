# Reproducing roborsi LIBERO

## Three Separate Tracks

### 1. Reported-result replay

This is the exact, inexpensive check for the public headline:

```bash
./setup.sh --core-only
./roborsi results replay \
  --manifest evidence/adaptive-pass10-v1/manifest.json \
  --json replay.json
```

Expected output:

```text
Spatial 9/10, Object 10/10, Goal 9/10, LIBERO-90 67/90
Total 95/120 = 79.2%
```

The bundle is compacted to one canonical native-success row per solved task.
It proves the reported task coverage and provenance fields. It does not contain
all failed/infra rows, so its token/time aggregate is not total campaign spend.

### 2. New adaptive campaign

```bash
export OPENAI_API_KEY="..."
./setup.sh
./roborsi eval libero-short --mode adaptive
```

For each ordered seed, the supervisor schedules only unsolved tasks. Visible
failures may produce complete skill proposals. A proposal is staged in a
campaign overlay, scanned for hidden simulator input, and rerun on the same
task/seed as a changed-path harness. Only native simulator success promotes the
overlay for later tasks. Rejected candidate code and its failed run remain on
disk.

This is a stochastic new experiment. It does not promise the historical
`95/120`, because the reported score accumulated across an earlier release
lineage, provider windows, and optional perception services.

### 3. Fixed-release campaign

```bash
./roborsi eval libero-short --mode fixed
```

Fixed mode disables code inspection and proposal tools, keeps one release ID,
and reports `task_level_fixed_pass_at_10`. Do not merge it with adaptive
coverage.

## Optional GraspGen Compatibility

The public default works without GraspGen by fitting a top-down candidate from
the segmented point cloud. Historical score-producing releases also used an
external GraspGen ZMQ server for some shapes.

For closer execution compatibility:

1. Follow the upstream installation at
   <https://github.com/NVlabs/GraspGen/tree/2dd8852e1be60f5f9d277fafcc621835cdf59110>.
2. Download the Franka checkpoint from
   <https://huggingface.co/adithyamurali/GraspGenModels>.
3. Start its ZMQ server on port `5556`.
4. Set `services.graspgen_port: 5556` in `roborsi.yaml`.

GraspGen is an external non-commercial dependency and is not installed by
roborsi.

## Resource Planning

| Path | API | Simulator/GPU | Typical duration |
|---|---|---|---|
| Evidence replay | none | none | seconds |
| One-task smoke | several VLM calls | one simulator worker | minutes |
| Full Pass@10 | potentially billions of tokens | up to 1,200 task/seed episodes | hours to days |

Actual cost depends on early task success, worker concurrency, provider
latency, and recovery length. Each episode records prompt/completion/total
tokens, metered and unmetered calls, wall time, and phase timing. Use those
records rather than estimating cost from tool-call counts.

## Comparing Results

Every comparison must match:

- task catalog and ordered seed set;
- adaptive versus fixed mode;
- model and reasoning effort;
- image size, horizon, controller, and optional services;
- final simulator predicate as the only success source;
- infrastructure exclusion and successful-seed protection.

If any field differs, report the run as a separate release rather than
overwriting or extending the public headline.
