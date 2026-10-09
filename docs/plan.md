# Plan

## Goal

A web particle-tracking tool for the Salish Sea for WDFW and other semi-technical users. A Leaflet map sends a release to a Python backend, which tracks particles on NOAA SSCOFS currents and returns trajectories.

**Prototype scope:** one day of hourly nowcast data (2026-10-04), surface currents only.

**Later features (design must not block these):** forecast mode, 3D / fixed depth (needed for sinking particles and larvae), backtracking, connectivity / polygon statistics, longer archive, multi-source plume runs.

**Second product:** a plume dilution tool for ocean alkalinity enhancement (OAE) effluent, in the same page and on the same engine. See [Plume dilution tool](#plume-dilution-tool-sequim-bay).

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
  - **Rejected: 3D run with `release_at_surface` + `SurfaceFloat`.** It runs, but surface particles move 3–7× too slowly. At Admiralty Inlet they went 3.3 km in 5 h, against 13.8 km from integrating the layer-0 velocity at the release element (Eulerian). In the central basin they went 0.35 km against 2.25 km. The 2D run gave 15.5 km and 2.34 km. The cause, probably vertical layer handling in the 3D FVCOM path, was not investigated; it is worth an upstream issue. (Found 2026-10-09: layer order; see Risks.)
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
 "depth_mode": "surface", "particle": {"type": "floating", "windage_pct": 3}}
```
`release` is a GeoJSON Point or Polygon. Prototype accepts only `depth_mode="surface"`. `particle` picks what is tracked: `{"type": "water"}` (default), `{"type": "floating", "windage_pct": 3, "washes_ashore": true}` (adds that % of the 10 m model wind; with `washes_ashore` it stops for good at the coastline, status `-3` in the tracks), or `{"type": "decaying", "half_life_h": 24}` (adds a per-particle `remaining` fraction to the tracks). Sinking particles and larvae need 3D currents and wait for 3D / fixed depth.

### 8. Environment: uv, `pyproject.toml` at repo root, `uv.lock`; no conda
- Every dependency, including OceanTracker (pip-only), has PyPI wheels.
- All direct dependencies pinned with `==`; Python 3.13.

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
- ✓ **3D surface velocities wrong** (see decision 3). Fixed 2026-10-09 by our reader subclass `SSCOFS3DReader` (see Risks: OceanTracker 3D path); 3D and fixed-depth features use it.
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
- Windage is a fixed share of the model's 10 m wind and also stands in for Stokes drift (SSCOFS has no waves). Real leeway depends on the object's shape and how high it floats.
- Hourly model output under-resolves peak tidal currents.
- Coast: OceanTracker moves a particle that would cross the coastline back to its last position, so water parcels bounce back. Floating material washes ashore instead (it stops at its last position, within one 2-minute step of the coast). Particles on a cell that dries are stranded until it floods.
- The FVCOM reader finds no open-boundary nodes, so the ocean edges act as coast: water that should leave piles up there, and floating material "washes ashore" on them.

## Success criteria

- **Phase 0 (go):** a surface-held OceanTracker run on SSCOFS completes; tracks stay in water and look plausible in Deception Pass and Admiralty Inlet; boundary pile-up is detectable; per-run overhead (compile + grid build) is acceptable with warm workers.
- **Prototype:** a WDFW user can draw a point or polygon release on 2026-10-04, run 500 particles, play the result back and download GeoJSON/CSV without help.
- **Latency (target, confirm after Phase 0):** a 500-particle, 24 h run returns in under ~1 min on a warm worker with a warm cache.
- Cache fill is idempotent and atomic; concurrent requests for the same hours never see partial files.

## Plume dilution tool (Sequim Bay)

**Goal:** model the far-field dilution of OAE effluent, as in Savoie et al. (`marine-energy/assets/Manuscript_Formatted_AMSavoie_v1.pdf`): a near-field plume model (PLUMES2.0v1) hands off to a far-field model (there, the Salish Sea Model, SSM). Users click to add sources on the same Leaflet page. One source first; several sources at once is the target, and larger-scale OAE later.

### 10. Scope: dilution, exposure and siting screening; not CDR efficiency
- Outputs: dilution and ΔTA/ΔDIC maps, then ΔpH and Ω maps, time series at receptor points, area above a threshold, date windows that can be matched to species-sensitive periods (paper Figure 9).
- Air–sea CO₂ uptake (CDR efficiency) stays with SSM. Its flux depends on each cell's pCO₂, which depends on how many particles share the cell, so it needs an Eulerian step coupled back to the particles; SSM-ICM already does this, with biology. This tool screens sites and scenarios worth running in SSM.

### 11. Site: Sequim Bay; Admiralty Inlet only as a validation case
- First source: PNNL-Sequim MCRL, 48.0793 N, -123.0438 E (`candidate_sites.csv`; no flow or NPDES record, so flow, port depth and effluent chemistry are user inputs).
- Mesh in the bay (48.00–48.10 N, 123.08–122.98 W): ~1,700 elements, median edge ~160 m, depths 0.7–39 m.
- The paper's theoretical Admiralty Inlet outfall (scenario TD1) is run only to compare against SSM and PLUMES output, which co-authors will share.

### 12. Plume runs are their own request type, not a particle class
- `PlumeRequest` with `sources: list[Source]` (1–6, pulled forward from Phase C on 2026-10-09) and a grid spec; same engine, same submit/poll/fetch pattern, same page.
- Release schedule, behavior and output are separate concerns: a particle class says how material moves; a source says how much is released, when and where; a plume run outputs concentration, not tracks. Behaviors such as decay can later attach to a source.

### 13. Near field comes from PLUMES; this tool starts where it ends
- Each source takes PLUMES outputs: initial (flux-averaged) dilution, plume diameter, plume depth. Particles start as a cloud of that diameter.
- Depth-averaged (as built): spreading the effluent over a ~150 m cell and the whole water column already dilutes it far beyond the near field, so the near-field dilution acts only as a cap (a cell's effluent fraction never exceeds 1/S₀). Plume depth matters once 3D exists.
- Acute (~6 m) and chronic (~62 m) mixing-zone metrics stay with PLUMES; they are smaller than a model cell. Releasing particles at a point would make near-source concentration depend on grid cell size.
- Coupled near field (planned 2026-10-09): run UM3 hourly at each source on SSCOFS current and T/S profiles from the Sequim-box files, through Ebb Carbon's `plumes2` (MIT Python port of PLUMES2.0, checked against the exe; the PLUMES2.0 release is a GUI-only Windows executable with no source). Gives hourly initial dilution, plume width, trap depth and chemistry at the mixing-zone edges. In depth-averaged mode it changes only the cap at the outfall cells; trap depth matters in 3D. `plumes2` needs Python ≥3.13, so the project moved to 3.13 (2026-10-09): one environment, since OceanTracker passes the full suite on 3.13 and a second environment would add upkeep for no gain. Built 2026-10-09 with these choices:
  - One run per hour, in parallel, not a lookup table: a run takes about 1.6 s and the profile (stratification as well as current) changes every hour.
  - The port is fixed below mean sea level, so its depth below the surface follows the tide. The profile is the source element's layers plus the surface and seabed.
  - The jets point downstream each hour unless a compass bearing is given; the effluent T and S default to the water at the port (seawater intake, as for PNNL and Ebb's Macoma runs).
  - plumes2 defaults otherwise, including the termination rule (second local maximum rise or fall). Ebb's Macoma runs used the third, which gives higher dilution (720 vs 890 in one Sequim hour); the default is the cautious choice. Far field off: OceanTracker is the far field.
  - A failed hour is reported and bridged from the hours around it. Sources more than 500 m from every Sequim-box element are refused.
  - Defaults are Ebb's Macoma diffuser and flow (25 × 12.7 mm ports at 0.61 m, 45° up, 2 m deep, 5.9 m³/h) as a placeholder for PNNL-Sequim.

### 14. Tracers: ΔTA and ΔDIC as conservative mass; chemistry computed after summing
- Each particle carries ΔTA and ΔDIC mass (discharge × excess concentration × release interval / particles per pulse).
- Gridded on one shared regular UTM grid with OceanTracker `GriddedStats2D` (`release_group_centered_grids=False`): counts per release group and property sums give mass per source per cell.
- Sources superpose as ΔTA and ΔDIC. pH and Ω are nonlinear, so they are computed per cell from (background + summed Δ) with PyCO2SYS (Phase B); never averaged across particles or summed across sources.
- Superposition holds for conservative or first-order-decaying tracers whose near fields don't merge.

### 15. Vertical: depth-averaged first, 3D behind a gate
- Phase A: depth-averaged currents on the full domain, C = Σm / (A·H). Assumes a fully mixed water column, so it is the upper bound on dilution (least conservative); the UI says so. Optional surface mixing depth for stratified cases.
- SSCOFS has no depth-averaged velocity (`ua`/`va` absent); compute it from the 10 layers weighted by sigma-layer thickness.
- Full domain avoids clipping: a clipped mesh's edges act as coast in OceanTracker (particles pile up there, and effluent that leaves on the ebb never returns on the flood).
- 3D (Phase C) runs on a clipped Sequim box only after the layer check in Risks passes.

### 16. Data: depth-averaged domain plus a 3D Sequim box; Jul–Aug 2026
- Window 2026-07-01 to 2026-08-31 (1,488 h; matches the paper's representative cases and leaves time for build-up in the bay).
- As built (Phase A): read all 10 layers of `u`, `v` (~42 MB/h from S3, ~10 s per hour per worker) and write full-domain depth-averaged files in the slim2d layout, so `SSCOFS2DReader` reads them unchanged (zlib, 10.5 MB/h, ~16 GB for the window; the grid variables repeat in every file).
- Sequim-box 3D files, a second pass (`fetch_3d.py --clip` → `data/plume/sequim3d/`): `u`, `v`, `ww`, `temp`, `salinity` on all layers plus `zeta`, for element centres in lon −123.20 to −122.85, lat 48.00 to 48.20 (Dungeness Spit to Protection Island; 10,489 elements, 5,736 nodes; 1.6 MB/h stored). Fetched now for the near-field profiles (decision 13); Phase C uses them too. The box's mesh indices span two of the three u/v chunks, so a smaller box would not read less.
- Time step: 300 s by default, settable per run (30 s to 10 min, dividing the 600 s release interval). A CFL check at the bay entrance gave 60 s (1.3 m/s peak, 91 m shortest edge), but OceanTracker follows particles across several cells in one step, and on a 3-day Sequim run 300 s differed from 60 s by 6.4% in the time-mean field against 6.0% between two 60 s runs (`scripts/plume_dt_check.py`). Run time is mostly a fixed ~8 ms per step, so 300 s is ~2.6× faster for 3 days and ~5× for 2 months.

### 17. Results stay on disk; the browser gets fields, not tracks
- `jobs.py` returns whole results through the process pool and keeps them in memory; `/runs/{id}/tracks` sends every particle. A plume needs 10⁵–10⁶ particles, so that would be gigabytes.
- Plume runs write gridded fields per source per frame (NetCDF) under `runs/plume/<id>/`. The API serves one frame, summary maps (max, percentiles), receptor time series and a small particle sample.
- Plume runs get their own job queue with progress, so a long run doesn't block particle runs.

### 18. Outfalls come from the marine-energy repo
- Sources: `data/water_infra/npdes_potw_outfalls.geojson` (301 POTW outfalls in a Salish Sea box) and `data/candidate_sites.csv` (1,845 sites in the box, 81 with a flow).
- Filter to marine discharges inside the SSCOFS wet mesh (the POTW set includes river outfalls, e.g. Burlington WWTP on the Skagit, 15 km inland). Snap each to the nearest wet element and show the snap distance. Pre-fill only fields the data has; flows are often missing and port depth and diffuser design never present.
- A one-off script builds `data/outfalls.geojson`; clicking an outfall adds it as a source.

### 19. Validation
- Synthetic: Gaussian plume for a continuous point source in uniform flow; mass budget (released = in domain + culled + decayed); superposition (two sources together = sum of separate runs, within noise).
- Admiralty Inlet TD1 (paper): effluent 13.5–65.3 MGD (0.6–2.9 m³/s), TA ~7,600 µmol/kg, DIC 4,312 µmol/kg, pH 9.55. PLUMES gives flux-averaged dilution 7.5–8.5 at the plume surfacing (diameter ~1.6 m, depth 0.79 m) and 7.5–9.6 at the chronic mixing zone (62 m). Compare our ΔpH with SSM's Jul–Aug mean from co-authors (paper Figure 8 is an annual mean, so not directly comparable).

### 20. Data on demand: a run fetches its own missing hours; forecast mode after (scoped 2026-10-09, not built)
- Today a plume run sees only the hours `scripts/fetch_3d.py` has already written (Jul–Aug 2026); any other window is a 422. `catalog.py` is a docstring only and `fields.py` holds only `depth_average`.
- Measured on the laptop (2026-10-09): one hour takes ~9 s per worker to fetch in either mode (depth-averaged or `--clip`), 14 s wall with process start-up. If 4 workers scale (untested), 3 days ≈ 3 min, a week ≈ 7 min, a month ≈ 30 min. Slower than the us-east-1 VM should be (decision 9).
- S3 freshness: cycles at 03/09/15/21Z; the 2026-10-09 t15z cycle finished writing at 18:26 UTC (~3.5 h after cycle time). The newest nowcast hour is therefore ~3.5–9.5 h old, and each cycle adds a 72 h forecast (f001–f072).
- **Stage 1, nowcast on demand (2024-10-01 to the newest nowcast hour):**
  - `catalog.hour_key(t, mode="nowcast")` → S3 key, file name, label (replaces `nowcast_key` in `fetch_surface.py`); `latest_hour()` lists the last two days' S3 prefixes, cached ~10 min.
  - `fields.ensure_hours(hours, kind, out, progress)`, `kind` = depth-averaged or Sequim-box 3D: the fetch and write functions move from `fetch_3d.py` into `fields`, and the script becomes a CLI over it. Atomic writes need a unique temporary name per writer; today's fixed `.part` name would collide when two runs fetch the same hour.
  - Jobs: a separate fetch pool (4 processes) fills missing hours, then the run joins the run queue, so one run's fetch does not hold up another run. Status gains `fetching` with done/total. Hours missing on S3 fail the run with the list. PLUMES-each-hour runs also fetch their Sequim-box 3D hours.
  - `/plume/meta` returns the archive range and the newest hour, not just what is on disk. The UI gets date/time inputs (a slider over two years of hours is unusable) and "Fetching currents 12 / 72…" in the status line.
- **Stage 2, forecast (to +72 h):**
  - `catalog` maps hours after the newest nowcast hour to the newest cycle's f001–f072 (f000 duplicates n006). File-name pattern and globs accept `f` steps.
  - Forecast files go in `data/plume/forecast/<cycle>/`, since each cycle replaces them. A run spanning "now" links nowcast hours up to the newest, then forecast hours.
  - The run id includes the forecast cycle; otherwise the result cache hands back an older cycle's forecast for an identical request.
  - The UI marks the forecast part of the timeline and the result names its cycle. Refresh on demand first (the first forecast run after a new cycle waits ~3 min); a scheduled fetch each cycle once hosted.
- Out of scope: the particle tab's surface files (same pattern, later); one S3 read writing both depth-averaged and 3D files (would halve PLUMES-mode fetches; deferred because fixed values are the default near field); cache eviction.
- To decide before building (recommendation first):
  - Longest on-demand fetch: 14 days (~13 min on the laptop if 4 workers scale). `duration_h` allows 62 days, ~1 h of fetching, too fragile for a browser request; longer windows stay with the CLI.
  - Disk: no eviction yet. Depth-averaged files are ~7 GB per month of data, and the cache grows only with what is run. Revisit on the VM.
  - Forecast refresh: on demand first, scheduled once hosted.

### Plume phases

| # | Phase | Exit criterion |
|---|---|---|
| A | Jul–Aug depth-averaged data; `PlumeRequest` with one source and PLUMES inputs; ΔTA/ΔDIC gridding to disk; outfall layer; plume mode in the page (click to add a source, dilution heat map, receptors) | Synthetic tests pass; tidal-jet blurring at the bay entrance quantified; 2-month Sequim run shown in the browser |
| B | Background TA/DIC; PyCO2SYS (approved); ΔpH and Ω maps; Admiralty TD1 run | Nonlinearity test passes; TD1 ΔpH matches SSM's footprint and order of magnitude |
| C | Several sources (per-source shares); open-boundary culling; longer windows via the Phase 1 data layer; 3D gate on the Sequim box; Sequim WRF (WA0022349) as second source | Superposition test passes; month-scale multi-source run; 3D layer check passes or the fallback is chosen |
| D | Data on demand (decision 20): nowcast archive fetched per run, then forecast to +72 h | A run outside the cached hours fetches and runs from the browser with progress shown; a forecast run names its cycle and is not served stale after a new cycle |

### Plume risks
- **OceanTracker 3D path:** Phase 0 surface particles moved 3–7× too slowly. **Cause found and fixed (2026-10-09): layer order.** FVCOM stores layers surface-first (`siglay[0] = -0.0158`); OceanTracker 0.5.3.9's `FVCOMreader` flips the sigma fractions to its bottom-first order (`build_vertical_grid`) but not the velocities (`read_file_var_as_4D_nodal_values`), so surface particles got near-bottom velocities. Its SHYFEM and GLORYS readers flip both. `SSCOFS3DReader` (`oceantracker_engine.py`) flips the data too; test `test_3d_reader_puts_surface_currents_at_the_surface` (known sheared current on the Sequim mesh: the stock reader moved a 1 m-deep particle −29 m in 2 h against ~2,160 m expected, and a 90 m-deep one 1,217 m against ~0).
  - Layer check (`scripts/layer_check.py`, 3 h from 2026-07-01 01:00 UTC, fixed-depth particles at 7 site/depths, vertical velocity zeroed, tide kept): the fixed reader's end points are 25–280 m from direct integration of the layer velocities; the stock reader's 74–4,064 m, matching direct integration with the layers reversed. Near the surface (2 m) the fixed reader moves within 2–3% of direct (3,408 vs 3,472 m; 4,373 vs 4,494 m). **Gate passed for near-surface and trap-depth use.**
  - Open residual: 80 m down in 98 m of water both readers move ~14% less than direct (1,366–1,379 vs 1,592 m). Not the layer order (both readers agree), not the bed log layer (bottom cell only). Uniform-sigma regridding is untested: `FVCOMreader` fails with `regrid_z_to_sigma_levels=False`. May be our integrator's nearest-element sampling. Matters for deep releases, not for near-surface plumes.
  - Still worth an upstream issue (FVCOM layer order).
- **No vertical diffusivity in SSCOFS** (`kh`/`km` absent). OceanTracker takes a constant `A_V` or an `A_Z_profile` field (with the Visser drift correction). Plan: Kz from a Richardson-number scheme (shear from u/v layers, N² from temp/salinity) at fetch time. Largest single uncertainty in 3D.
- **Horizontal diffusivity uncalibrated:** results depend strongly on `A_H`; run sensitivity cases and label output as screening.
- **Particle noise:** relative noise in a cell scales as 1/√n, worst at high dilution where thresholds sit. Size the particle count from release rate × duration; coarser cells or kernel smoothing far from the source; flag noisy cells.
- **Build-up in an enclosed bay:** Sequim Bay's flushing time may be longer than the window. Plot effluent mass in the bay over time and say whether it levelled off.
- **Tidal jets:** element velocities are IDW-interpolated to nodes (see Risks above), which may blur the bay-entrance jet.
- **Outfall data quality:** NPDES coordinates may be the plant, not the diffuser (Sequim WRF has two pairs: 48.0914, -123.0364 and 48.0803, -123.0842).
- **Ocean edges act as coast:** over month-scale runs, alkalinity that should leave via the Strait of Juan de Fuca piles up at the shelf edge. Open-boundary culling (Phase 2 todo) is required before Phase C long runs.
- **Download size:** ~120 GB for Jul–Aug; better done on the us-east-1 VM if the laptop link is slow.

### Plume open questions
- PNNL outfall: flow, port depth, diffuser design, effluent TA, DIC, temperature and salinity.
- Near-field values at Sequim: a PLUMES run from co-authors, or agreed estimates.
- Background chemistry: TA from a regional salinity regression; DIC from SSM output or observations.
- Exposure thresholds: which pH / Ω values and averaging times matter.
- Concentration grid cell size (no smaller than the mesh, ~150 m in the bay).

### Plume success criteria
- Synthetic Gaussian-plume, mass-budget and (Phase C) superposition tests pass.
- A 2-month Sequim run shows dilution maps and an effluent-mass-in-bay series that either levels off or is flagged as not levelled off.
- Admiralty TD1 ΔpH matches SSM's Jul–Aug footprint and order of magnitude.

## Changelog

| Date | Change | Rationale |
|---|---|---|
| 2026-10-08 | Added plume dilution tool (Sequim Bay OAE effluent): decisions 10–19, phases A–C, risks, open questions | Audit of the particle-plume sketch; OAE purpose per Savoie et al.; site, validation case, window and PyCO2SYS decided by the user |
| 2026-10-08 | Decisions 13 and 16 updated to the Phase A build: near-field dilution is a cap in depth-averaged mode; fetch is u/v only for now (measured 42 MB/h read, 10.5 MB/h stored) | Measured during the build; 3D variables deferred until Phase C needs them |
| 2026-10-08 | Decision 16: plume time step 300 s by default (was 60 s from CFL), settable per run | dt check: 300 s within particle noise of 60 s; run time scales with step count, so runs are 2.6–5× faster |
| 2026-10-09 | Decision 13: near field to be coupled hourly through `plumes2` (was: no built-in model). Decision 16: Sequim-box 3D files fetched now, not at Phase C | User asked for near-field resolution; PLUMES2.0 itself is GUI-only; UM3 needs current and T/S profiles at the outfall |
| 2026-10-09 | Phase C multi-source pulled forward: up to 6 sources per run on one map, per-source fields in the API, blended per-source dye colours; "lowest dilution over the run" is now the summed field's peak. The UI's near field defaults to fixed values (PLUMES each hour is opt-in), so a default run reads only the depth-averaged files | User asked for two or more sources with a colour per source, and for a 2D-only default for the prototype; superposition test passes (Sequim WRF + Sequim Bay point) |
| 2026-10-09 | Risks: OceanTracker 3D path cause found (FVCOM layer order) and fixed by `SSCOFS3DReader`; 3D layer check passed for near-surface use, deep residual (~14%) open | `scripts/layer_check.py` and `test_3d_reader_puts_surface_currents_at_the_surface` |
| 2026-10-09 | Decision 20 and plume Phase D: runs fetch their own missing hours, then forecast mode; scoped, not built | User asked whether 3D data can be pulled in real time; measured ~9 s per hour per worker and S3 posting ~3.5 h after cycle time |
