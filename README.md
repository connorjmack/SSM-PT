# SSM-PT

SSM-PT (Salish Sea Model Particle Tracking) is a web tool for tracking surface particles in the Salish Sea. Users click or draw a release on a Leaflet map; a Python backend runs [OceanTracker](https://github.com/oceantracker/oceantracker) on NOAA SSCOFS model currents and returns trajectories to play back and download. It is built for WDFW and other semi-technical users. The prototype covers one day of hourly surface currents (2026-10-04).

## Architecture

```
┌─ frontend/  (Leaflet, browser) ────────────────────────────────────────────┐
│ draw release point/polygon · set N, start, duration, diffusivity           │
│ time-slider playback of particles · download GeoJSON/CSV                   │
└────────────────────────────────────────────────────────────────────────────┘
        │ POST /runs  (release spec JSON)            ▲ GET /runs/{id}/tracks
        ▼                                            │ (lon/lat per time step)
┌─ backend/ssm_pt/  (Python) ────────────────────────────────────────────────┐
│ api/app.py     FastAPI: /meta  /runs  /runs/{id}  /runs/{id}/tracks        │
│     │                                                                      │
│ api/jobs.py    warm worker pool; run id = hash(request) → repeats cached   │
│     │                                                                      │
│ engine/        Tracker interface → OceanTracker adapter (UTM 10 m)         │
│     │ needs hours t0 … t1                                                  │
│ fields.py      ensure_hours(): fetch only missing hours → data/cache/      │
│ catalog.py     hour → S3 key (nowcast now, forecast later)                 │
│ grid.py        static mesh, read once + domain outline GeoJSON             │
└────────────────────────────────────────────────────────────────────────────┘
        │ HTTP range reads: surface u, v only (~14 MB of each 210 MB file)
        ▼
  AWS S3  s3://noaa-nos-ofs-pds/sscofs/netcdf/   (NOAA SSCOFS, FVCOM mesh)
```

Compute is lazy: a run fetches only the hours missing from `data/cache/`, and only the surface layer. Each cached hour is reused by every later run. Only particle positions go to the browser. See [`docs/architecture.md`](docs/architecture.md) for details.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned in `.python-version`.

```
uv sync
```

```
uv run pytest
```

## Run the prototype

```
uv run uvicorn ssm_pt.api.app:app --port 8000
```

Open http://localhost:8000, click the map in the water, then press **Run**. The prototype tracks on the six local hourly files in `data/phase0/slim2d/` (2026-10-04 04:00–09:00 UTC); set `SSM_PT_DATA_DIR` to use another folder. Run output goes to `runs/api/`. The S3 data layer (Phase 1) is not wired in yet.

## Docs

- [`docs/plan.md`](docs/plan.md): decisions, rationale, phases, risks
- [`docs/architecture.md`](docs/architecture.md): repo map, implementation reference, SSCOFS data reference
- [`docs/todo.md`](docs/todo.md): task checklist
