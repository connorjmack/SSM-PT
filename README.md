# SSM-PT

SSM-PT (Salish Sea Model Particle Tracking) is a web tool for tracking surface particles in the Salish Sea. Users click or draw a release on a Leaflet map; a Python backend runs [OceanTracker](https://github.com/oceantracker/oceantracker) on NOAA SSCOFS model currents and returns trajectories to play back and download. It is built for WDFW and other semi-technical users. The prototype covers five days of hourly surface currents (2026-09-30 16:00 to 2026-10-05 15:00 UTC); runs can be up to 120 h.

## Architecture

```
┌─ frontend/  (Leaflet, browser) ────────────────────────────────────────────┐
│ draw release point/polygon · set N, start, duration, diffusivity           │
│ time-slider playback of particles · download GeoJSON/CSV                   │
└────────────────────────────────────────────────────────────────────────────┘
        │ POST /runs  (release spec JSON)            ▲ GET /runs/{id}/tracks
        ▼                                            │ (lon/lat per time step)
┌─ backend/ssm_pt/  (Python) ────────────────────────────────────────────────┐
│ api/app.py     FastAPI: /meta /runs /runs/{id} /runs/{id}/tracks /currents │
│     │                                                                      │
│ api/jobs.py    warm worker pool; run id = hash(request) → repeats cached   │
│     │                                                                      │
│ engine/        Tracker interface → OceanTracker adapter (UTM 10 m)         │
│     │ needs hours t0 … t1                                                  │
│ fields.py      ensure_hours(): fetch only missing hours → data/cache/      │
│ catalog.py     hour → S3 key (nowcast now, forecast later)                 │
│ grid.py        static mesh, read once + domain outline GeoJSON             │
│ currents.py    thinned surface-current arrows for the map view, per hour   │
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
uv run python scripts/fetch_surface.py --start 2026-09-30T16:00 --end 2026-10-05T15:00
uv run uvicorn ssm_pt.api.app:app --port 8000
```

`fetch_surface.py` writes one slim surface-only file per hour into `data/phase0/slim2d/`, reading only the needed chunks from S3 (about 44 MB of each 211 MB file). Open http://localhost:8000, click the map in the water, set the start and end, then press **Run**. The time sliders span whatever hours are in that folder; set `SSM_PT_DATA_DIR` to use another folder. Arrows show the model's surface current at the hour on screen (the Start slider before a run, the playback time after), thinned to about one per 30 px; toggle them in the layers control. Run output goes to `runs/api/`. The S3 data layer (Phase 1) is not wired in yet; restart the server after fetching new hours.

## Docs

- [`docs/plan.md`](docs/plan.md): decisions, rationale, phases, risks
- [`docs/architecture.md`](docs/architecture.md): repo map, implementation reference, SSCOFS data reference
- [`docs/todo.md`](docs/todo.md): task checklist
