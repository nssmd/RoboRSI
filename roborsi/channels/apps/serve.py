"""Start the Manager on one or more platforms.

    python -m roborsi.channels.apps.serve --list
    python -m roborsi.channels.apps.serve cli
    python -m roborsi.channels.apps.serve telegram web

Several platforms run at once, each on its own thread, all feeding the same
Manager — which is the point of the layering: whoever is talking to the system
is talking to the Manager, and the transport is not supposed to change what it
is or what it remembers.
"""

from __future__ import annotations

import argparse
import threading

from ..core.manager import Manager
from ..core.registry import load_builtin_platforms, registry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("platforms", nargs="*", default=[], help="cli / feishu / telegram / web")
    ap.add_argument("--list", action="store_true", help="show platforms and readiness")
    args = ap.parse_args()

    load_builtin_platforms()

    if args.list or not args.platforms:
        print(f"{'平台':12s} {'状态':12s} {'卡片':5s} {'文件':5s}  环境变量")
        for row in registry.table():
            print(f"{row['name']:12s} {row['status']:12s} "
                  f"{'✓' if row['cards'] else '-':5s} {'✓' if row['files'] else '-':5s}  "
                  f"{', '.join(row['env']) or '-'}")
        return 0

    unknown = [p for p in args.platforms if registry.get(p) is None]
    if unknown:
        print(f"未知平台 {unknown};可用: {registry.names()}")
        return 1

    adapters = []
    for name in args.platforms:
        adapter = registry.create(name)
        # Each adapter is its own OutboundPort, so a reply goes back the way it
        # came in rather than to whichever platform happened to start first.
        adapters.append((name, adapter, Manager(outbound=adapter)))

    if len(adapters) == 1:
        name, adapter, manager = adapters[0]
        adapter.run(manager)
        return 0

    threads = []
    for name, adapter, manager in adapters:
        t = threading.Thread(target=adapter.run, args=(manager,),
                             name=f"channel-{name}", daemon=True)
        t.start()
        threads.append(t)
        print(f"[started] {name}", flush=True)
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        for _, adapter, _ in adapters:
            adapter.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
