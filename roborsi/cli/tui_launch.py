"""``roborsi tui`` launcher — open the Ink Manager cockpit.

Standalone + stdlib-only ON PURPOSE: the top-level Typer app (commands.py) pulls
in sub-apps that require Python 3.11 (StrEnum), but the RoboTwin runtime is 3.10.
This module imports nothing heavy, so the bare `roborsi` / `roborsi tui`
dispatch path works on 3.10 — exactly like `roborsi chat`.

It ensures the packaged dashboard server is up, then hands the terminal to the
Ink UI (roborsi/frontend/tui). node (>=18) is required.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TUI_DIR = REPO / "roborsi" / "frontend" / "tui"


def _bridge_up(host: str, port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://{host}:{port}/data.json", timeout=2)
        return True
    except Exception:
        return False


def launch(host: str = "127.0.0.1", port: int = 8791, no_bridge: bool = False) -> None:
    if shutil.which("node") is None:
        sys.exit("[roborsi] 需要 node(>=18) 才能开 TUI 驾驶舱 — 请先安装 node。")
    if not (TUI_DIR / "node_modules").exists():
        print("[roborsi] 首次运行：安装 TUI 依赖（npm install）…")
        subprocess.run(["npm", "install"], cwd=TUI_DIR, check=True)

    bridge = None
    if not no_bridge and not _bridge_up(host, port):
        print(f"[roborsi] 启动 Manager 桥接 :{port} …")
        bridge = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "roborsi.embodied.board.web.server",
                "--host",
                host,
                "--evo-port",
                str(port),
                "--cockpit-port",
                "0",
            ],
            cwd=REPO,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(20):
            if _bridge_up(host, port):
                break
            time.sleep(0.5)
    try:
        subprocess.run(
            ["node", str(TUI_DIR / "bin" / "roborsi-tui.mjs"),
             "--host", host, "--port", str(port)], cwd=TUI_DIR)
    finally:
        if bridge is not None:
            bridge.terminate()


def main() -> None:
    ap = argparse.ArgumentParser(prog="roborsi tui",
                                 description="Open the RoboRSI Manager cockpit (Ink terminal UI).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--no-bridge", action="store_true",
                    help="Don't auto-start the bridge (connect to an existing one).")
    args = ap.parse_args()
    launch(host=args.host, port=args.port, no_bridge=args.no_bridge)


if __name__ == "__main__":
    main()
