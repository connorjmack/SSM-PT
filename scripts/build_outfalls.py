"""Build data/outfalls.geojson for the plume tool from the marine-energy repo's outfall and candidate-site data.

Keeps points within --max-snap-m of an SSCOFS element (marine discharges; river and inland points drop out),
snaps each to its nearest element centre and records the snap distance, so users can see when a facility's
coordinates are the plant rather than the diffuser.
"""
import argparse
import csv
import json
from pathlib import Path

import netCDF4
import numpy as np
from pyproj import Transformer

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
TO_LONLAT = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)
MGD_TO_M3S = 0.0438126
DOMAIN = (-130.0, 44.0, -121.5, 52.5)  # generous lon/lat box around the SSCOFS mesh, to skip the rest of the US early
SKIP_CLASSES = {"", "brownfield"}  # brownfields are land parcels, not discharges


def read_points(repo: Path) -> list[dict]:
    pts = []
    potw = json.loads((repo / "data/water_infra/npdes_potw_outfalls.geojson").read_text())
    for f in potw["features"]:
        if not f["geometry"]:
            continue
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        flow = p.get("design_flow_mgd")
        pts.append({"name": p["facility_name"].title(), "kind": "Wastewater outfall (NPDES)", "lon": lon, "lat": lat,
                    "npdes_id": p["npdes_id"], "outfall_id": p.get("outfall_id"),
                    "flow_mgd": flow if flow and not p.get("flow_suspect") else None})
    with open(repo / "data/candidate_sites.csv") as fh:
        for r in csv.DictReader(fh):
            if r["process_class"] in SKIP_CLASSES or not r["latitude"]:
                continue
            flow = r["flow_mgd_design"] or r["flow_mgd_recent"]
            pts.append({"name": r["facility_name"], "kind": r["process_class"].replace("_", " ").capitalize(),
                        "lon": float(r["longitude"]), "lat": float(r["latitude"]), "npdes_id": r["npdes_ids"] or None,
                        "outfall_id": None, "flow_mgd": float(flow) if flow else None})
    w, s, e, n = DOMAIN
    return [p for p in pts if w <= p["lon"] <= e and s <= p["lat"] <= n]


def snap(pts: list[dict], mesh_file: Path, max_snap_m: float) -> list[dict]:
    """Nearest element centre to each point (search a +-max_snap_m slab of centres sorted by x)."""
    with netCDF4.Dataset(mesh_file) as nc:
        nc.set_auto_mask(False)
        xc, yc, h, nv = nc["xc"][:], nc["yc"][:], nc["h"][:], nc["nv"][:] - 1
    depth = h[nv].mean(axis=0)
    order = np.argsort(xc)
    xs = xc[order]
    kept, seen = [], set()
    for p in pts:
        x, y = TO_UTM.transform(p["lon"], p["lat"])
        lo, hi = np.searchsorted(xs, [x - max_snap_m, x + max_snap_m])
        if lo == hi:
            continue
        cand = order[lo:hi]
        d = np.hypot(xc[cand] - x, yc[cand] - y)
        k = int(np.argmin(d))
        if d[k] > max_snap_m:
            continue
        el = int(cand[k])
        key = (p["name"], el)
        if key in seen:  # the same facility listed twice at one place
            continue
        seen.add(key)
        lon, lat = TO_LONLAT.transform(xc[el], yc[el])
        kept.append(p | {"snap_m": round(float(d[k])), "depth_m": round(float(depth[el]), 1),
                         "flow_m3s": round(p["flow_mgd"] * MGD_TO_M3S, 4) if p["flow_mgd"] else None,
                         "listed_lon": p["lon"], "listed_lat": p["lat"], "lon": round(lon, 5), "lat": round(lat, 5)})
    return kept


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=Path.home() / "Documents/GitHub/marine-energy", type=Path)
    ap.add_argument("--mesh", default=None, type=Path, help="any SSCOFS file with xc/yc/nv/h; default: first in data/plume/davg")
    ap.add_argument("--max-snap-m", default=1000.0, type=float)
    ap.add_argument("--out", default="data/outfalls.geojson", type=Path)
    args = ap.parse_args()

    mesh = args.mesh or sorted(Path("data/plume/davg").glob("*.nc"))[0]
    pts = read_points(args.repo)
    kept = snap(pts, mesh, args.max_snap_m)
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [p.pop("lon"), p.pop("lat")]},
              "properties": p} for p in kept]
    args.out.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print(f"{len(pts)} points near the domain; {len(kept)} within {args.max_snap_m:.0f} m of the mesh -> {args.out}")


if __name__ == "__main__":
    main()
