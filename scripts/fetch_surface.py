"""Fetch slim surface-only SSCOFS nowcast files straight from S3, reading only the chunks they need."""
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import h5netcdf.legacyapi as h5nc
import s3fs

from phase0_slim import add_vars, missing_vars, slim

BUCKET = "noaa-nos-ofs-pds/sscofs/netcdf"


def nowcast_key(t: datetime) -> tuple[str, str]:
    """(S3 key, file name) of the nowcast file valid at hour t (UTC).

    Cycles run at 03, 09, 15 and 21Z; n001-n006 are valid cycle-5h .. cycle, so 22-23Z
    come from the next day's t03z. To be replaced by ssm_pt.catalog (Phase 1).
    """
    ahead = (3 - t.hour) % 6
    c = t + timedelta(hours=ahead)
    name = f"sscofs.t{c:%H}z.{c:%Y%m%d}.fields.n{6 - ahead:03d}.nc"
    return f"{BUCKET}/{c:%Y/%m/%d}/{name}", name


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", required=True, help="first valid hour, UTC, e.g. 2026-09-30T16:00")
    p.add_argument("--end", required=True, help="last valid hour, UTC")
    p.add_argument("--out", default="data/phase0/slim2d", type=Path)
    args = p.parse_args()

    fs = s3fs.S3FileSystem(anon=True)
    args.out.mkdir(parents=True, exist_ok=True)
    t, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    missing = []
    while t <= end:
        key, name = nowcast_key(t)
        dest = args.out / name
        add = missing_vars(dest) if dest.exists() else None  # files from before a variable was added
        if add == []:
            print(f"skip {name}")
        elif not fs.exists(key):
            missing.append(name)
            print(f"MISSING {name}")
        else:
            # 2 MB blocks fetch ~48 MB of the 211 MB file: u/v are stored in 4-layer chunks
            with fs.open(key, "rb", block_size=2**21, cache_type="blockcache") as f, h5nc.Dataset(f, "r") as s:
                if add:
                    add_vars(s, dest, add)
                else:
                    slim(s, dest)
            print(f"{'added ' + ', '.join(add) + ' to' if add else 'got'}  {name}  ({t:%Y-%m-%d %H}Z)")
        t += timedelta(hours=1)
    if missing:
        print(f"{len(missing)} hours missing on S3; the run window will have gaps: {missing}")


if __name__ == "__main__":
    main()
