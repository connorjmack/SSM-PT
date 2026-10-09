"""Fetch all 10 layers of SSCOFS fields from S3 and write hourly files for the plume tool.

Default: depth-averaged u/v in the slim2d layout (u, v as (time, nele)), so the tracker's SSCOFS2DReader reads
them unchanged. --clip: u, v, ww, temp and salinity on all layers inside the Sequim box (plan.md decision 16), for
near-field ambient profiles and the Phase C 3D runs. A clipped file holds the box's mesh, with nv renumbered to its
nodes, and node_index / nele_index pointing back into the full mesh.
"""
import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import h5netcdf.legacyapi as h5nc
import netCDF4
import numpy as np
import s3fs

from fetch_surface import nowcast_key
from phase0_slim import GRID_VARS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from ssm_pt.fields import depth_average  # noqa: E402
from ssm_pt.grid import clip_mesh  # noqa: E402

TIME_VARS = ["time", "zeta", "wet_cells"]
# Element centres in lon -123.20 to -122.85, lat 48.00 to 48.20: Dungeness Spit to Protection Island, Sequim Bay in
# the middle. About 10,500 elements; the box's mesh indices span two of the three element chunks, so a smaller box
# would download about as much
SEQUIM_BOX = (-123.20, -122.85, 48.00, 48.20)
CLIP_VARS = ["u", "v", "ww", "temp", "salinity"]  # (time, siglay, nele or node)


def read_clip_grid(key: str, box: tuple) -> dict:
    """The mesh of the elements whose centres lie in box (lon, lon, lat, lat), from one fields file: name -> (dims, values, attrs)."""
    x0, x1, y0, y1 = box
    fs = s3fs.S3FileSystem(anon=True)
    with fs.open(key, "rb", block_size=2**21, cache_type="blockcache") as f, h5nc.Dataset(f, "r") as s:
        lonc, latc = s["lonc"][:] - 360, s["latc"][:]  # SSCOFS longitudes are 0-360
        e = np.flatnonzero((lonc >= x0) & (lonc <= x1) & (latc >= y0) & (latc <= y1))
        n, nv = clip_mesh(s["nv"][:] - 1, e)
        grid = {
            "node_index": (("node",), n, {"long_name": "0-based index of the node in the full SSCOFS mesh"}),
            "nele_index": (("nele",), e, {"long_name": "0-based index of the element in the full SSCOFS mesh"}),
            "nv": (("three", "nele"), (nv + 1).astype(np.int32), {"long_name": "nodes surrounding element, 1-based, this file's nodes"}),
        }
        for name in GRID_VARS + ["siglay", "siglev"]:
            if name != "nv":
                sv = s[name]
                values = sv[:][..., e if "nele" in sv.dimensions else n]
                grid[name] = (sv.dimensions, values, {k: sv.getncattr(k) for k in sv.ncattrs() if k != "_FillValue"})
    return grid


def write_clip(s, dst: Path, grid: dict):
    """Write the box's fields from s, an open SSCOFS fields Dataset, atomically to dst; grid is from read_clip_grid."""
    e, n = grid["nele_index"][1], grid["node_index"][1]
    part = dst.with_suffix(".part")
    with netCDF4.Dataset(part, "w") as d:
        d.setncatts({k: s.getncattr(k) for k in ("title", "source", "CoordinateSystem", "CoordinateProjection")})
        d.setncattr("ssm_pt_note", f"elements with centres in lon {SEQUIM_BOX[:2]}, lat {SEQUIM_BOX[2:]}; "
                                   "node_index and nele_index give each node's and element's place in the full mesh")
        for name in ("time", "node", "nele", "three", "siglay", "siglev"):
            d.createDimension(name, {"time": None, "node": n.size, "nele": e.size}.get(name, len(s.dimensions[name])))
        for name, (dims, values, attrs) in grid.items():
            dv = d.createVariable(name, values.dtype.newbyteorder("="), dims, zlib=True, complevel=1)
            dv.setncatts(attrs)
            dv[:] = values
        for name in TIME_VARS + CLIP_VARS:
            sv = s[name]
            idx = e if "nele" in sv.dimensions else n if "node" in sv.dimensions else None
            # read one span covering the box, then pick its columns
            values = sv[:] if idx is None else sv[..., idx[0]:idx[-1] + 1][..., idx - idx[0]]
            dv = d.createVariable(name, sv.dtype.newbyteorder("="), sv.dimensions, zlib=True, complevel=1)
            dv.setncatts({k: sv.getncattr(k) for k in sv.ncattrs() if k != "_FillValue"})
            dv[:] = values
    part.rename(dst)


def write_davg(s, dst: Path):
    """Write the depth-averaged copy of s, an open SSCOFS fields Dataset, atomically to dst."""
    siglev = s["siglev"][:]
    if not np.allclose(siglev, siglev[:, :1]):
        raise ValueError("siglev differs between nodes; depth_average assumes one set of levels")
    part = dst.with_suffix(".part")
    with netCDF4.Dataset(part, "w") as d:
        d.setncatts({k: s.getncattr(k) for k in ("title", "source", "CoordinateSystem", "CoordinateProjection")})
        d.setncattr("ssm_pt_note", "u, v are depth averages of the 10 sigma layers, weighted by layer thickness")
        for name in ("time", "node", "nele", "three"):
            dim = s.dimensions[name]
            d.createDimension(name, None if dim.isunlimited() else len(dim))
        for name in GRID_VARS + TIME_VARS:
            sv = s[name]
            dv = d.createVariable(name, sv.dtype.newbyteorder("="), sv.dimensions, zlib=True, complevel=1)
            dv.setncatts({k: sv.getncattr(k) for k in sv.ncattrs() if k != "_FillValue"})
            dv[:] = sv[:]
        for name in ("u", "v"):
            sv = s[name]
            dv = d.createVariable(name, np.float32, ("time", "nele"), zlib=True, complevel=1)
            dv.setncatts({k: sv.getncattr(k) for k in sv.ncattrs() if k != "_FillValue"})
            dv[:] = depth_average(sv[:], siglev[:, 0]).astype(np.float32)
    part.rename(dst)


def fetch_hour(t: datetime, out: Path, grid: dict | None = None) -> str:
    key, name = nowcast_key(t)
    dest = out / name
    if dest.exists():
        return f"skip {name}"
    fs = s3fs.S3FileSystem(anon=True)
    if not fs.exists(key):
        return f"MISSING {name}"
    t0 = time.time()
    with fs.open(key, "rb", block_size=2**21, cache_type="blockcache") as f, h5nc.Dataset(f, "r") as s:
        if grid is None:
            write_davg(s, dest)
        else:
            write_clip(s, dest, grid)
    return f"got  {name}  ({t:%Y-%m-%d %H}Z, {time.time() - t0:.0f} s)"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True, help="first valid hour, UTC, e.g. 2026-07-01T00:00")
    p.add_argument("--end", required=True, help="last valid hour, UTC")
    p.add_argument("--clip", action="store_true", help="write the Sequim-box 3D files instead of depth averages")
    p.add_argument("--out", type=Path, help="default data/plume/davg, or data/plume/sequim3d with --clip")
    p.add_argument("--workers", default=4, type=int)
    args = p.parse_args()

    out = args.out or Path("data/plume/sequim3d" if args.clip else "data/plume/davg")
    out.mkdir(parents=True, exist_ok=True)
    t, end, hours = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end), []
    while t <= end:
        hours.append(t)
        t += timedelta(hours=1)
    grid = read_clip_grid(nowcast_key(hours[0])[0], SEQUIM_BOX) if args.clip else None
    missing = 0
    with ProcessPoolExecutor(args.workers) as pool:
        for f in as_completed(pool.submit(fetch_hour, h, out, grid) for h in hours):
            msg = f.result()
            missing += msg.startswith("MISSING")
            print(msg, flush=True)
    if missing:
        print(f"{missing} hours missing on S3; the run window will have gaps")


if __name__ == "__main__":
    main()
