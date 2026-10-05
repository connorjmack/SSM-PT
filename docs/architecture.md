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
| `scripts/` | CLIs and one-off scripts. Phase 0 test scripts: `phase0_fetch.py` downloads full files to `data/phase0/full/`; `phase0_slim.py` writes 2D surface files to `data/phase0/slim2d/` (23 MB/hour); `phase0_run.py` runs OceanTracker into `runs/phase0/<tag>/`; `phase0_reader.py` holds the `SSCOFS2DReader` subclass, imported by OceanTracker via `add_path`. |
| `docs/` | `plan.md` (decisions), `todo.md` (checklist), this file. |
| `pyproject.toml`, `uv.lock`, `.python-version` | Environment source of truth. |

`.gitignore` excludes `data/`, `runs/`, `.venv/` and OceanTracker output dirs (`oceantracker_output/`, `root_output_dir/`).

## Implementation reference

### Modules (stubs)

| Module | Stated job |
|---|---|
| `catalog` | Pure function: valid hour → S3 key + source label (nowcast/forecast). |
| `grid` | Extract the static mesh once; domain-outline GeoJSON from boundary edges (edges in exactly one triangle). |
| `fields` | `ensure_hours(hours)`: parallel HTTP range reads of surface fields into a per-hour local cache; atomic (tmp + rename), idempotent. |
| `engine/base` | Tracker interface: `run(release, params) -> tracks`. |
| `engine/oceantracker_engine` | OceanTracker adapter. |
| `currents` | Surface-current arrows for the map: the model element nearest each 30 px screen cell of the view, at one hour. |
| `api/app` | FastAPI: `GET /meta`, `POST /runs`, `GET /runs/{id}`, `GET /runs/{id}/tracks`, `GET /currents`. |
| `api/jobs` | Process pool with warm workers; run id = hash of normalized request. |

### Environment

- Python 3.12 (`.python-version`); `requires-python = ">=3.12,<3.14"`.
- Build backend hatchling; wheel package `backend/ssm_pt`.
- Direct dependencies pinned `==` in `pyproject.toml`; dev group: pytest. Full resolution in `uv.lock`.

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
