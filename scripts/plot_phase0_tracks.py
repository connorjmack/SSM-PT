"""Plot Phase 0 OceanTracker tracks per release site over the SSCOFS mesh shoreline."""
import argparse
import glob
from datetime import datetime
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import netCDF4
import numpy as np
from matplotlib.collections import LineCollection
from pyproj import Transformer

from oceantracker.read_output.python import load_output_files

mpl.rcParams.update({
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "savefig.dpi": 300,
    "figure.constrained_layout.use": True,
    "font.size": 8,
})
TO_LONLAT = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)


def save_figure(fig, outdir: Path, name: str, ext: str = "pdf") -> Path:
    """Write a timestamped figure and repoint latest.{ext} at it."""
    outdir.mkdir(parents=True, exist_ok=True)
    stamped = outdir / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.{ext}"
    fig.savefig(stamped, bbox_inches="tight")
    latest = outdir / f"latest.{ext}"
    latest.unlink(missing_ok=True)
    latest.symlink_to(stamped.name)
    return stamped


def shoreline_segments(grid_file):
    """Mesh boundary edges (edges used by exactly one triangle) as lon/lat segments."""
    with netCDF4.Dataset(grid_file) as nc:
        nv = nc["nv"][:].T - 1
        lon = nc["lon"][:] - 360.0
        lat = nc["lat"][:]
    edges = np.sort(np.concatenate([nv[:, [0, 1]], nv[:, [1, 2]], nv[:, [2, 0]]]), axis=1)
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    b = uniq[counts == 1]
    return np.stack([np.stack([lon[b[:, 0]], lat[b[:, 0]]], 1), np.stack([lon[b[:, 1]], lat[b[:, 1]]], 1)], 1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", default="runs/phase0/slim2d", type=Path)
    p.add_argument("--grid-file", default=None, help="SSCOFS file with nv/lon/lat (default: first in data/phase0/slim2d)")
    args = p.parse_args()
    grid_file = args.grid_file or sorted(glob.glob("data/phase0/slim2d/*.nc"))[0]

    t = load_output_files.load_track_data(str(next(args.run.glob("*caseInfo.json"))))
    x, gid = t["x"], t["IDrelease_group"]
    hours = (t["time"] - t["time"][0]) / 3600.0
    lon, lat = TO_LONLAT.transform(x[:, :, 0], x[:, :, 1])
    shore = shoreline_segments(grid_file)
    norm = mpl.colors.Normalize(0, hours[-1])

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.4))
    for g, ax in enumerate(axes.flat):
        sel = gid == g
        lo, la = lon[:, sel], lat[:, sel]
        cx, cy = np.nanmean(lo), np.nanmean(la)
        half = max(np.nanmax(np.abs(la - cy)) * 1.3, 0.03)
        k = 1 / np.cos(np.radians(cy))
        ax.set_xlim(cx - half * k, cx + half * k)
        ax.set_ylim(cy - half, cy + half)
        ax.add_collection(LineCollection(shore, colors="0.3", linewidths=0.6))
        segs = np.stack([np.stack([lo[:-1], la[:-1]], -1), np.stack([lo[1:], la[1:]], -1)], -2)  # (t, p, 2, 2)
        seg_t = np.repeat(0.5 * (hours[:-1] + hours[1:]), sel.sum())
        lc = LineCollection(segs.reshape(-1, 2, 2), cmap="viridis", norm=norm, linewidths=0.5, rasterized=True)
        lc.set_array(seg_t)
        ax.add_collection(lc)
        ax.plot(lo[0], la[0], "k+", ms=4, mew=0.6)
        ax.plot(lo[-1], la[-1], "o", ms=2, mfc="none", mec="k", mew=0.4)
        ax.set_aspect(k)
        ax.xaxis.set_major_locator(mpl.ticker.MaxNLocator(4))
        ax.ticklabel_format(useOffset=False)
        ax.set_xlabel("Longitude (°E)")
        ax.set_ylabel("Latitude (°N)")
        ax.text(0.03, 0.97, f"({'abcd'[g]})", transform=ax.transAxes, fontweight="bold", va="top")
    fig.colorbar(mpl.cm.ScalarMappable(norm=norm, cmap="viridis"), ax=axes, shrink=0.6,
                 label="Time since release (h)")
    out = args.run / "figures"
    for ext in ("pdf", "png"):
        print(save_figure(fig, out, "phase0_tracks_2d", ext))


if __name__ == "__main__":
    main()
