# Simulation backend: RoboTwin 2.0

RoboRSI treats RoboTwin as an external dependency — we don't pip-install
it. Its dep tree (SAPIEN, cuRobo, CUDA 12.1, Vulkan, PyTorch, pytorch3d) is
incompatible with a lightweight pip resolve and wants its own conda env.

## 1. Install RoboTwin

```bash
sudo apt install libvulkan1 mesa-vulkan-drivers vulkan-tools
git clone https://github.com/RoboTwin-Platform/RoboTwin.git $ROBORSI_ROBOTWIN_ROOT
cd $ROBORSI_ROBOTWIN_ROOT
conda create -n RoboTwin python=3.10 -y
conda activate RoboTwin
bash script/_install.sh
python script/update_embodiment_config_path.py
bash script/_download_assets.sh
```

Smoke test:

```bash
bash collect_data.sh beat_block_hammer demo_randomized 0 1
# → writes one episode.hdf5 under data/beat_block_hammer/
```

If SAPIEN segfaults on Vulkan: check `vulkaninfo` works; on headless boxes
you may need `apt install libnvidia-gl-<driver-version>`.

## 2. Point RoboRSI at it

Default path: `$ROBORSI_ROBOTWIN_ROOT`. Override with:

```bash
export ROBORSI_ROBOTWIN_ROOT=/path/to/RoboTwin
```

Then:

```bash
# Inside the RoboTwin conda env so SAPIEN/cuRobo/Torch resolve:
pip install -e /path/to/RoboRSI         # install the RoboRSI package itself

roborsi sim list              # robotwin should be 'available: yes'
roborsi sim tasks robotwin    # ~50 tasks
roborsi sim run beat_block_hammer --seed 0
```

## 3. Collect data via the skill

```bash
roborsi skill run beat_block_hammer \
    --params '{"mode":"collect","backend":"robotwin","episodes":5,"seed":0}'
```

Output lands under:

```
~/.roborsi/data/beat_block_hammer/<YYYYMMDD-HHMMSS-xxxxxx>/
    meta.json
    episode.parquet
    frames/<cam>/000000.jpg
```

## 4. Plan → run end-to-end

```bash
roborsi plan "敲击桌面上的方块"
roborsi run  "敲击桌面上的方块" --backend robotwin
```

The second command invokes the VLM Planner, picks `beat_block_hammer`,
and executes it with collect mode by default. Each produced episode's
`meta.json` embeds the planner trace in `plan_trace` for later Skill
Minter reuse.

## Known limitations

- `SimEnv.step(action)` is intentionally not implemented in this MVP.
  Closed-loop policy inference (π₀) will land in a follow-up slice; for
  now RoboTwin's own `play_once()` drives episodes from reset to done.
- Per-step observations only captured at start + end. RoboTwin's
  internal HDF5 recorder has full fidelity; a conversion path HDF5 →
  parquet will arrive alongside π₀ training.
- Running the CLI **outside** the RoboTwin conda env raises
  `ModuleNotFoundError: sapien`. That's on purpose — RoboRSI will not
  shadow the user's env with mock deps.
