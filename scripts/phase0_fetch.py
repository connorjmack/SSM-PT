"""Phase 0: download full SSCOFS nowcast fields files for the OceanTracker go/no-go test."""
import argparse
from pathlib import Path

import s3fs

BUCKET = "noaa-nos-ofs-pds/sscofs/netcdf"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", default="20261004", help="cycle date YYYYMMDD")
    p.add_argument("--cycle", default="09", help="cycle hour (03, 09, 15, 21)")
    p.add_argument("--out", default="data/phase0/full", type=Path)
    args = p.parse_args()

    fs = s3fs.S3FileSystem(anon=True)
    args.out.mkdir(parents=True, exist_ok=True)
    d = args.date
    for k in range(1, 7):  # n001-n006 = cycle-5h .. cycle hour
        name = f"sscofs.t{args.cycle}z.{d}.fields.n{k:03d}.nc"
        key = f"{BUCKET}/{d[:4]}/{d[4:6]}/{d[6:]}/{name}"
        dest = args.out / name
        if dest.exists() and dest.stat().st_size == fs.size(key):
            print(f"skip {name}")
            continue
        tmp = dest.with_suffix(".part")
        fs.get(key, str(tmp))
        tmp.rename(dest)
        print(f"got  {name}")


if __name__ == "__main__":
    main()
