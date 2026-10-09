"""Does the plume depend on the time step? Runs one 3-day PNNL-Sequim plume at several dt and compares the
time-mean effluent fraction with the first run. Repeating the first dt shows the particle noise floor.
Most of a plume run's time is a fixed cost per step, so dt sets the run time (plan.md decision 16)."""
import argparse
import time
from pathlib import Path

import netCDF4
import numpy as np

from ssm_pt.engine.plume import PlumeEngine, PlumeRequest


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=Path("data/plume/davg"))
    ap.add_argument("--out", type=Path, default=Path("runs/dt_check"))
    ap.add_argument("--dt", type=int, nargs="+", default=[60, 60, 120, 300])
    a = ap.parse_args()
    req = PlumeRequest(sources=[dict(name="PNNL-Sequim MCRL", lon=-123.0438, lat=48.0793, flow_m3s=0.1)],
                       start="2026-07-01T00:00:00Z", duration_h=72, n_particles=20000)
    ref, rows = None, []
    for i, dt in enumerate(a.dt):
        out = a.out / f"{i}_dt{dt:g}"
        t = time.time()
        meta = PlumeEngine(a.data).run(req.model_copy(update={"dt_s": dt}), out)
        wall = time.time() - t
        with netCDF4.Dataset(out / "plume.nc") as nc:
            mean = nc["fraction_mean"][:].sum(axis=0)
        ref = mean if ref is None else ref
        rows.append((dt, wall, meta["min_dilution"], meta["on_grid_pct"][-1], 100 * np.abs(mean - ref).sum() / ref.sum()))
    print("dt_s  wall_s  min_dilution  on_grid_end_pct  mean_field_diff_pct")
    for r in rows:
        print("{:4g}  {:6.1f}  {:12.0f}  {:15.1f}  {:19.1f}".format(*r))


if __name__ == "__main__":
    main()
