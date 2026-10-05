"""Phase 0: write slimmed 2D surface-only copies of SSCOFS fields files (candidate cache format)."""
import argparse
from pathlib import Path

import netCDF4

GRID_VARS = ["x", "y", "xc", "yc", "lon", "lat", "lonc", "latc", "nv", "h"]
TIME_VARS = ["time", "zeta", "wet_cells"]
SURFACE_VARS = ["u", "v"]  # written as (time, nele) from siglay=0


def slim(src: Path, dst: Path):
    with netCDF4.Dataset(src) as s, netCDF4.Dataset(dst.with_suffix(".part"), "w") as d:
        d.setncatts({k: s.getncattr(k) for k in ("title", "source", "CoordinateSystem", "CoordinateProjection")})
        for name in ("time", "node", "nele", "three"):
            dim = s.dimensions[name]
            d.createDimension(name, None if dim.isunlimited() else len(dim))
        for name in GRID_VARS + TIME_VARS:
            sv = s[name]
            dv = d.createVariable(name, sv.dtype, sv.dimensions)
            dv.setncatts({k: sv.getncattr(k) for k in sv.ncattrs() if k != "_FillValue"})
            dv[:] = sv[:]
        for name in SURFACE_VARS:
            sv = s[name]
            dv = d.createVariable(name, sv.dtype, ("time", "nele"))
            dv.setncatts({k: sv.getncattr(k) for k in sv.ncattrs() if k != "_FillValue"})
            dv[:] = sv[:, 0, :]
    dst.with_suffix(".part").rename(dst)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--src", default="data/phase0/full", type=Path)
    p.add_argument("--dst", default="data/phase0/slim2d", type=Path)
    args = p.parse_args()
    args.dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(args.src.glob("sscofs.*.fields.n*.nc")):
        slim(f, args.dst / f.name)
        print(f"{f.name}: {(args.dst / f.name).stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
