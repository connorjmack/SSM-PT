"""FastAPI app: GET /meta, POST /runs, GET /runs/{id}, GET /runs/{id}/tracks, GET /currents."""
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import netCDF4
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ssm_pt.api.jobs import Jobs
from ssm_pt.currents import Currents
from ssm_pt.engine.base import RunRequest

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("SSM_PT_DATA_DIR", ROOT / "data/phase0/slim2d"))
RUNS_DIR = Path(os.environ.get("SSM_PT_RUNS_DIR", ROOT / "runs/api"))


def hour_files(data_dir: Path) -> dict[datetime, Path]:
    """Local hourly SSCOFS files keyed by valid time (UTC), sorted by time."""
    files = {}
    for p in sorted(data_dir.glob("sscofs.*.fields.n*.nc")):
        with netCDF4.Dataset(p) as nc:
            t = nc["time"]
            for d in netCDF4.num2date(t[:], t.units, only_use_cftime_datetimes=False, only_use_python_datetimes=True):
                files[d.replace(tzinfo=UTC)] = p
    return dict(sorted(files.items()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    files = hour_files(DATA_DIR)
    if not files:
        raise RuntimeError(f"No SSCOFS files in {DATA_DIR}")
    app.state.hours = list(files)
    app.state.currents = Currents(files)
    app.state.jobs = Jobs(DATA_DIR, RUNS_DIR)
    yield
    app.state.jobs.pool.shutdown(cancel_futures=True)


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
    t = app.state.jobs.tracks(rid)
    if t is None:
        raise HTTPException(404, "No tracks for this run id (unknown, still running, or failed)")
    return JSONResponse(t)


@app.get("/currents")
def currents(time: datetime, west: float = Query(ge=-180, le=180), south: float = Query(ge=-90, le=90),
             east: float = Query(ge=-180, le=180), north: float = Query(ge=-90, le=90),
             zoom: float = Query(ge=0, le=22)):
    """Surface-current arrows for the map view at the available hour nearest `time`."""
    t = time if time.tzinfo else time.replace(tzinfo=UTC)
    hour = min(app.state.hours, key=lambda h: abs(h - t))
    return JSONResponse(app.state.currents.arrows(hour, west, south, east, north, zoom))


# Mounted last so the API routes above take precedence.
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
