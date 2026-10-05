"""Phase 0: run OceanTracker on SSCOFS files and print timing and track summaries."""
import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("OCEANTRACKER_NUMBA_CACHING", "1")  # must be set before oceantracker import

import netCDF4
import numpy as np
from pyproj import Transformer

from oceantracker.main import OceanTracker
from oceantracker.read_output.python import load_output_files

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)

# (name, lon, lat, release radius m)
SITES = [
    ("deception_pass", -122.643, 48.4065, 50.0),
    ("admiralty_inlet", -122.645, 48.030, 200.0),
    ("open_water_main_basin", -122.450, 47.650, 200.0),
]


def shelf_site(nc_path):
    """Release point ~0.15 deg inside the western open boundary at 48N."""
    with netCDF4.Dataset(nc_path) as nc:
        lon = nc["lon"][:] - 360.0
        lat = nc["lat"][:]
    band = np.abs(lat - 48.0) < 0.05
    return ("near_open_boundary", float(lon[band].min()) + 0.15, 48.0, 500.0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input-dir", default="data/phase0/full")
    p.add_argument("--file-mask", default="sscofs.*.fields.n*.nc")
    p.add_argument("--tag", default="full3d")
    p.add_argument("--reader", default="FVCOMreader", help="reader class_name")
    p.add_argument("--n", type=int, default=100, help="particles per site")
    p.add_argument("--dt", type=float, default=120.0, help="time step, s")
    p.add_argument("--A-H", type=float, default=1.0, help="horizontal diffusivity, m2/s")
    p.add_argument("--surface-float", action=argparse.BooleanOptionalAction, default=True,
                   help="3D runs: hold particles at the free surface (must be off for 2D files)")
    p.add_argument("--track-interval", type=float, default=600.0, help="track output interval, s")
    args = p.parse_args()

    files = sorted(Path(args.input_dir).glob(args.file_mask))
    sites = SITES + [shelf_site(files[0])]
    out_dir = Path("runs/phase0") / args.tag

    ot = OceanTracker()
    ot.settings(run_output_dir=str(out_dir), time_step=args.dt, use_random_seed=True,
                NUMBA_cache_code=True, write_tracks=True, add_path=[str(Path(__file__).parent)])
    ot.add_class("reader", class_name=args.reader, input_dir=args.input_dir,
                 file_mask=args.file_mask, geographic_coords=False)
    ot.add_class("dispersion", A_H=args.A_H)
    ot.add_class("tracks_writer", update_interval=args.track_interval)
    for name, lon, lat, radius in sites:
        x, y = TO_UTM.transform(lon, lat)
        ot.add_class("release_groups", name=name, points=[[x, y]], pulse_size=args.n,
                     release_radius=radius, release_at_surface=args.surface_float)
    if args.surface_float:
        ot.add_class("trajectory_modifiers", class_name="SurfaceFloat", name="surface_float")

    t0 = time.time()
    case_info = ot.run()
    wall = time.time() - t0

    tracks = load_output_files.load_track_data(case_info)
    x = tracks["x"]  # (time, particle, xyz)
    print(f"\nwall time {wall:.1f} s; track keys: {sorted(tracks)}")
    print(f"x shape {x.shape}, time steps written {tracks['time'].size}")
    status = tracks["status"]
    gid = tracks["IDrelease_group"]
    for g, (name, *_ ) in enumerate(sites):
        sel = gid == g
        disp = np.hypot(*(x[-1, sel, :2] - x[0, sel, :2]).T)
        codes, counts = np.unique(status[-1, sel], return_counts=True)
        print(f"{name:24s} n={sel.sum():4d} final displacement km "
              f"median={np.nanmedian(disp)/1e3:6.2f} max={np.nanmax(disp)/1e3:6.2f} "
              f"final status {dict(zip(codes.tolist(), counts.tolist()))}")
    (out_dir / "phase0_summary.json").write_text(json.dumps(
        {"wall_s": wall, "case_info": str(case_info), "sites": sites, "args": vars(args)}, indent=1))


if __name__ == "__main__":
    main()
