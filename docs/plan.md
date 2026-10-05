# Plan

## Goal

A web particle-tracking tool for the Salish Sea for WDFW and other semi-technical users. A Leaflet map sends a release to a Python backend, which tracks particles on NOAA SSCOFS currents and returns trajectories.

**Prototype scope:** one day of hourly nowcast data (2026-10-04), surface currents only.

**Later features (design must not block these):** forecast mode, windage, 3D / fixed depth, backtracking, connectivity / polygon statistics, longer archive.

## Decisions

### 1. Data from the AWS S3 NODD bucket, not CO-OPS THREDDS
- Source: `s3://noaa-nos-ofs-pds/sscofs/netcdf/YYYY/MM/DD/`, anonymous, us-east-1. Archive from 2024-10-01; same-day files available.
- THREDDS keeps only 31 days and is a shared server.
- S3 HTTP range reads give the variable/layer subsetting we need anyway.

### 2. "Lazy" = requested hours, surface layer, needed variables only. No spatial subsetting.
- Each stored chunk spans ~1/3 of the domain, so a bbox saves at most 3× I/O.
- Whole-domain surface u+v is ~3.5 MB/hour in memory (~85 MB/day): cheap.
- Clipping an unstructured mesh needs per-request connectivity rebuilds and creates artificial boundaries that particles leak through.
- One shared per-hour cache then serves all users.

### 3. Tracker: OceanTracker==0.5.3.9 behind an adapter, gated by a Phase 0 go/no-go test
- Run in UTM zone 10 metres (EPSG:32610, `geographic_coords=False`). Avoids a lon/lat metres-to-degrees bug fixed only in 0.5.3.9, and the 0–360 longitudes.
- pyproj converts Leaflet lon/lat to and from UTM.
- The adapter (`engine/base.py` interface) keeps the engine swappable.
- **Fallback if Phase 0 fails:** a small custom 2D numba tracker on the `nv` mesh.
- **Phase 0 result (2026-10-05): GO, using 2D surface-only cache files, not 3D.**
  - **2D surface files plus a reader subclass work.** Cache files hold surface-layer `u`/`v` as `(time, nele)`, plus `zeta`, `wet_cells` and the grid variables. The stock `FVCOMreader` fails on 2D: there is no `cell_center_weights` and the element-to-node shapes don't match. `scripts/phase0_reader.py::SSCOFS2DReader` fixes both in about 20 lines. Do not add `SurfaceFloat` in 2D; it writes `x[:, 2]`, which a 2D run does not have.
  - **Rejected: 3D run with `release_at_surface` + `SurfaceFloat`.** It runs, but surface particles move 3–7× too slowly. At Admiralty Inlet they went 3.3 km in 5 h, against 13.8 km from integrating the layer-0 velocity at the release element (Eulerian). In the central basin they went 0.35 km against 2.25 km. The 2D run gave 15.5 km and 2.34 km. The cause, probably vertical layer handling in the 3D FVCOM path, was not investigated; it is worth an upstream issue.
  - **Timings (laptop, 400 particles, 5 h, dt = 120 s):** 29 s cold (9.5 s package scan, 5 s grid setup, numba compile); 6.5–7.7 s with numba caching. Time stepping itself takes about 7 ms per step.
  - **Model `x`/`y` match EPSG:32610** to within 0.25 m, so pyproj conversion is consistent with the mesh.
- Rejected:
  - **PyLag:** needs `Itime`/`Itime2` (SSCOFS lacks them); Cython build; linux-only conda on a personal channel; PyPI name taken by another package.
  - **OpenDrift:** FVCOM readers need `Itime` or Cartesian+MJD time; nearest-neighbour lookup, no interpolation.
  - **Parcels v4:** released 2026-09-09 as "early, not stable"; UXArray has no FVCOM converter; no sigma support.

### 4. Prototype uses the nowcast "best estimate" only
- Continuous hindcast built from n001–n006 of each cycle.
- Forecast f001–f072 is a later feature; `catalog` returns a source label (nowcast/forecast) from the start so it can be added without an interface change.

### 5. Async API: submit, poll, fetch
- `POST /runs` → run id; `GET /runs/{id}` → status; `GET /runs/{id}/tracks` → result.
- Run id is a hash of the normalized request, so identical requests share one result.
- Output to the browser: compact JSON with times and per-particle lon/lat rounded to 5 decimals (~1 m), plus status.
- Warm worker processes hold compiled numba code and the grid between runs.

### 6. Frontend: plain Leaflet, no build step
- Release by point or polygon draw, plus a parameter form.
- Time-slider playback on a Canvas layer.
- GeoJSON and CSV download.

### 7. Request schema reserves fields for future features
```json
{"release": {"type": "Point", "coordinates": [-122.6, 48.4]},
 "n_particles": 500, "start": "2026-10-04T06:00Z", "duration_h": 24,
 "diffusivity_m2s": 1.0, "output_interval_min": 30,
 "depth_mode": "surface", "windage_pct": 0}
```
`release` is a GeoJSON Point or Polygon. Prototype accepts only `depth_mode="surface"` and `windage_pct=0`.

### 8. Environment: uv, `pyproject.toml` at repo root, `uv.lock`; no conda
- Every dependency, including OceanTracker (pip-only), has PyPI wheels.
- All direct dependencies pinned with `==`; Python 3.12.

### 9. Hosting: a VM in us-east-1, next to the bucket

## Phases

| # | Phase | Exit criterion |
|---|---|---|
| 0 | OceanTracker test (go/no-go) | Decision recorded here with timings and track inspection |
| 1 | Data layer: `catalog`, `grid`, `fields`, fetch CLI (TDD, `catalog` first) | 2026-10-04 cache pre-filled by CLI; tests pass |
| 2 | Engine adapter + track CLI writing `tracks.json` | CLI run from a release GeoJSON produces valid tracks |
| 3 | API and job pool | Submit/poll/fetch works end-to-end against the cache |
| 4 | Leaflet frontend | Release, run, playback, download in a browser |
| 5 | Deploy (us-east-1 VM) and WDFW user testing | Feedback collected from WDFW users |

**Phase 0 detail:** download ~6 full hourly files for 2026-10-04; do a surface-held run; record timings (numba compile, grid build, run); inspect tracks in the tidal passes and at the open boundary; then retry with slimmed cache files.

## Blockers

- None. Phase 0 is GO (see decision 3). `exceptiongroup==1.3.1` is pinned because oceantracker 0.5.3.9 imports it without declaring it.

## Open questions

- **Run window vs. cached data.** One day of data (2026-10-04 00–23Z) cannot serve the example request (06Z start + 24 h ends 2026-10-05 06Z). Either cache through 2026-10-05 or cap `start + duration_h` to the cached window and reject otherwise.

## Risks

### OceanTracker (Phase 0 tested items marked ✓)
- `FVCOMreader` is flagged `development=True`; maintainers said (issue #27) they reverse-engineered the format from two examples.
- ✓ **2D path broken for FVCOM:** fixed with our `SSCOFS2DReader` subclass. It relies on private method names (`_construct_hori_grid_variables`), so re-test on any OceanTracker upgrade.
- ✓ **3D surface velocities wrong** (see decision 3). Future 3D or fixed-depth features need this fixed upstream, or our own vertical interpolation.
- **Open boundary treated as land:** particles pile up at the shelf boundary. Flag them in post-processing. Not yet observed: 5 h was too short for shelf particles to reach the boundary.
- **Element velocities IDW-interpolated to nodes:** may blur tidal jets (Deception Pass, Admiralty Inlet). Not yet quantified.
- Reads local files only (`input_dir` + `file_mask`), so the cache must be on local disk.
- ✓ Startup: 29 s cold, about 7 s with `OCEANTRACKER_NUMBA_CACHING=1`; warm workers or caching are needed.
- `use_random_seed=True` did not give identical tracks across runs (probably parallel numba RNG), so run-id caching should store results rather than rely on re-running.
- OceanTracker's "max memory used" log line reports a bogus 423 GB on macOS; ignore it.
- Defaults to note: horizontal diffusivity `A_H` = 0.1 m²/s; RK4 integration; NetCDF track output read via `oceantracker.read_output.python.load_output_files.load_track_data`; entry point `oceantracker.main.run(params)`.

### Data / service
- OceanTracker 0.5.3.9 was published 2026-10-05; little field history.
- Archive depth for "longer archive" depends on NODD retention.

## UI caveats (show to users)

- "Surface" is the top sigma layer, about 1.6% of water depth (`siglay[0] = -0.0158`), not the skin layer.
- No windage or Stokes drift in the prototype; floating material (oil, debris, kelp) moves differently.
- Hourly model output under-resolves peak tidal currents.

## Success criteria

- **Phase 0 (go):** a surface-held OceanTracker run on SSCOFS completes; tracks stay in water and look plausible in Deception Pass and Admiralty Inlet; boundary pile-up is detectable; per-run overhead (compile + grid build) is acceptable with warm workers.
- **Prototype:** a WDFW user can draw a point or polygon release on 2026-10-04, run 500 particles, play the result back and download GeoJSON/CSV without help.
- **Latency (target, confirm after Phase 0):** a 500-particle, 24 h run returns in under ~1 min on a warm worker with a warm cache.
- Cache fill is idempotent and atomic; concurrent requests for the same hours never see partial files.
