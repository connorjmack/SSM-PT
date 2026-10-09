"""Plume dilution: continuous releases from outfalls, gridded to effluent fraction (1 / dilution).

Depth-averaged (plan.md decisions 12-17): particles carry effluent volume, OceanTracker counts them on a
regular UTM grid, and fraction = particle volume / (cell area x water depth). The near field caps how
concentrated the plume can be: fixed values from PLUMES, or (decision 13) plumes2's UM3 run every hour on the
SSCOFS current, T and S profile at the source from the Sequim-box 3D files. Excess TA/DIC scale linearly with
the fraction.
"""
import json
import math
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("OCEANTRACKER_NUMBA_CACHING", "1")  # must be set before oceantracker import

import netCDF4
import numpy as np
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

H_MIN = 0.5  # m; floor on water depth so cells on drying flats do not divide by ~0
RELEASE_INTERVAL_S = 600  # one pulse of particles every 10 min stands for that interval's discharge
MAX_SOURCES = 6  # past this the map's per-source dye colours are hard to tell apart
MAX_MAP_KM = 200  # longest side of the map
FILE_NAME = re.compile(r"sscofs\.t(\d\d)z\.(\d{8})\.fields\.n(\d{3})\.nc$")
NEAR_FIELD_REACH_M = 500  # a source farther than this from every 3D element is outside the Sequim box
NF_FIELDS = ("dilution", "diameter_m", "trap_depth_m", "width_m", "current_m_s")  # hourly near-field outputs


def valid_hour(name: str) -> datetime:
    """Valid time of a nowcast file from its name: step n00k of cycle HH is valid at HH - 6 + k hours."""
    m = FILE_NAME.match(name)
    return datetime.strptime(m[2], "%Y%m%d").replace(tzinfo=UTC) + timedelta(hours=int(m[1]) - 6 + int(m[3]))


def window_files(data_dir: Path, start: datetime, end: datetime) -> list[Path]:
    """The hourly files from an hour before start to an hour after end, in time order."""
    files = [p for p in Path(data_dir).glob("sscofs.*.fields.n*.nc")
             if start - timedelta(hours=1) <= valid_hour(p.name) <= end + timedelta(hours=1)]
    return sorted(files, key=lambda p: valid_hour(p.name))


def link_window(data_dir: Path, start: datetime, end: datetime, dst: Path):
    """Link the run's window_files into dst. OceanTracker opens every file in its input folder to catalog times
    (about 9 ms each; Jul-Aug is 1488 files), so a run reads only its own."""
    dst.mkdir(parents=True, exist_ok=True)
    for p in window_files(data_dir, start, end):
        (dst / p.name).symlink_to(p.resolve())


class Diffuser(BaseModel):
    """Port layout for the near field run every hour through plumes2. Defaults are Ebb Carbon's Macoma diffuser
    (plumes2 reference case55), a placeholder until PNNL-Sequim's arrive."""
    model_config = ConfigDict(extra="forbid")
    n_ports: int = Field(25, ge=1, le=500)
    port_diameter_m: float = Field(0.0127, gt=0, le=5)
    port_spacing_m: float = Field(0.6096, ge=0, le=100)
    vertical_angle_deg: float = Field(45.0, ge=-90, le=90)
    bearing_deg: float | None = Field(None, ge=0, lt=360)  # compass direction the jets point; None = downstream
    port_depth_m: float = Field(2.0, gt=0, le=500)  # below mean sea level: the port is fixed, the tide moves
    effluent_salinity: float | None = Field(None, ge=0, le=60)  # None = the water at the port (seawater intake)
    effluent_temperature_c: float | None = Field(None, ge=-2, le=60)


class Source(BaseModel):
    """An outfall. The near field is either fixed values from PLUMES (defaults are the paper's Admiralty case) or,
    with a diffuser, plumes2 run every hour, which then replaces near_field_dilution and plume_diameter_m."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field("Source", max_length=120)
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    flow_m3s: float = Field(gt=0, le=100)
    near_field_dilution: float = Field(8.0, ge=1, le=10_000)  # flux-averaged dilution where the near field ends
    plume_diameter_m: float = Field(1.6, gt=0, le=1000)  # sets the release cloud; matters more in 3D
    plume_depth_m: float = Field(0.79, ge=0, le=500)  # trap depth; unused until 3D
    excess_ta: float = Field(5500.0, ge=0, le=100_000)  # effluent TA minus ambient, umol/kg
    excess_dic: float = Field(2300.0, ge=0, le=100_000)  # effluent DIC minus ambient, umol/kg
    diffuser: Diffuser | None = None  # None = the fixed near-field values above


class PlumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[Source] = Field(min_length=1, max_length=MAX_SOURCES)
    start: AwareDatetime
    duration_h: float = Field(gt=0, le=24 * 62)
    n_particles: int = Field(50_000, ge=1000, le=500_000)
    diffusivity_m2s: float = Field(1.0, ge=0, le=100)
    mixing_depth_m: float | None = Field(None, gt=0, le=500)  # None = whole water column
    cell_m: float = Field(150.0, ge=50, le=2000)
    span_km: float = Field(20.0, ge=2, le=MAX_MAP_KM)  # the map reaches half this far around each source
    output_interval_min: int = Field(60, ge=10, le=360)
    # Run time is mostly a fixed cost per step. 300 s matched 60 s within particle noise on a 3-day Sequim run
    # (scripts/plume_dt_check.py); OceanTracker follows particles across several cells in one step
    dt_s: int = Field(300, gt=0, le=RELEASE_INTERVAL_S)

    @field_validator("start")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)  # one canonical form, so equal requests hash to one run id

    @field_validator("dt_s")
    @classmethod
    def _divides_release_interval(cls, v: int) -> int:
        if RELEASE_INTERVAL_S % v:  # otherwise pulses fall between steps and the released volume drifts
            raise ValueError(f"the time step must divide the {RELEASE_INTERVAL_S} s release interval")
        return v

    @model_validator(mode="after")
    def _map_not_too_big(self):
        if len(self.sources) > 1:  # memory and the result grow with the map's cells
            _, cols, rows = map_grid(source_xy(self.sources), self.span_km, self.cell_m)
            side = max(cols, rows) * self.cell_m / 1000
            if side > MAX_MAP_KM:
                raise ValueError(f"The map around these sources would be {side:.0f} km across, over the "
                                 f"{MAX_MAP_KM} km limit; lower the map size or run far-apart sources separately.")
        return self


def source_xy(sources: list[Source]) -> np.ndarray:
    """(source, 2) UTM 10N positions."""
    from ssm_pt.engine.oceantracker_engine import TO_UTM

    return np.array([TO_UTM.transform(s.lon, s.lat) for s in sources])


def map_grid(xy: np.ndarray, span_km: float, cell_m: float) -> tuple[np.ndarray, int, int]:
    """Centre, cols and rows of the map: the sources' bounding box plus span_km / 2 on every side, in whole cells,
    so every source has the room a lone source gets (a span_km square around it)."""
    reach = max(1, round(span_km * 1000 / cell_m))
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    cols, rows = (np.round((hi - lo) / cell_m).astype(int) + reach).tolist()
    return (lo + hi) / 2, cols, rows


def effluent_fraction(count, depth_sum, volume_per_particle, cell_area, near_field_dilution, mixing_depth_m=None):
    """Effluent volume fraction per (time, source, row, col) cell from OceanTracker gridded counts.

    depth_sum is the sum of total water depth over the counted particles, so depth_sum / count is the mean
    depth where they are. volume_per_particle is per source; near_field_dilution is per source, or per
    (time, source) when the near field changes hour by hour.
    """
    with np.errstate(invalid="ignore", divide="ignore"):
        depth = np.where(count > 0, depth_sum / count, np.inf)
    depth = np.maximum(depth, H_MIN)
    if mixing_depth_m is not None:
        depth = np.minimum(depth, mixing_depth_m)
    v = np.asarray(volume_per_particle)[None, :, None, None]
    nfd = np.asarray(near_field_dilution, dtype=float)
    cap = 1 / (nfd[None, :, None, None] if nfd.ndim == 1 else nfd[:, :, None, None])
    return np.minimum(count * v / (cell_area * depth), cap)


# ── Coupled near field: plumes2 (Ebb Carbon's Python port of PLUMES2.0 UM3) on the Sequim-box profiles ──

def nearest_element(path: Path, x: float, y: float) -> tuple[int, float]:
    """Element of a Sequim-box file whose centre is nearest (x, y), UTM metres, and how far it is."""
    with netCDF4.Dataset(path) as nc:
        nc.set_auto_mask(False)
        dx, dy = nc["xc"][:] - x, nc["yc"][:] - y
    k = int(np.argmin(dx ** 2 + dy ** 2))
    return k, float(np.hypot(dx[k], dy[k]))


def ambient_profile(path: Path, element: int) -> dict:
    """Water column at one element of a Sequim-box file: layer-centre depths below the surface, u, v, T, S
    per layer, bed depth h and surface elevation zeta. Node values are averaged over the element's corners."""
    with netCDF4.Dataset(path) as nc:
        nc.set_auto_mask(False)
        n = nc["nv"][:, element] - 1  # 1-based
        h, zeta = float(nc["h"][:][n].mean()), float(nc["zeta"][0][n].mean())
        return {"depth": -nc["siglay"][:][:, n].mean(axis=1) * (h + zeta),
                "u": nc["u"][0, :, element], "v": nc["v"][0, :, element],
                "temp": nc["temp"][0][:, n].mean(axis=1), "salinity": nc["salinity"][0][:, n].mean(axis=1),
                "h": h, "zeta": zeta}


def current_at(p: dict, depth: float) -> tuple[float, float]:
    """Speed and direction (degrees CCW from east, plumes2's convention) in the layer nearest depth."""
    k = int(np.argmin(np.abs(p["depth"] - depth)))
    return float(np.hypot(p["u"][k], p["v"][k])), float(np.degrees(np.arctan2(p["v"][k], p["u"][k])) % 360)


def near_field_case(p: dict, d: Diffuser, flow_m3s: float):
    """plumes2 Case for one hour's profile. The port sits port_depth_m below mean sea level, so its depth below
    the surface follows the tide; the profile runs from the surface to the seabed. Far field off: OceanTracker
    is the far field."""
    from plumes2 import config as p2

    if p["h"] <= d.port_depth_m:
        raise ValueError(f"The port ({d.port_depth_m} m below mean sea level) is at or below the model seabed "
                         f"here ({p['h']:.1f} m).")
    port_depth, elevation = d.port_depth_m + p["zeta"], p["h"] - d.port_depth_m
    bottom = port_depth + elevation  # as plumes2 computes the seabed; the profile must reach it exactly
    k = np.r_[0, np.arange(len(p["depth"])), len(p["depth"]) - 1]  # surface and seabed copy the end layers
    depth = np.r_[0.0, np.asarray(p["depth"], dtype=float), bottom]  # float64, or float32 files round the seabed short
    speed = np.hypot(p["u"], p["v"])[k]
    direction = (np.degrees(np.arctan2(p["v"], p["u"])) % 360)[k]
    temp, salt = p["temp"][k], p["salinity"][k]
    levels = [p2.AmbientLevel(depth=float(z), current_speed=float(s), current_direction=float(a), salinity=float(sa),
                              temperature=float(t), farfield_speed=float(s), farfield_direction=float(a))
              for z, s, a, sa, t in zip(depth, speed, direction, salt, temp)]
    jet = current_at(p, port_depth)[1] if d.bearing_deg is None else (90.0 - d.bearing_deg) % 360
    return p2.Case(
        diffuser=p2.Diffuser(port_diameter=d.port_diameter_m, port_elevation=elevation,
                             vertical_angle=d.vertical_angle_deg, horizontal_angle=jet, n_ports=d.n_ports,
                             port_spacing=d.port_spacing_m, port_depth=port_depth),
        effluent=p2.Effluent(
            flow=flow_m3s,
            salinity=d.effluent_salinity if d.effluent_salinity is not None else float(np.interp(port_depth, depth, salt)),
            temperature=(d.effluent_temperature_c if d.effluent_temperature_c is not None
                         else float(np.interp(port_depth, depth, temp)))),
        mixing_zone=p2.MixingZone(acute_distance=0, chronic_distance=0),  # mixing-zone values need its far field
        ambient=p2.AmbientProfile(levels=levels),
        far_field=p2.FarFieldSettings(enabled=False))


def near_field_hour(path: str, element: int, diffuser_json: str, flow_m3s: float) -> dict:
    """One plumes2 run on one hour's profile (a process-pool task): the dilution, plume size and depth where the
    near field ends. A failed hour (the port out of the water at low tide, say) comes back as {"failed": why}."""
    import warnings

    from plumes2 import run

    p = ambient_profile(Path(path), element)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            case = near_field_case(p, Diffuser.model_validate_json(diffuser_json), flow_m3s)
            r = run(case)
        except Exception as e:  # noqa: BLE001  # reported per hour; the other hours still count
            return {"failed": str(e).splitlines()[0]}
    end = r.nearfield.iloc[-1]
    speed, direction = current_at(p, case.diffuser.port_depth)
    return {"dilution": float(r.final_dilution), "diameter_m": float(end["plume_diameter_m"]),
            "trap_depth_m": float(end["depth_m"]), "current_m_s": speed,
            "width_m": float(case.diffuser.wastefield_width(float(end["plume_diameter_m"]), direction)),
            "warnings": sorted({str(w.message).splitlines()[0] for w in caught})}


def near_field_series(files: list[Path], element: int, d: Diffuser, flow_m3s: float, workers: int | None = None) -> dict:
    """Hourly near field at one source, one plumes2 run per file in parallel (about 1.6 s each)."""
    from concurrent.futures import ProcessPoolExecutor

    workers = workers or max(1, min(len(files), (os.cpu_count() or 2) - 2))
    with ProcessPoolExecutor(workers) as ex:
        rows = list(ex.map(near_field_hour, [str(f) for f in files], [element] * len(files),
                           [d.model_dump_json()] * len(files), [flow_m3s] * len(files)))
    failed = [f"{valid_hour(f.name):%Y-%m-%d %H}Z: {r['failed']}" for f, r in zip(files, rows) if "failed" in r]
    if len(failed) == len(files):
        raise ValueError(f"The near field failed every hour, e.g. {failed[0]}")
    out = {k: np.array([r.get(k, np.nan) for r in rows]) for k in NF_FIELDS}
    out["time"] = np.array([valid_hour(f.name).timestamp() for f in files])
    out["failed"] = failed
    out["warnings"] = sorted({w for r in rows for w in r.get("warnings", [])})
    return out


class PlumeEngine:
    """Runs depth-averaged plume releases on the files in data_dir (UTM 10N metres)."""

    def __init__(self, data_dir: Path, file_mask: str = "sscofs.*.fields.n*.nc", nearfield_dir: Path | None = None):
        self.data_dir, self.file_mask = Path(data_dir), file_mask
        # 3D profiles for the coupled near field: scripts/fetch_3d.py --clip writes them beside the davg files
        self.nearfield_dir = Path(nearfield_dir) if nearfield_dir else self.data_dir.parent / "sequim3d"

    def near_field(self, s: Source, x: float, y: float, start: datetime, end: datetime) -> dict:
        """Hourly plumes2 near field for a source with a diffuser, over the run's window of 3D files."""
        files = window_files(self.nearfield_dir, start, end)
        if not files or valid_hour(files[0].name) > start or valid_hour(files[-1].name) < end:
            raise ValueError(f"The 3D files in {self.nearfield_dir} do not cover this run; fetch them with "
                             "scripts/fetch_3d.py --clip, or use fixed near-field values.")
        element, dist = nearest_element(files[0], x, y)
        if dist > NEAR_FIELD_REACH_M:
            raise ValueError(f"{s.name}: the PLUMES near field needs 3D currents and T/S, which cover Sequim Bay only; "
                             f"this source is {dist / 1000:.1f} km outside them. Use fixed near-field values for it.")
        try:
            return near_field_series(files, element, s.diffuser, s.flow_m3s)
        except ValueError as e:  # with several sources, say which one to fix
            raise ValueError(f"{s.name}: {e}") from e

    def run(self, req: PlumeRequest, out_dir: Path) -> dict:
        from oceantracker.main import OceanTracker
        from oceantracker.read_output.python import load_output_files

        out_dir = Path(out_dir)
        xy = source_xy(req.sources)
        centre, cols, rows = map_grid(xy, req.span_km, req.cell_m)
        duration_s = req.duration_h * 3600
        pulses = int(duration_s // RELEASE_INTERVAL_S) + 1
        pulse_size = max(1, round(req.n_particles / (pulses * len(req.sources))))
        volume = np.array([s.flow_m3s * RELEASE_INTERVAL_S / pulse_size for s in req.sources])
        end = req.start + timedelta(seconds=duration_s)
        nf = [self.near_field(s, x, y, req.start, end) if s.diffuser else None for s, (x, y) in zip(req.sources, xy)]
        # Particles start across the wastefield where the near field ends (the typical hour's, when coupled)
        radius = [(np.nanmedian(n["width_m"]) if n else s.plume_diameter_m) / 2 for s, n in zip(req.sources, nf)]

        ot = OceanTracker()
        ot.settings(run_output_dir=str(out_dir), time_step=req.dt_s, NUMBA_cache_code=True,
                    write_tracks=False, max_run_duration=duration_s)
        ot.add_class("dispersion", A_H=req.diffusivity_m2s)
        start = req.start.astimezone(UTC).replace(tzinfo=None).isoformat()
        for i, (s, (x, y)) in enumerate(zip(req.sources, xy)):
            ot.add_class("release_groups", name=f"source{i}", points=[[x, y]], start=start, duration=duration_s,
                         release_interval=RELEASE_INTERVAL_S, pulse_size=pulse_size, release_radius=float(radius[i]))
        ot.add_class("particle_statistics", name="grid", class_name="GriddedStats2D_timeBased",
                     grid_center=centre.tolist(), rows=rows, cols=cols, span_x=cols * req.cell_m, span_y=rows * req.cell_m,
                     update_interval=req.output_interval_min * 60, particle_property_list=["water_depth", "tide"])
        with tempfile.TemporaryDirectory() as hindcast:
            link_window(self.data_dir, req.start, end, Path(hindcast))
            ot.add_class("reader", class_name="ssm_pt.engine.oceantracker_engine.SSCOFS2DReader",
                         input_dir=hindcast, file_mask=self.file_mask, geographic_coords=False)
            case_info = ot.run()
        if case_info is None:  # OceanTracker logs its errors and returns None instead of raising
            log = (out_dir / "run_log.txt").read_text()
            if "No points are inside domain" in log:
                raise ValueError("A source is on land or outside the model domain.")
            raise RuntimeError(f"OceanTracker failed; see {out_dir}/error_warnings.err")

        d = load_output_files.load_stats_data(case_info, name="grid")
        cap = np.tile([s.near_field_dilution for s in req.sources], (len(d["time"]), 1)).astype(float)  # (time, source)
        for i, n in enumerate(nf):
            if n is not None:  # the hourly near field at each output time; failed hours are bridged
                ok = np.isfinite(n["dilution"])
                cap[:, i] = np.interp(d["time"], n["time"][ok], n["dilution"][ok])
        f = effluent_fraction(d["count"], d["sum_water_depth"] + d["sum_tide"], volume, req.cell_m ** 2, cap,
                              req.mixing_depth_m)
        # Released and still-on-the-grid effluent, per source, for the build-up series
        released = d["num_released"] * volume[None, :]
        on_grid = d["count"].sum(axis=(2, 3)) * volume[None, :]
        x_c, y_c = d["x"][0], d["y"][0]  # cell centres
        write_plume_file(out_dir / "plume.nc", d["time"], x_c, y_c, f, released, on_grid, req, nf)
        return plume_meta(out_dir / "plume.nc", req, pulse_size * pulses * len(req.sources))


def write_plume_file(path: Path, time_s, x_c, y_c, f, released, on_grid, req: PlumeRequest, nf=None):
    """nf: per source, its hourly near-field series (near_field_series) or None for fixed values."""
    with netCDF4.Dataset(path, "w") as nc:
        nc.setncatts({"title": "SSM-PT depth-averaged plume", "crs": "EPSG:32610", "cell_m": req.cell_m,
                      "request": req.model_dump_json()})
        nc.createDimension("time", len(time_s))
        nc.createDimension("source", f.shape[1])
        nc.createDimension("row", len(y_c))
        nc.createDimension("col", len(x_c))
        nc.createVariable("time", "f8", ("time",)).setncatts({"units": "seconds since 1970-01-01 00:00:00"})
        nc["time"][:] = time_s
        nc.createVariable("x", "f8", ("col",))[:] = x_c
        nc.createVariable("y", "f8", ("row",))[:] = y_c
        v = nc.createVariable("fraction", "f4", ("time", "source", "row", "col"), zlib=True, complevel=1)
        v.setncatts({"long_name": "effluent volume fraction (1 / dilution), depth-averaged"})
        v[:] = f
        # Each source's share at the time the combined fraction peaks, so the shares add up to that peak
        peak = f.sum(axis=1).argmax(axis=0)[None, None]  # (1, 1, row, col) time index
        v = nc.createVariable("fraction_max", "f4", ("source", "row", "col"), zlib=True)
        v.setncatts({"long_name": "each source's fraction at the time the summed fraction peaks"})
        v[:] = np.take_along_axis(f, peak, axis=0)[0]
        nc.createVariable("fraction_mean", "f4", ("source", "row", "col"), zlib=True)[:] = f.mean(axis=0)
        nc.createVariable("released_m3", "f8", ("time", "source"))[:] = released
        nc.createVariable("on_grid_m3", "f8", ("time", "source"))[:] = on_grid
        if nf and any(n is not None for n in nf):
            import plumes2

            hours = next(n["time"] for n in nf if n is not None)  # every source runs on the same window of hours
            nc.createDimension("nf_time", len(hours))
            nc.createVariable("nf_time", "f8", ("nf_time",)).setncatts({"units": "seconds since 1970-01-01 00:00:00"})
            nc["nf_time"][:] = hours
            for k in NF_FIELDS:
                nc.createVariable(f"nf_{k}", "f4", ("nf_time", "source"))[:] = np.column_stack(
                    [n[k] if n is not None else np.full(len(hours), np.nan) for n in nf])
            nc.setncatts({"near_field_model": f"plumes2 {plumes2.__version__} (UM3), hourly",
                          "near_field_notes": json.dumps([{"failed": n["failed"], "warnings": n["warnings"]}
                                                          if n is not None else None for n in nf])})


def plume_meta(path: Path, req: PlumeRequest, n_particles: int) -> dict:
    """JSON-ready summary of a plume file: times, grid placement, build-up series."""
    from ssm_pt.engine.oceantracker_engine import TO_LONLAT

    iso = lambda s: datetime.fromtimestamp(s, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with netCDF4.Dataset(path) as nc:
        nc.set_auto_mask(False)
        t, x, y = nc["time"][:], nc["x"][:], nc["y"][:]
        released, on_grid = nc["released_m3"][:].sum(axis=1), nc["on_grid_m3"][:].sum(axis=1)
        f_max = nc["fraction_max"][:].sum(axis=0)
        near_field = [None] * len(req.sources)
        if "nf_time" in nc.variables:
            hours = [iso(s) for s in nc["nf_time"][:]]
            for i, note in enumerate(json.loads(nc.getncattr("near_field_notes"))):
                if note is not None:  # NaN (a failed hour) becomes null: JSON has no NaN
                    near_field[i] = {"times": hours, **note, **{k: [round(float(v), 3) if np.isfinite(v) else None
                                                                    for v in nc[f"nf_{k}"][:, i]] for k in NF_FIELDS}}
    half = req.cell_m / 2
    # Corner lon/lat of the UTM grid; Leaflet draws it as a lat/lon rectangle, which is within a fraction of a
    # cell near the zone's central meridian (-123), where the Salish Sea sites are
    (w, e), (s, n) = TO_LONLAT.transform([x[0] - half, x[-1] + half], [y[0] - half, y[-1] + half])
    with np.errstate(invalid="ignore", divide="ignore"):
        on_grid_pct = np.where(released > 0, 100 * on_grid / released, 0)
    return {
        "times": [iso(s) for s in t],
        "bounds": [[float(s), float(w)], [float(n), float(e)]],
        "rows": len(y), "cols": len(x), "cell_m": req.cell_m,
        "sources": [s.model_dump() for s in req.sources],
        "n_particles": n_particles,
        "released_m3": np.round(released, 1).tolist(),
        "on_grid_pct": np.round(on_grid_pct, 2).tolist(),
        "min_dilution": float(1 / f_max.max()) if f_max.max() > 0 else None,
        "mixing_depth_m": req.mixing_depth_m,
        "near_field": near_field,  # per source: hourly plumes2 series, or None for fixed values
    }


def read_fraction(path: Path, frame: int | str) -> np.ndarray:
    """(source, row, col) effluent fraction: one output time, or 'max' / 'mean' over the run ('max' is each
    source's share when the summed fraction peaks)."""
    with netCDF4.Dataset(path) as nc:
        nc.set_auto_mask(False)
        if frame in ("max", "mean"):
            return nc[f"fraction_{frame}"][:]
        return nc["fraction"][int(frame)]


def read_receptor(path: Path, lon: float, lat: float) -> dict | None:
    """Effluent fraction over time in the grid cell containing (lon, lat), summed and (several sources) per
    source, or None outside the grid."""
    from ssm_pt.engine.oceantracker_engine import TO_UTM

    x, y = TO_UTM.transform(lon, lat)
    with netCDF4.Dataset(path) as nc:
        nc.set_auto_mask(False)
        xc, yc, cell = nc["x"][:], nc["y"][:], float(nc.getncattr("cell_m"))
        c, r = math.floor((x - xc[0]) / cell + 0.5), math.floor((y - yc[0]) / cell + 0.5)
        if not (0 <= r < len(yc) and 0 <= c < len(xc)):
            return None
        f = nc["fraction"][:, :, r, c]  # (time, source)
    out = {"row": r, "col": c, "fraction": f.sum(axis=1).round(9).tolist()}
    if f.shape[1] > 1:
        out["by_source"] = f.T.round(9).tolist()
    return out
