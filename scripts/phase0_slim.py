"""Phase 0: write slimmed 2D surface-only copies of SSCOFS fields files (candidate cache format)."""
import argparse
import shutil
from pathlib import Path

import netCDF4

GRID_VARS = ["x", "y", "xc", "yc", "lon", "lat", "lonc", "latc", "nv", "h"]
TIME_VARS = ["time", "zeta", "wet_cells", "uwind_speed", "vwind_speed"]  # wind (10 m, elements) for windage
SURFACE_VARS = ["u", "v"]  # written as (time, nele) from siglay=0


def slim(s, dst: Path):
    """Write the slim copy of s, an open netCDF4 or h5netcdf.legacyapi Dataset (local file or S3 stream)."""
    with netCDF4.Dataset(dst.with_suffix(".part"), "w") as d:
        d.setncatts({k: s.getncattr(k) for k in ("title", "source", "CoordinateSystem", "CoordinateProjection")})
        for name in ("time", "node", "nele", "three"):
            dim = s.dimensions[name]
            d.createDimension(name, None if dim.isunlimited() else len(dim))
        for name in GRID_VARS + TIME_VARS:
            _copy(s, d, name)
        for name in SURFACE_VARS:
            sv = s[name]
            dv = d.createVariable(name, sv.dtype.newbyteorder("="), ("time", "nele"))
            dv.setncatts({k: sv.getncattr(k) for k in sv.ncattrs() if k != "_FillValue"})
            dv[:] = sv[:, 0, :]
    dst.with_suffix(".part").rename(dst)


def _copy(s, d, name: str):
    sv = s[name]
    dv = d.createVariable(name, sv.dtype.newbyteorder("="), sv.dimensions)
    dv.setncatts({k: sv.getncattr(k) for k in sv.ncattrs() if k != "_FillValue"})
    dv[:] = sv[:]


def missing_vars(dst: Path) -> list[str]:
    """Grid and time variables a slim copy lacks, e.g. wind in files written before it was added."""
    with netCDF4.Dataset(dst) as d:
        return [n for n in GRID_VARS + TIME_VARS if n not in d.variables]


def add_vars(s, dst: Path, names: list[str]):
    """Copy the named variables from s into the existing slim copy dst (via a temporary copy, so a failure leaves dst intact)."""
    part = dst.with_suffix(".part")
    shutil.copyfile(dst, part)
    with netCDF4.Dataset(part, "a") as d:
        for name in names:
            _copy(s, d, name)
    part.rename(dst)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--src", default="data/phase0/full", type=Path)
    p.add_argument("--dst", default="data/phase0/slim2d", type=Path)
    args = p.parse_args()
    args.dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(args.src.glob("sscofs.*.fields.n*.nc")):
        with netCDF4.Dataset(f) as s:
            slim(s, args.dst / f.name)
        print(f"{f.name}: {(args.dst / f.name).stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
