"""FastAPI app.

Particles: GET /meta, POST /runs, GET /runs/{id}, GET /runs/{id}/tracks, GET /currents.
Plumes: GET /plume/meta, GET /outfalls, POST /plumes, GET /plumes/{id}, GET /plumes/{id}/result,
GET /plumes/{id}/frame/{k}, GET /plumes/{id}/receptor.
"""
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import netCDF4
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ssm_pt.api.jobs import Jobs, run_plume
from ssm_pt.currents import Currents
from ssm_pt.engine.base import RunRequest
from ssm_pt.engine.plume import PlumeRequest, read_fraction, read_receptor

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("SSM_PT_DATA_DIR", ROOT / "data/phase0/slim2d"))
RUNS_DIR = Path(os.environ.get("SSM_PT_RUNS_DIR", ROOT / "runs/api"))
PLUME_DIR = Path(os.environ.get("SSM_PT_PLUME_DIR", ROOT / "data/plume/davg"))
PLUME_RUNS_DIR = Path(os.environ.get("SSM_PT_PLUME_RUNS_DIR", ROOT / "runs/plume"))
OUTFALLS = Path(os.environ.get("SSM_PT_OUTFALLS", ROOT / "data/outfalls.geojson"))


def file_hours(p: Path) -> list[datetime]:
    with netCDF4.Dataset(p) as nc:
        t = nc["time"]
        return [d.replace(tzinfo=UTC) for d in
                netCDF4.num2date(t[:], t.units, only_use_cftime_datetimes=False, only_use_python_datetimes=True)]


def hour_files(data_dir: Path, known: dict[Path, list[datetime]] | None = None) -> dict[datetime, Path]:
    """Local hourly SSCOFS files keyed by valid time (UTC), sorted by time. Files already in `known`
    (path -> hours) are not reopened; new ones are added to it."""
    known = {} if known is None else known
    files = {}
    for p in sorted(data_dir.glob("sscofs.*.fields.n*.nc")):
        if p not in known:
            known[p] = file_hours(p)
        files.update({d: p for d in known[p]})
    return dict(sorted(files.items()))


def contiguous(files: dict[datetime, Path]) -> dict[datetime, Path]:
    """The unbroken hourly run from the first file; a fetch still in progress can leave later gaps."""
    out, prev = {}, None
    for t, p in files.items():
        if prev is not None and t - prev != timedelta(hours=1):
            break
        out[t], prev = p, t
    return out


def refresh_plume_data():
    """Rescan the plume data folder when its file count changes (files keep arriving while a fetch runs)."""
    n = sum(1 for _ in PLUME_DIR.glob("sscofs.*.fields.n*.nc"))
    if n == app.state.plume_n:
        return
    files = contiguous(hour_files(PLUME_DIR, app.state.plume_known))
    app.state.plume_n, app.state.plume_hours = n, list(files)
    app.state.plume_currents = Currents(files) if files else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    files = hour_files(DATA_DIR)
    if not files:
        raise RuntimeError(f"No SSCOFS files in {DATA_DIR}")
    app.state.hours = list(files)
    app.state.currents = Currents(files)
    app.state.jobs = Jobs(DATA_DIR, RUNS_DIR)
    app.state.plume_n, app.state.plume_hours, app.state.plume_currents, app.state.plume_known = -1, [], None, {}
    refresh_plume_data()
    app.state.plume_jobs = Jobs(PLUME_DIR, PLUME_RUNS_DIR, runner=run_plume)
    yield
    app.state.jobs.pool.shutdown(cancel_futures=True)
    app.state.plume_jobs.pool.shutdown(cancel_futures=True)


app = FastAPI(title="SSM-PT", lifespan=lifespan)


@app.get("/meta")
def meta():
    return {"hours": app.state.hours, "source": "NOAA SSCOFS nowcast, top sigma layer"}


@app.post("/runs")
def submit(req: RunRequest):
    first, last = app.state.hours[0], app.state.hours[-1]
    if req.start < first or req.start + timedelta(hours=req.duration_h) > last:
        raise HTTPException(422, f"The run must fit inside the available data, "
                                 f"{first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M} UTC.")
    return app.state.jobs.status(app.state.jobs.submit(req))


@app.get("/runs/{rid}")
def status(rid: str):
    s = app.state.jobs.status(rid)
    if s is None:
        raise HTTPException(404, "Unknown run id")
    return s


@app.get("/runs/{rid}/tracks")
def tracks(rid: str):
    t = app.state.jobs.result(rid)
    if t is None:
        raise HTTPException(404, "No tracks for this run id (unknown, still running, or failed)")
    return JSONResponse(t)


@app.get("/currents")
def currents(time: datetime, west: float = Query(ge=-180, le=180), south: float = Query(ge=-90, le=90),
             east: float = Query(ge=-180, le=180), north: float = Query(ge=-90, le=90),
             zoom: float = Query(ge=0, le=22), source: Literal["surface", "plume"] = "surface"):
    """Current arrows for the map view at the available hour nearest `time`: the surface layer for particle
    runs, or the depth average the plume tool runs on."""
    t = time if time.tzinfo else time.replace(tzinfo=UTC)
    if source == "plume":
        refresh_plume_data()
        hours, cur = app.state.plume_hours, app.state.plume_currents
        if cur is None:
            raise HTTPException(404, "No plume data on disk")
    else:
        hours, cur = app.state.hours, app.state.currents
    hour = min(hours, key=lambda h: abs(h - t))
    return JSONResponse(cur.arrows(hour, west, south, east, north, zoom))


# ── Plume dilution tool ──

@app.get("/plume/meta")
def plume_meta():
    refresh_plume_data()
    return {"hours": app.state.plume_hours, "outfalls": OUTFALLS.exists(),
            "source": "NOAA SSCOFS nowcast, depth-averaged over the 10 sigma layers"}


@app.get("/outfalls")
def outfalls():
    if not OUTFALLS.exists():
        return {"type": "FeatureCollection", "features": []}
    return JSONResponse(json.loads(OUTFALLS.read_text()))


@app.post("/plumes")
def submit_plume(req: PlumeRequest):
    refresh_plume_data()
    hours = app.state.plume_hours
    if not hours:
        raise HTTPException(422, "No plume data on disk; run scripts/fetch_3d.py first.")
    if req.start < hours[0] or req.start + timedelta(hours=req.duration_h) > hours[-1]:
        raise HTTPException(422, f"The run must fit inside the available data, "
                                 f"{hours[0]:%Y-%m-%d %H:%M} to {hours[-1]:%Y-%m-%d %H:%M} UTC.")
    jobs = app.state.plume_jobs
    return jobs.status(jobs.submit(req))


@app.get("/plumes/{rid}")
def plume_status(rid: str):
    s = app.state.plume_jobs.status(rid)
    if s is None:
        raise HTTPException(404, "Unknown plume run id")
    return s


def plume_file(rid: str) -> Path:
    if app.state.plume_jobs.result(rid) is None:
        raise HTTPException(404, "No result for this plume run id (unknown, still running, or failed)")
    return app.state.plume_jobs.out_dir(rid) / "plume.nc"


@app.get("/plumes/{rid}/result")
def plume_result(rid: str):
    plume_file(rid)
    return JSONResponse(app.state.plume_jobs.result(rid))


@app.get("/plumes/{rid}/frame/{frame}")
def plume_frame(rid: str, frame: str):
    """Effluent fraction (1 / dilution) on the grid, sparse: flat row-major indices of non-empty cells and
    their values summed over sources (plus by_source, when there are several). frame is an output index, or
    'max' / 'mean' over the run."""
    if frame not in ("max", "mean") and not frame.isdigit():
        raise HTTPException(422, "frame must be an output index, 'max' or 'mean'")
    try:
        f = read_fraction(plume_file(rid), frame)
    except IndexError:
        raise HTTPException(404, "No such frame")
    f = f.reshape(len(f), -1)  # (source, cell)
    total = f.sum(axis=0)
    idx = np.flatnonzero(total > 0)
    out = {"idx": idx.tolist(), "val": [float(f"{v:.4g}") for v in total[idx]]}
    if len(f) > 1:  # each source's values in the same cells, for the per-source colours and TA/DIC
        out["by_source"] = [[float(f"{v:.4g}") for v in row[idx]] for row in f]
    return out


@app.get("/plumes/{rid}/receptor")
def plume_receptor(rid: str, lon: float = Query(ge=-180, le=180), lat: float = Query(ge=-90, le=90)):
    r = read_receptor(plume_file(rid), lon, lat)
    if r is None:
        raise HTTPException(422, "That point is outside the plume grid")
    return r


# Mounted last so the API routes above take precedence.
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
