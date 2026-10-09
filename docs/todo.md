# Todo

Seeded from `plan.md` phases. Mark `[x]` as done; archive closed phases.

## Setup
- [ ] Create the environment: `uv sync` (user)
- [ ] Confirm `uv run python -c "import oceantracker, ssm_pt"` works
- [ ] Decide the run window: cache 2026-10-04 only, or through 2026-10-05 (see plan.md "Open questions")

## Phase 0 — OceanTracker go/no-go
- [x] Write `scripts/phase0_fetch.py`: download 6 full fields files for 2026-10-04 (`t09z` n001–n006, 04–09Z) into `data/phase0/full/`
- [x] Write `scripts/phase0_run.py`: OceanTracker 3D FVCOM run, `geographic_coords=False` (UTM), `release_at_surface=True` + `SurfaceFloat`, `OCEANTRACKER_NUMBA_CACHING=1`, output under `runs/phase0/`
- [x] Release sites: Deception Pass, Admiralty Inlet, open-water control, near the open shelf boundary
- [x] Record any reader overrides needed for SSCOFS (no `Itime`/`Itime2`, `time` in seconds since 2018-01-01, 1-based `nv`)
- [x] Record timings: numba compile (cold and cached), grid build, run
- [x] Try the 2D path once; record whether it fails as source reading suggests
- [x] Inspect tracks: surface speeds checked against model data (2D correct, 3D wrong)
- [ ] Quantify IDW blurring in tidal passes; observe open-boundary pile-up with a run longer than 5 h (carry into Phase 2)
- [x] Build slimmed surface-only files (surface u/v/ww, `zeta`, grid vars) and rerun; compare with full-file tracks
- [x] ~~If slim files fail: build u/v/ww all-layer files~~ (not needed: 2D slim files work, 3D surface velocities are wrong)
- [x] Load output via `load_track_data`; note track array shapes and NetCDF layout
- [x] Write go/no-go, timings and cache-variable set into plan.md; if no-go, add the numba-tracker fallback to plan.md

## End-to-end slice (local files, before Phase 1)
- [x] Engine adapter on the 6 local slim files (`SSCOFS2DReader` moved into `engine/oceantracker_engine.py`)
- [x] FastAPI app serving the API and `frontend/`; one warm worker process; in-memory results keyed by request hash
- [x] Leaflet page: click release, parameter form, run/poll, track playback, result stats, caveats
- [x] Land release returns a clear error; runs outside the data window rejected (422)
- [ ] Show to a WDFW user; collect first impressions

## Phase 1 — Data layer (TDD)
### catalog
- [ ] Failing tests: hour → (S3 key, `"nowcast"`) for one hour in each cycle block (00–03, 04–09, 10–15, 16–21Z)
- [ ] Failing tests: 22–23Z map to next day's `t03z` n001–n002; n000 never chosen
- [ ] Failing tests: hours before 2024-10-01 and non-whole hours raise; timezone-naive input rejected or treated as UTC (decide)
- [ ] Implement `catalog` until green
- [ ] Reserve forecast path: signature accepts a mode/source argument; forecast mapping left unimplemented

### grid
- [ ] Failing tests on a tiny synthetic mesh: boundary edges = edges in exactly one triangle; outline rings close; island becomes a hole
- [ ] Failing test: 0–360 longitudes converted to −180–180
- [ ] Implement extraction from one fields file (`nv` → 0-based, `x`/`y`, `lon`/`lat`, `h`, `nbe`) into `data/grid/`
- [ ] Implement outline GeoJSON; check it visually against the coastline once
- [ ] Implement point-in-domain check for release validation

### fields
- [ ] Failing tests with a fake fetcher: per-hour cache file path; second call fetches nothing (idempotent)
- [ ] Failing test: failure mid-write leaves no file at the final path (tmp + rename)
- [ ] Failing test: hours fetched in parallel; concurrent calls for the same hour do not corrupt the cache
- [ ] Implement `ensure_hours(hours)` with fsspec/s3fs range reads (anonymous); variable set from Phase 0
- [ ] Write `scripts/fetch_hours.py`: CLI calling `ensure_hours` for a date range
- [ ] Pre-fill the cache for 2026-10-04; record hour count, bytes and wall time in architecture.md

## Phase 2 — Engine adapter
- [x] Define the Tracker interface and request/params model (reserved fields from plan.md decision 7)
- [x] OceanTracker adapter: lon/lat → UTM (pyproj), run, load tracks, UTM → lon/lat rounded to 5 dp
- [ ] Let particles leave through the open ocean boundary and flag them (the FVCOM reader finds no open-boundary nodes, so the edge acts as coast)
- [ ] `scripts/track.py`: release GeoJSON in, `tracks.json` out

## Phase 3 — API and jobs
- [x] Request normalization + run-id hash
- [ ] Warm-worker process pool
- [ ] Endpoints `GET /meta`, `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/tracks`; cache miss → `ensure_hours`
- [ ] Tests with FastAPI `TestClient` and a fake tracker

## Phase 4 — Leaflet frontend
- [ ] Map with domain outline from `/meta`
- [ ] Point/polygon draw (CDN plugin, no build step) and parameter form
- [x] Submit, poll, fetch; Canvas playback with time slider
- [ ] GeoJSON and CSV download
- [x] Show UI caveats (surface layer depth, windage limits, hourly output)
- [x] Particle classes: water parcel, floating (windage), decaying (half-life)
- [x] Floating material washes ashore (option, on by default); "Stranded" card counts only tidal stranding
- [ ] Sinking particles and larvae (need 3D currents)

## Phase 5 — Deploy and user test
- [ ] Provision VM in us-east-1; serve API + static frontend
- [ ] Pre-fill cache on the VM; smoke-test end to end
- [ ] Run sessions with WDFW users; log feedback in plan.md

## Plume dilution tool (Sequim Bay)
See plan.md decisions 10–19.

### Waiting on others
- [ ] PNNL-Sequim outfall details: flow, port depth, diffuser design, effluent TA/DIC/temperature/salinity
- [ ] Near-field values at Sequim: PLUMES run from co-authors, or agreed estimates (initial dilution, plume diameter, plume depth)
- [ ] From co-authors: SSM TD1/TA40 Jul–Aug output and the PLUMES Admiralty Inlet runs

### Phase A — data
- [ ] Write `scripts/fetch_3d.py`: all 10 layers of u/v/ww/temp/salinity plus zeta and sigma grid for an hour range (reuse `nowcast_key` and the blockcache read in `scripts/fetch_surface.py`)
- [ ] Failing test: depth average of u/v on a synthetic sigma column equals the layer-thickness-weighted mean
- [ ] Implement depth averaging until green
- [ ] Write full-domain depth-averaged files to `data/plume/davg/` in the slim2d layout; confirm `SSCOFS2DReader` runs on one
- [ ] Write the clipped Sequim-box 3D files to `data/plume/sequim3d/` (for Phase C)
- [ ] Fetch a 1-day test window; record bytes transferred, file sizes and wall time in architecture.md
- [ ] Fetch 2026-07-01 to 2026-08-31 (1,488 h)
- [ ] Tidal-jet check at the bay entrance: particle speed vs element velocity; pick dt from a CFL check; record in plan.md

### Phase A — engine
- [ ] Failing tests for `PlumeRequest`/`Source` in `backend/tests/test_plume.py` (one source max, flow > 0, dilution ≥ 1, run inside the data window)
- [ ] Implement `PlumeRequest` and `Source` in `backend/ssm_pt/engine/plume.py`
- [ ] Build a synthetic uniform-flow mesh file for tests
- [ ] Failing test: continuous point source in uniform flow matches the analytical Gaussian plume
- [ ] Adapter: one release group per source, continuous release (`release_interval`, `duration`), release cloud from near-field diameter
- [ ] Particle property carrying ΔTA and ΔDIC mass
- [ ] `GriddedStats2D` on a shared UTM grid; concentration C = Σm / (A·H); Gaussian test green
- [ ] Failing test then fix: mass budget (released = in domain + culled)
- [ ] Particle count from release rate × duration; plume-run particle cap separate from `RunRequest`

### Phase A — results and API
- [ ] Write per-source gridded fields per frame (NetCDF) under `runs/plume/<id>/`
- [ ] Separate plume job queue with progress reporting
- [ ] Endpoints: `POST /plumes`, `GET /plumes/{id}`, frame, summary (max, percentiles), receptor time series
- [ ] API tests with `TestClient` and a fake plume engine

### Phase A — outfalls
- [ ] `scripts/build_outfalls.py`: read marine-energy POTW outfalls and candidate sites, keep marine ones inside the wet mesh, snap to nearest wet element, write `data/outfalls.geojson` with snap distance
- [ ] `GET /outfalls`

### Phase A — UI (`frontend/index.html`)
- [ ] Plume mode toggle alongside particle mode
- [ ] Outfall layer; click an outfall or the water to add a source
- [ ] Source panel: flow, start/end, near-field dilution/diameter/depth, ΔTA, ΔDIC
- [ ] Log-scale dilution heat map with playback
- [ ] Receptor points: click to add, time series chart
- [ ] Caveats: depth-averaged = upper bound on dilution; screening only; near field from PLUMES

### Phase A — verify
- [ ] `uv run pytest` passes
- [ ] Manual: 2-month Sequim run in the browser; check the effluent-mass-in-bay series

### Phase B — chemistry and validation
- [ ] Add pinned `PyCO2SYS` to `pyproject.toml`
- [ ] Decide background TA (salinity regression) and DIC source (SSM output or observations); record in plan.md
- [ ] Failing test: pH from summed ΔTA/ΔDIC differs from summed ΔpH and matches PyCO2SYS reference values
- [ ] Per-cell pH, Ω_ar and ΔpH from cell-mean TA/DIC
- [ ] ΔpH and Ω layers in the UI; area above a threshold
- [ ] Run Admiralty TD1 for Jul–Aug; compare ΔpH with SSM Jul–Aug mean and dilution with PLUMES Table 2
- [ ] Record the validation result in plan.md

### Phase C — multiple sources, long runs, 3D
- [ ] Lift the one-source limit; per-source share of concentration
- [ ] Failing test then fix: superposition (two sources together = sum of separate runs, within noise)
- [ ] Open-boundary culling (Phase 2 task above) before any month-scale run
- [ ] Longer windows through the Phase 1 data layer (`fields.ensure_hours`)
- [ ] 3D layer check on the Sequim box: fixed-depth releases vs direct layer integration (layer-order hypothesis)
- [ ] Kz from a Richardson-number scheme written as `A_Z_profile`
- [ ] Trap-depth release; cull boundary inside the box; box-doubling sensitivity
- [ ] Sequim WRF (WA0022349) as the second source
