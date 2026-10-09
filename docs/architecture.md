# Architecture

Current state: repo skeleton. Python modules contain only a docstring stating their job; no implementation yet.

## Repo map

| Path | What it is |
|---|---|
| `backend/ssm_pt/` | Python package `ssm_pt`, built from `backend/` (hatchling). Docstring stubs. |
| `backend/ssm_pt/engine/` | Tracker interface and OceanTracker adapter (stubs). |
| `backend/ssm_pt/api/` | FastAPI app and job pool (stubs). |
| `backend/tests/` | pytest suite (`testpaths` in `pyproject.toml`). Empty. |
| `frontend/` | Leaflet client. Empty. |
| `scripts/` | CLIs and one-off scripts. Phase 0 test scripts: `phase0_fetch.py` downloads full files to `data/phase0/full/`; `phase0_slim.py` writes 2D surface files, with 10 m wind, to `data/phase0/slim2d/` (27 MB/hour); `phase0_run.py` runs OceanTracker into `runs/phase0/<tag>/`; `phase0_reader.py` holds the `SSCOFS2DReader` subclass, imported by OceanTracker via `add_path`. |
| `docs/` | `plan.md` (decisions), `todo.md` (checklist), this file. |
| `pyproject.toml`, `uv.lock`, `.python-version` | Environment source of truth. |

`.gitignore` excludes `data/`, `runs/`, `.venv/` and OceanTracker output dirs (`oceantracker_output/`, `root_output_dir/`).

## Implementation reference

### Modules (stubs)

| Module | Stated job |
|---|---|
| `catalog` | Pure function: valid hour → S3 key + source label (nowcast/forecast). |
| `grid` | Extract the static mesh once; domain-outline GeoJSON from boundary edges (edges in exactly one triangle). Built so far: `clip_mesh(nv, elements)`, the kept elements' nodes (sorted) and their `nv` renumbered to them. |
| `fields` | `ensure_hours(hours)`: parallel HTTP range reads of surface fields into a per-hour local cache; atomic (tmp + rename), idempotent. |
| `engine/base` | Tracker interface: `run(release, params) -> tracks`. Request model, including the particle classes (water parcel, floating, decaying). |
| `engine/oceantracker_engine` | OceanTracker adapter. Floating adds a `Windage` velocity modifier (a share of the `wind_velocity` reader field) and, unless `washes_ashore` is false, a `WashAshore` trajectory modifier that sets our own status `ASHORE = -3` where OceanTracker would move a particle back off the coastline. Decaying adds OceanTracker's `AgeDecay` property as `remaining`. Numba caches compiled code on disk, except for OceanTracker's two particle-comparison kernels: they take a numba function as an argument, so the cache never matched across processes, grew by one entry per process and crashed once it held about 130. |
| `currents` | Surface-current arrows for the map: the model element nearest each 30 px screen cell of the view, at one hour. |
| `api/app` | FastAPI: `GET /meta`, `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/tracks`, `GET /currents` (`source=surface` or `plume`). Plume: `GET /plume/meta`, `GET /outfalls`, `POST /plumes`, `GET /plumes/{id}`, `/result`, `/frame/{k or max or mean}` (sparse effluent fraction), `/receptor`. Rescans `data/plume/davg/` when its file count changes and serves the unbroken hourly run from the first file. |
| `api/jobs` | Process pools with warm workers, one for particle runs and one for plume runs; run id = hash of normalized request. |
| `engine/plume` | Plume dilution. `PlumeRequest` with `sources` (one for now). One OceanTracker release group per source, a pulse every 10 min, time step `dt_s` from the request (default 300 s; must divide the 10 min release interval); `GriddedStats2D_timeBased` counts particles and sums water depth + tide on a regular UTM grid. Effluent fraction = count × volume per particle / (cell area × mean depth, floor 0.5 m, optional mixing depth), capped at 1 / near-field dilution. Writes `plume.nc` (fraction per time and source, max, mean, released and on-grid volume). Each run reads a temporary folder of links to its own hours (from an hour before start to an hour after end), because OceanTracker opens every file in its input folder to catalog times (about 9 ms per file). Run time is mostly a fixed cost per time step (about 8 ms, whatever the particle count), so dt sets it; numba threads and more particles barely change it. A source with a `diffuser` gets the coupled near field: `nearest_element` and `ambient_profile` read its water column from each hour's `data/plume/sequim3d/` file (`nearfield_dir`), `near_field_case` builds the plumes2 case, and `near_field_series` runs one plumes2 per hour in a process pool (about 1.6 s each). The hourly dilution, interpolated to each output time, replaces the fixed cap; the median wastefield width sets the release radius. `plume.nc` then also holds `nf_*` series on an `nf_time` axis, and the result's `near_field` lists them per source. |
| `fields` | Besides the stub: `depth_average(u, siglev)`, the layer-thickness-weighted water-column mean. |

### Plume data and scripts

- `scripts/fetch_3d.py`: all 10 layers of u/v from S3 (about 42 MB per hour, about 10 s per hour per worker; 8 workers give about 9 hours of data per minute), depth-averaged into `data/plume/davg/` in the slim2d layout with zlib (10.5 MB per hour; Jul–Aug 2026 is about 16 GB). SSCOFS sigma levels are the same at every node. `--clip` writes `data/plume/sequim3d/` instead: `u`, `v`, `ww`, `temp`, `salinity` (all layers), `zeta` and `wet_cells` for the elements whose centres lie in `SEQUIM_BOX` (10,489 elements, 5,736 nodes, about 1.8 MB per hour; Jul–Aug 2026 is 2.6 GB), with `nv` renumbered to the file's nodes and `node_index`/`nele_index` pointing into the full mesh. The grid is read once; each hour reads one index span per variable. S3 chunking: `u`/`v`/`ww` (1, 4, 144470) per element, `temp`/`salinity` (1, 5, 119867) per node, `zeta` one chunk; all uncompressed.
- `scripts/build_outfalls.py`: marine-energy `npdes_potw_outfalls.geojson` and `candidate_sites.csv` (not brownfields), within 1 km of an element centre, snapped to it → `data/outfalls.geojson` (352 points).
- `frontend/plume.js`: the plume tab, loaded by `index.html`; shares its map, basemaps, current arrows and helpers.

### Environment

- Python 3.13 (`.python-version`); `requires-python = ">=3.13,<3.14"`, because `plumes2` needs 3.13 or later. OceanTracker 0.5.3.9 runs on 3.13: the full suite passes, with the same pins as on 3.12.
- Build backend hatchling; wheel package `backend/ssm_pt`.
- Direct dependencies pinned `==` in `pyproject.toml`; dev group: pytest. Full resolution in `uv.lock`.
- `plumes2` (Ebb Carbon's MIT Python port of PLUMES2.0) is not on PyPI: `[tool.uv.sources]` pins it to commit `9791c80`. It brings PyCO2SYS.

## Data source reference: NOAA SSCOFS

### Location
- `s3://noaa-nos-ofs-pds/sscofs/netcdf/YYYY/MM/DD/`, anonymous access, us-east-1.
- Archive starts 2024-10-01. Same-day files are present.

### Files
- `sscofs.tHHz.YYYYMMDD.fields.{n000–n006,f000–f072}.nc`; cycles 03, 09, 15, 21z.
- Each cycle also has `stations.{nowcast,forecast}.nc` and `regulargrid.*.nc` (1.68 GB each, unused).
- Fields file: 210,525,170 bytes, NetCDF4/HDF5, uncompressed. FVCOM 4.4.7.

### Cycle timing
- Cycle `HH`: `n0k` valid at `HH − 6 + k` hours; `fNNN` valid at `HH + NNN`.
- Example `t03z`: n000 = 21:00 previous day, n006 = 03:00, f001 = 04:00.
- Continuous hindcast = n001–n006 of each cycle. Valid hours of day D:

| Hours (UTC) | Folder | File |
|---|---|---|
| 00–03 | D | `t03z` n003–n006 |
| 04–09 | D | `t09z` n001–n006 |
| 10–15 | D | `t15z` n001–n006 |
| 16–21 | D | `t21z` n001–n006 |
| 22–23 | D+1 | `t03z` n001–n002 |

### Grid
- 239,734 nodes; 433,410 elements; 10 `siglay`, 11 `siglev`. `siglay[0] = -0.0158`.
- `nv` (3, nele), 1-based. `nbe` present.

### Variables

| Variable | Shape / location | Notes |
|---|---|---|
| `u`, `v`, `ww` | (time, siglay, nele) float32 | Chunks (1, 4, 144470): 2.31 MB each; 3 chunks span all elements |
| `zeta`, `h`, `wet_nodes` | nodes | |
| `wet_cells`, `uwind_speed`, `vwind_speed`, `tauc` | elements | |
| `time` | float64 | seconds since 2018-01-01 00:00:00 |
| `Times` | char array | |

Absent: `ua`/`va`, `Itime`/`Itime2`.

### Coordinates
- `lon`/`lat` (nodes), `lonc`/`latc` (elements). `lon` is 0–360 (230.49–238.04); `lat` 44.39–52.11.
- `x`/`y`/`xc`/`yc`: UTM zone 10 metres. Global attrs `CoordinateProjection='proj=utm +ellps=WGS84 +zone=10'`, `CoordinateSystem='GeoReferenced'`.

### Measured read cost (remote, fsspec + h5py)
- Grid metadata open: ~1.3 s.
- Surface u+v, one hour: ~1.7 s, ~14 MB transferred (3 chunks × 2.31 MB per variable; chunks hold 4 layers).
- Whole-domain surface u+v in memory: ~3.5 MB/hour.
