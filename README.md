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

Requires [uv](https://docs.astral.sh/uv/). Python 3.13 is pinned in `.python-version` (the `plumes2` near-field model needs 3.13 or later).

```
uv sync
```

```
uv run pytest
```

The engine tests run OceanTracker on the local hourly files and are skipped when `data/phase0/slim2d/` is empty.

## Run the prototype

```
uv run python scripts/fetch_surface.py --start 2026-09-30T16:00 --end 2026-10-05T15:00
uv run uvicorn ssm_pt.api.app:app --port 8000
```

`fetch_surface.py` writes one slim file per hour (surface currents plus the model's 10 m wind) into `data/phase0/slim2d/`, reading only the needed chunks from S3 (about 48 MB of each 211 MB file). Re-running it adds any variable missing from older files, such as wind, without downloading them again. Open http://localhost:8000, click the map in the water, set the start and end, choose what you are tracking, then press **Run**. The time sliders span whatever hours are in that folder; set `SSM_PT_DATA_DIR` to use another folder. Arrows show the model's surface current at the hour on screen (the Start slider before a run, the playback time after), thinned to about one per 30 px; toggle them in the layers control. Under Physics, "What are you tracking?" picks the particle class: a water parcel (currents and random spreading only), floating material (also pushed by a share of the 10 m wind, default 3%; it washes ashore and stays where it reaches the coast unless "Washes ashore" is unticked), or a decaying substance (its amount halves every half-life; dots fade). Water parcels and decaying substances bounce back off the coast. Any particle the falling tide leaves on a dry flat is stranded until the water returns. The model's open-ocean edges currently act as coast. Sinking particles and larvae need currents below the surface and wait for 3D data. Click a particle (at any frame) to show its whole path with distance travelled; click away or press Esc to clear. Run output goes to `runs/api/`. The S3 data layer (Phase 1) is not wired in yet; restart the server after fetching new hours.

### Plume dilution tool

```
uv run python scripts/fetch_3d.py --start 2026-07-01T00:00 --end 2026-08-31T23:00 --workers 8
uv run python scripts/fetch_3d.py --clip --start 2026-07-01T00:00 --end 2026-08-31T23:00 --workers 8
uv run python scripts/build_outfalls.py
```

`fetch_3d.py` reads all 10 model layers of u/v (about 42 MB per hour from S3, about 10 s per hour per worker) and writes depth-averaged hourly files to `data/plume/davg/` (10.5 MB each). With `--clip` it writes the 3D currents, temperature and salinity around Sequim Bay to `data/plume/sequim3d/` (about 1.8 MB each), the ambient profiles the PLUMES near field runs on. `build_outfalls.py` reads outfalls and candidate sites from the marine-energy repo (`--repo`, default `~/Documents/GitHub/marine-energy`), keeps those within 1 km of the model mesh, snaps them to it and writes `data/outfalls.geojson`. Open http://localhost:8000/#plume (or the **Plume dilution** tab), click an outfall (◆) or the water to place the source, set its flow and near field, then press **Run**. Hover or tap the (i) beside any setting for what it does and where its default comes from. For more sources (up to 6), press **+ Add another source** and click again; each source keeps its own settings (click its row to edit them), and the map colours each source's plume in its own dye, mixing the colours where plumes overlap (hover for each source's share). The map reaches half the map size beyond every source in each direction (a square that wide around a lone source), up to 200 km across. By default the near field is fixed values (dilution and plume diameter from your own PLUMES run; defaults are the Admiralty Inlet TD1 case), so a default run reads only the depth-averaged files. Choose "PLUMES, every hour" to run PLUMES' UM3 model every hour through Ebb Carbon's `plumes2` on the model's current, temperature and salinity profile at the source (Sequim Bay box only, from the `--clip` files); its diffuser defaults are Ebb's Macoma diffuser, a placeholder until PNNL-Sequim's are known. Each source discharges continuously from Start to End. The map shows dilution (1 : N), or excess TA or DIC, in 150 m cells, mixed through the whole water column by default (the most dilute case), with a 5 min time step by default (shorter steps are slower and, in Sequim Bay, changed nothing beyond particle noise); shift-click to add a receptor point and plot its time series. The plume tab picks up new hours while a fetch is still running (the window is the unbroken run from the first hour). Plume output goes to `runs/plume/`. See `docs/plan.md` decisions 10–19 for scope and caveats.

## Docs

- [`docs/plan.md`](docs/plan.md): decisions, rationale, phases, risks
- [`docs/architecture.md`](docs/architecture.md): repo map, implementation reference, SSCOFS data reference
- [`docs/todo.md`](docs/todo.md): task checklist
