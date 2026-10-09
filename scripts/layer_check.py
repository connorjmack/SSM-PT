"""3D layer check (plan.md Risks, Phase C gate): do OceanTracker's 3D tracks follow each layer's current?

Particles held at fixed depths in the Sequim box are tracked by OceanTracker with its stock FVCOMreader and with
our SSCOFS3DReader, and by direct integration of the SSCOFS layer velocities (nearest element, linear between
layer centres at the particle's sigma, linear in time, RK2). The vertical velocity is zeroed in a copy of the
files so particles stay at their depth; the tide is kept. Direct integration with the layers reversed shows what
a layer-order bug would give.
"""
import argparse
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("OCEANTRACKER_NUMBA_CACHING", "1")  # must be set before oceantracker import

import netCDF4
import numpy as np

from ssm_pt.engine.oceantracker_engine import TO_UTM
from ssm_pt.engine.plume import valid_hour, window_files

# (name, lon, lat, depths below mean sea level in m)
SITES = [
    ("Strait off Dungeness, 98 m", -123.05, 48.17, [2, 30, 80]),
    ("Strait, 15 m", -123.10, 48.16, [2, 12]),
    ("Sequim Bay mouth, 12 m", -123.035, 48.085, [2, 9]),
]
READERS = {"stock": "FVCOMreader", "fixed": "ssm_pt.engine.oceantracker_engine.SSCOFS3DReader"}


def copy_without_ww(files, dst):
    dst.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy(f, dst / f.name)
        with netCDF4.Dataset(dst / f.name, "a") as nc:
            nc["ww"][:] = 0


def oceantracker_tracks(hindcast, reader, points, out_dir, hours, dt):
    from oceantracker.main import OceanTracker
    from oceantracker.read_output.python import load_output_files

    ot = OceanTracker()
    ot.settings(run_output_dir=str(out_dir), time_step=dt, NUMBA_cache_code=True, max_run_duration=hours * 3600)
    ot.add_class("reader", class_name=reader, input_dir=str(hindcast), file_mask="sscofs.*.nc", geographic_coords=False)
    ot.add_class("dispersion", A_H=0.0, A_V=0.0)
    ot.add_class("tracks_writer", update_interval=dt)
    for i, p in enumerate(points):
        ot.add_class("release_groups", name=f"p{i}", points=[p], pulse_size=1)
    return load_output_files.load_track_data(ot.run())["x"]  # (time, particle, xyz)


class LayerIntegrator:
    """Direct integration of the files' layer velocities, element-centred, for particles at fixed z."""

    def __init__(self, files, reverse=False):
        with netCDF4.Dataset(files[0]) as nc:
            nc.set_auto_mask(False)
            nv = nc["nv"][:] - 1
            self.xc, self.yc = nc["xc"][:], nc["yc"][:]
            self.h = nc["h"][:][nv].mean(axis=0)
            self.frac = 1 + nc["siglay"][:][:, nv].mean(axis=1).T  # (nele, layer) height above the bed / depth
        self.t = np.array([valid_hour(f.name).timestamp() for f in files])
        u, v, zeta = [], [], []
        for f in files:
            with netCDF4.Dataset(f) as nc:
                nc.set_auto_mask(False)
                u.append(nc["u"][0].T), v.append(nc["v"][0].T), zeta.append(nc["zeta"][0][nv].mean(axis=0))
        self.u, self.v, self.zeta = np.array(u), np.array(v), np.array(zeta)  # (time, nele, layer), (time, nele)
        if reverse:
            self.u, self.v = self.u[:, :, ::-1], self.v[:, :, ::-1]

    def velocity(self, x, y, z, t):
        k = np.argmin((self.xc - x) ** 2 + (self.yc - y) ** 2)
        i = int(np.clip(np.searchsorted(self.t, t) - 1, 0, len(self.t) - 2))
        w = (t - self.t[i]) / (self.t[i + 1] - self.t[i])
        zeta = (1 - w) * self.zeta[i, k] + w * self.zeta[i + 1, k]
        f = (z + self.h[k]) / (self.h[k] + zeta)  # height above the bed as a fraction of the water column
        fr = self.frac[k, ::-1]  # ascending for np.interp; values beyond the end layers are held
        return [float(np.interp(f, fr, (1 - w) * a[i, k, ::-1] + w * a[i + 1, k, ::-1])) for a in (self.u, self.v)]

    def track(self, x, y, z, t0, hours, dt):
        p = np.array([x, y], float)
        for n in range(int(hours * 3600 / dt)):
            t = t0 + n * dt
            k1 = np.array(self.velocity(*p, z, t))
            p = p + dt * np.array(self.velocity(*(p + 0.5 * dt * k1), z, t + 0.5 * dt))
        return p


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=Path("data/plume/sequim3d"))
    ap.add_argument("--out", type=Path, default=Path("runs/layer_check"))
    ap.add_argument("--start", default="2026-07-01T02:00", help="UTC; the run starts an hour after the first file")
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--dt", type=float, default=60.0)
    a = ap.parse_args()

    start = datetime.fromisoformat(a.start).replace(tzinfo=UTC)
    files = window_files(a.data, start, start + timedelta(hours=a.hours))
    t0 = valid_hour(files[0].name).timestamp()  # OceanTracker starts at the first file
    hindcast = a.out / "hindcast"
    shutil.rmtree(a.out, ignore_errors=True)
    copy_without_ww(files, hindcast)

    points = [(name, d, [*TO_UTM.transform(lon, lat), -float(d)]) for name, lon, lat, depths in SITES for d in depths]
    ends = {k: oceantracker_tracks(hindcast, r, [p for *_, p in points], a.out / k, a.hours, a.dt)[-1]
            for k, r in READERS.items()}
    direct, reversed_ = LayerIntegrator(files), LayerIntegrator(files, reverse=True)

    print(f"\n{a.hours:g} h from {datetime.fromtimestamp(t0, UTC):%Y-%m-%d %H:%M} UTC, dt {a.dt:g} s. "
          "Distance moved (m), and how far each OceanTracker end point is from the direct one (m):")
    print(f"{'site':28s} {'depth':>5s} {'direct':>7s} {'reversed':>8s} {'stock':>7s} {'fixed':>7s} {'stock off':>9s} {'fixed off':>9s}")
    for i, (name, d, (x, y, z)) in enumerate(points):
        e = direct.track(x, y, z, t0, a.hours, a.dt)
        r = reversed_.track(x, y, z, t0, a.hours, a.dt)
        s, f = ends["stock"][i, :2], ends["fixed"][i, :2]
        dist = lambda q: np.hypot(*(q - [x, y]))
        print(f"{name:28s} {d:5.0f} {dist(e):7.0f} {dist(r):8.0f} {dist(s):7.0f} {dist(f):7.0f} "
              f"{np.hypot(*(s - e)):9.0f} {np.hypot(*(f - e)):9.0f}")


if __name__ == "__main__":
    main()
