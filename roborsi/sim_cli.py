"""Sim-only CLI entry: ``python -m roborsi.sim_cli``.

Used inside the RoboTwin conda env (Python 3.10) where importing the full
roborsi CLI breaks on 3.11+ syntax (StrEnum, ``type X = …``) coming from
hardware/embodiment modules. This entry only wires the sim-relevant
sub-apps:

  sim · skill · task · farm · plan · run

It deliberately does NOT import flexiv / camera / channels / agent loops.
"""

from __future__ import annotations

import typer

from roborsi.cli.skill import skill_app
from roborsi.cli.sim import sim_app
from roborsi.cli.task import task_app
from roborsi.cli.farm import farm_app
from roborsi.cli.skill_tiers import base_app, atomic_app, long_horizon_app
from roborsi.cli.bench import bench_app
from roborsi.cli.bench_lh import bench_lh_app
from roborsi.cli.selfevo import selfevo_app


app = typer.Typer(
    name="roborsi-sim",
    help="RoboRSI — sim-only entry (RoboTwin env).",
    no_args_is_help=True,
)
app.add_typer(skill_app, name="skill")
app.add_typer(sim_app, name="sim")
app.add_typer(task_app, name="task")
app.add_typer(farm_app, name="farm")
app.add_typer(base_app, name="base")
app.add_typer(atomic_app, name="atomic")
app.add_typer(long_horizon_app, name="long-horizon")
app.add_typer(bench_app, name="bench")
app.add_typer(bench_lh_app, name="bench-lh")
app.add_typer(selfevo_app, name="selfevo")


if __name__ == "__main__":
    app()
