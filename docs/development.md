# Development Guide

## Prerequisites

- Python 3.11+
- Git

## Local Setup

```bash
# Clone & install in editable mode with dev extras
git clone https://github.com/nssmd/robo-rsi.git
cd RoboRSI
pip install -e ".[dev]"

# First-time setup (creates ~/.roborsi/config.json & workspace)
roborsi onboard
```

## Running Tests

```bash
# Unit tests (no hardware required)
python -m pytest tests/ -x -q

# Skip PTY integration tests (useful in minimal CI environments)
python -m pytest tests/ -x -q -m "not pty"

# Run only PTY integration tests
python -m pytest tests/integration/ -x -q -m pty
```

## Stub Mode

Set `ROBORSI_STUB=1` to replace real hardware calls with deterministic
stubs.  This allows the full embodied pipeline (scan, identify, calibrate,
teleoperate, record) to run on a laptop without any robot arms or cameras.

```bash
# Run the agent in stub mode
ROBORSI_STUB=1 roborsi agent

# PTY integration tests use stub mode automatically
python -m pytest tests/integration/ -x -q -m pty
```

What gets stubbed:

| Component | Real behaviour | Stub behaviour |
|---|---|---|
| `scan_serial_ports()` | reads `/dev/serial/by-*` | returns 2 fake ports |
| `scan_cameras()` | probes `/dev/video*` via OpenCV | returns 1 fake camera |
| `probe_port()` | reads Feetech motor positions | returns motor IDs [1..6] |
| `read_positions()` | reads motor positions via SCS | returns all zeros |
| `run_interactive()` | spawns a subprocess | returns exit-code 0 immediately |
| `_find_moved_port()` | reads motor positions | picks scripted port |

All stub defaults are overridable via env vars for per-test flexibility:

| Variable | Default |
|---|---|
| `ROBORSI_STUB_PORTS` | 2 fake serial ports (JSON list) |
| `ROBORSI_STUB_CAMERAS` | 1 fake camera (JSON list) |
| `ROBORSI_STUB_MOTORS` | 6 motors per port (JSON object) |
| `ROBORSI_STUB_MOVED_PORT` | first port's by_id |

All stub logic lives in `roborsi/embodied/stub.py`.

## Workspace Reset

During development you may want to return to a clean state:

```bash
# Interactive (asks for confirmation)
roborsi dev reset

# Non-interactive
roborsi dev reset --yes

# Reset and configure a specific model
roborsi dev reset --yes --model openai/gpt-4o --api-key sk-...
```

This deletes `~/.roborsi/workspace` and `~/.roborsi/config.json`, then
re-runs `roborsi onboard` non-interactively.

## Environment Variables

| Variable | Purpose |
|---|---|
| `ROBORSI_HOME` | Override the base directory (default `~/.roborsi`). Useful for tests and parallel instances. |
| `ROBORSI_STUB` | Set to `1` to activate stub mode (fake hardware). |
| `ROBORSI_STUB_PORTS` | JSON list of fake serial ports (override defaults). |
| `ROBORSI_STUB_CAMERAS` | JSON list of fake cameras (override defaults). |
| `ROBORSI_STUB_MOTORS` | JSON object mapping port by_id → motor ids. |
| `ROBORSI_STUB_MOVED_PORT` | by_id of port that identify detects as moved. |

## Troubleshooting

### `ModuleNotFoundError: No module named 'lerobot'`

LeRobot is an optional dependency under the `research` extra:

```bash
pip install -e ".[research]"
```

### PTY tests fail with `ModuleNotFoundError: No module named 'pexpect'`

Install the dev extra:

```bash
pip install -e ".[dev]"
```

### Terminal messed up after Ctrl-C

Run `reset` in your shell to restore terminal settings.

### Tests fail with `roborsi.config` import errors

Make sure you installed in editable mode:

```bash
pip install -e ".[dev]"
```
