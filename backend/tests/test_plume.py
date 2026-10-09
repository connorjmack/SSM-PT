"""Plume dilution tool: depth averaging, request validation, concentration from particle counts."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from ssm_pt.fields import depth_average


def test_depth_average_weights_layers_by_sigma_thickness():
    siglev = np.array([0.0, -0.1, -0.4, -1.0])  # layer thicknesses 0.1, 0.3, 0.6 of the water column
    u = np.array([[[1.0, 0.0], [2.0, 0.0], [3.0, 6.0]]])  # (time, siglay, nele)
    np.testing.assert_allclose(depth_average(u, siglev), [[0.1 + 0.6 + 1.8, 3.6]])


def test_depth_average_of_uniform_column_is_unchanged():
    siglev = np.linspace(0, -1, 11)
    u = np.full((2, 10, 5), 0.7)
    np.testing.assert_allclose(depth_average(u, siglev), np.full((2, 5), 0.7))


def test_clipped_mesh_keeps_its_elements_nodes_and_renumbers_them():
    from ssm_pt.grid import clip_mesh
    nv = np.array([[0, 1, 2, 3], [1, 3, 3, 5], [2, 2, 4, 4]])  # four triangles on six nodes, 0-based
    nodes, local = clip_mesh(nv, np.array([1, 2]))
    np.testing.assert_array_equal(nodes, [1, 2, 3, 4])  # sorted, so a file read can take one span
    np.testing.assert_array_equal(nodes[local], nv[:, [1, 2]])


# ── Request model ──

SOURCE = dict(lon=-123.0438, lat=48.0793, flow_m3s=0.1)
REQ = dict(sources=[SOURCE], start="2026-07-01T00:00:00Z", duration_h=24)


def test_source_defaults_are_the_paper_near_field():
    from ssm_pt.engine.plume import PlumeRequest
    s = PlumeRequest(**REQ).sources[0]
    assert s.near_field_dilution == 8.0 and s.plume_diameter_m == 1.6


def test_one_source_only_for_now():
    from ssm_pt.engine.plume import PlumeRequest
    with pytest.raises(ValidationError):
        PlumeRequest(**REQ | {"sources": [SOURCE, SOURCE]})
    with pytest.raises(ValidationError):
        PlumeRequest(**REQ | {"sources": []})


def test_time_step_defaults_to_300_s():
    from ssm_pt.engine.plume import PlumeRequest
    assert PlumeRequest(**REQ).dt_s == 300
    assert PlumeRequest(**REQ | {"dt_s": 60}).dt_s == 60


@pytest.mark.parametrize("dt", [0, 7, 450, 1200])  # each pulse must start on a step, so dt divides the 600 s interval
def test_time_step_must_divide_the_release_interval(dt):
    from ssm_pt.engine.plume import PlumeRequest
    with pytest.raises(ValidationError):
        PlumeRequest(**REQ | {"dt_s": dt})


@pytest.mark.parametrize("bad", [{"flow_m3s": 0}, {"near_field_dilution": 0.5}, {"excess_ta": -1}])
def test_source_rejects_unphysical_values(bad):
    from ssm_pt.engine.plume import PlumeRequest
    with pytest.raises(ValidationError):
        PlumeRequest(**REQ | {"sources": [SOURCE | bad]})


# ── Concentration from gridded particle counts ──

def test_effluent_fraction_is_particle_volume_over_water_volume():
    from ssm_pt.engine.plume import effluent_fraction
    count = np.array([[[[4.0, 0.0]]]])  # (time, group, row, col)
    depth_sum = 4 * np.array([[[[10.0, 0.0]]]])  # each particle sits in 10 m of water
    f = effluent_fraction(count, depth_sum, volume_per_particle=np.array([50.0]), cell_area=100.0 ** 2,
                          near_field_dilution=np.array([8.0]))
    np.testing.assert_allclose(f, [[[[4 * 50 / (100 ** 2 * 10), 0.0]]]])


def test_effluent_fraction_never_exceeds_the_near_field_dilution():
    from ssm_pt.engine.plume import effluent_fraction
    f = effluent_fraction(np.array([[[[1000.0]]]]), np.array([[[[1000.0]]]]), volume_per_particle=np.array([50.0]),
                          cell_area=100.0, near_field_dilution=np.array([8.0]))
    assert f.max() == pytest.approx(1 / 8)


def test_mixing_depth_limits_the_water_column():
    from ssm_pt.engine.plume import effluent_fraction
    args = dict(count=np.array([[[[2.0]]]]), depth_sum=np.array([[[[40.0]]]]), volume_per_particle=np.array([10.0]),
                cell_area=1e4, near_field_dilution=np.array([8.0]))
    full, surface = effluent_fraction(**args), effluent_fraction(**args, mixing_depth_m=5.0)
    assert surface[0, 0, 0, 0] == pytest.approx(full[0, 0, 0, 0] * 20 / 5)


def test_near_field_cap_can_change_every_output_time():
    from ssm_pt.engine.plume import effluent_fraction
    big = np.full((2, 1, 1, 1), 1000.0)  # (time, source, row, col), far more effluent than the cap allows
    f = effluent_fraction(big, big, volume_per_particle=np.array([50.0]), cell_area=100.0,
                          near_field_dilution=np.array([[8.0], [100.0]]))  # (time, source)
    np.testing.assert_allclose(f[:, 0, 0, 0], [1 / 8, 1 / 100])


# ── Coupled near field (plumes2, plan.md decision 13) ──

def write_column(path, h=10.0, zeta=0.5, u=(0.3, 0.1), v=(0.0, 0.1), temp=(12.0, 10.0), salt=(30.0, 31.0), dtype="f8"):
    """A Sequim-box file in the clipped layout: one element on three nodes, two sigma layers, one hour."""
    import netCDF4
    with netCDF4.Dataset(path, "w") as nc:
        for name, n in [("three", 3), ("nele", 1), ("node", 3), ("siglay", 2), ("time", 1)]:
            nc.createDimension(name, n)
        per_node = lambda a: np.repeat(np.asarray(a, float)[None, :, None], 3, axis=2)
        for name, dims, values in [
            ("nv", ("three", "nele"), [[1], [2], [3]]), ("xc", ("nele",), [500.0]), ("yc", ("nele",), [500.0]),
            ("h", ("node",), [h] * 3), ("siglay", ("siglay", "node"), [[-0.25] * 3, [-0.75] * 3]),
            ("zeta", ("time", "node"), [[zeta] * 3]),
            ("u", ("time", "siglay", "nele"), np.asarray(u, float)[None, :, None]),
            ("v", ("time", "siglay", "nele"), np.asarray(v, float)[None, :, None]),
            ("temp", ("time", "siglay", "node"), per_node(temp)), ("salinity", ("time", "siglay", "node"), per_node(salt)),
        ]:
            nc.createVariable(name, "i4" if name == "nv" else dtype, dims)[:] = values
    return path


def test_ambient_profile_is_the_element_water_column(tmp_path):
    from ssm_pt.engine.plume import ambient_profile
    p = ambient_profile(write_column(tmp_path / "a.nc"), 0)
    np.testing.assert_allclose(p["depth"], [0.25 * 10.5, 0.75 * 10.5])  # layer centres below the moving surface
    np.testing.assert_allclose(p["u"], [0.3, 0.1])
    np.testing.assert_allclose(p["temp"], [12.0, 10.0])
    np.testing.assert_allclose(p["salinity"], [30.0, 31.0])
    assert (p["h"], p["zeta"]) == (10.0, 0.5)


def test_nearest_element_and_how_far_it_is(tmp_path):
    from ssm_pt.engine.plume import nearest_element
    assert nearest_element(write_column(tmp_path / "a.nc"), 503.0, 504.0) == (0, 5.0)


def test_port_stays_fixed_above_the_seabed_as_the_tide_moves(tmp_path):
    from ssm_pt.engine.plume import Diffuser, ambient_profile, near_field_case
    case = near_field_case(ambient_profile(write_column(tmp_path / "a.nc"), 0), Diffuser(port_depth_m=2.0), 0.001)
    assert case.diffuser.port_depth == pytest.approx(2.5)  # 2 m below mean sea level, tide 0.5 m up
    assert case.diffuser.port_elevation == pytest.approx(8.0)
    depths = [lv.depth for lv in case.ambient.levels]
    assert depths[0] == 0 and depths[-1] == case.diffuser.bottom_depth  # profile spans surface to seabed


def test_profile_reaches_the_seabed_exactly_from_float32_files(tmp_path):
    import warnings

    from plumes2.config import GeometryWarning

    from ssm_pt.engine.plume import Diffuser, ambient_profile, near_field_case
    p = ambient_profile(write_column(tmp_path / "a.nc", h=7.1, zeta=0.37, dtype="f4"), 0)  # SSCOFS stores float32
    with warnings.catch_warnings():
        warnings.simplefilter("error", GeometryWarning)  # "profile stops above the seabed" if rounded short
        near_field_case(p, Diffuser(), 0.001)


@pytest.mark.parametrize("bearing, angle", [(None, 0.0), (0.0, 90.0), (90.0, 0.0), (270.0, 180.0)])
def test_jets_point_downstream_or_along_a_compass_bearing(tmp_path, bearing, angle):
    from ssm_pt.engine.plume import Diffuser, ambient_profile, near_field_case
    # The port (2.5 m) is nearest the top layer, where the current flows east; plumes2 angles are CCW from east
    case = near_field_case(ambient_profile(write_column(tmp_path / "a.nc"), 0), Diffuser(bearing_deg=bearing), 0.001)
    assert case.diffuser.horizontal_angle == pytest.approx(angle)


def test_effluent_is_the_water_at_the_port_unless_given(tmp_path):
    from ssm_pt.engine.plume import Diffuser, ambient_profile, near_field_case
    p = ambient_profile(write_column(tmp_path / "a.nc"), 0)
    e = near_field_case(p, Diffuser(), 0.001).effluent  # seawater intake: same T and S as around the port
    assert (e.salinity, e.temperature, e.flow) == (pytest.approx(30.0), pytest.approx(12.0), 0.001)
    e = near_field_case(p, Diffuser(effluent_salinity=25.0, effluent_temperature_c=15.0), 0.001).effluent
    assert (e.salinity, e.temperature) == (25.0, 15.0)


def test_port_below_the_model_seabed_is_rejected(tmp_path):
    from ssm_pt.engine.plume import Diffuser, ambient_profile, near_field_case
    with pytest.raises(ValueError, match="seabed"):
        near_field_case(ambient_profile(write_column(tmp_path / "a.nc"), 0), Diffuser(port_depth_m=12.0), 0.001)


def test_source_takes_a_diffuser_for_the_coupled_near_field():
    from ssm_pt.engine.plume import PlumeRequest
    assert PlumeRequest(**REQ).sources[0].diffuser is None  # fixed near-field values
    d = PlumeRequest(**REQ | {"sources": [SOURCE | {"diffuser": {}}]}).sources[0].diffuser
    assert (d.n_ports, d.port_diameter_m, d.port_depth_m, d.bearing_deg) == (25, 0.0127, 2.0, None)  # Ebb's Macoma


# ── Input files ──

def test_file_name_gives_its_valid_hour():
    from ssm_pt.engine.plume import valid_hour
    assert valid_hour("sscofs.t09z.20260701.fields.n006.nc") == datetime(2026, 7, 1, 9, tzinfo=UTC)
    assert valid_hour("sscofs.t03z.20260702.fields.n001.nc") == datetime(2026, 7, 1, 22, tzinfo=UTC)


def test_only_the_run_window_is_linked(tmp_path):
    from ssm_pt.engine.plume import link_window
    src, dst = tmp_path / "all", tmp_path / "run"
    src.mkdir()
    for k in range(1, 7):  # 04:00 to 09:00
        (src / f"sscofs.t09z.20260701.fields.n00{k}.nc").touch()
    link_window(src, datetime(2026, 7, 1, 6, tzinfo=UTC), datetime(2026, 7, 1, 7, tzinfo=UTC), dst)
    assert sorted(p.name[-7:-3] for p in dst.iterdir()) == ["n002", "n003", "n004", "n005"]  # 05:00 to 08:00


# ── Engine on the local depth-averaged files (about 10 s) ──

PLUME_DATA = Path(__file__).resolve().parents[2] / "data/plume/davg"
needs_plume_data = pytest.mark.skipif(not any(PLUME_DATA.glob("*.nc")), reason=f"no plume files in {PLUME_DATA}")


@needs_plume_data
def test_released_volume_is_flow_times_time_and_stays_on_the_grid(tmp_path):
    from ssm_pt.engine.plume import RELEASE_INTERVAL_S, PlumeEngine, PlumeRequest
    req = PlumeRequest(**REQ | {"duration_h": 3, "n_particles": 2000, "span_km": 40})
    meta = PlumeEngine(PLUME_DATA).run(req, tmp_path)
    expected = SOURCE["flow_m3s"] * (3 * 3600 + RELEASE_INTERVAL_S)  # the first pulse stands for one interval
    assert meta["released_m3"][-1] == pytest.approx(expected, rel=1e-6)
    assert meta["on_grid_pct"][-1] == pytest.approx(100)  # 3 h is too short to leave a 40 km grid
    assert meta["min_dilution"] >= SOURCE.get("near_field_dilution", 8.0)
    assert meta["near_field"] == [None]


SEQUIM_3D = PLUME_DATA.parent / "sequim3d"
needs_sequim_3d = pytest.mark.skipif(not any(SEQUIM_3D.glob("*.nc")), reason=f"no 3D files in {SEQUIM_3D}")
PNNL = dict(lon=-123.04427, lat=48.07881, flow_m3s=5.9 / 3600)  # snapped outfall; Ebb's Macoma flow as placeholder


@needs_plume_data
@needs_sequim_3d
def test_coupled_near_field_runs_plumes_every_hour_and_caps_the_map(tmp_path):
    from ssm_pt.engine.plume import PlumeEngine, PlumeRequest
    req = PlumeRequest(**REQ | {"sources": [PNNL | {"diffuser": {}}], "start": "2026-07-01T01:00:00Z",
                                "duration_h": 2, "n_particles": 2000})
    meta = PlumeEngine(PLUME_DATA).run(req, tmp_path)
    nf = meta["near_field"][0]
    assert len(nf["times"]) == 5  # an hour either side of the 2 h run
    assert all(d > 1 for d in nf["dilution"]) and all(0 < z < 10 for z in nf["trap_depth_m"])
    assert meta["min_dilution"] >= min(nf["dilution"]) * (1 - 1e-6)


# ── API ──

def test_contiguous_stops_at_the_first_gap():
    from ssm_pt.api.app import contiguous
    t0 = datetime(2026, 7, 1)
    files = {t0 + timedelta(hours=h): Path(f"{h}.nc") for h in (0, 1, 2, 4, 5)}
    assert list(contiguous(files)) == [t0, t0 + timedelta(hours=1), t0 + timedelta(hours=2)]


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The app module with its plume state set up as the lifespan would (route functions are called directly;
    Starlette's TestClient needs httpx2, which is not a dependency)."""
    import ssm_pt.api.app as m
    from ssm_pt.api.jobs import Jobs, run_plume
    if not any(PLUME_DATA.glob("*.nc")):
        pytest.skip(f"no plume files in {PLUME_DATA}")
    monkeypatch.setattr(m, "OUTFALLS", tmp_path / "none.geojson")
    m.app.state.plume_n, m.app.state.plume_hours, m.app.state.plume_currents, m.app.state.plume_known = -1, [], None, {}
    m.refresh_plume_data()
    m.app.state.plume_jobs = Jobs(PLUME_DATA, tmp_path / "runs", runner=run_plume)
    yield m
    m.app.state.plume_jobs.pool.shutdown(cancel_futures=True)


def test_plume_meta_lists_an_unbroken_hourly_window(api):
    hours = api.plume_meta()["hours"]
    assert hours and all(b - a == timedelta(hours=1) for a, b in zip(hours, hours[1:]))


def test_outfalls_empty_when_not_built(api):
    assert api.outfalls() == {"type": "FeatureCollection", "features": []}


def test_plume_outside_the_data_window_rejected(api):
    from fastapi import HTTPException

    from ssm_pt.engine.plume import PlumeRequest
    with pytest.raises(HTTPException) as e:
        api.submit_plume(PlumeRequest(**REQ | {"start": "2020-01-01T00:00:00Z"}))
    assert e.value.status_code == 422 and "available data" in e.value.detail


def test_frame_and_receptor_of_a_finished_run(api):
    from concurrent.futures import Future

    from fastapi import HTTPException

    from ssm_pt.engine.oceantracker_engine import TO_UTM
    from ssm_pt.engine.plume import PlumeRequest, write_plume_file
    x0, y0 = TO_UTM.transform(SOURCE["lon"], SOURCE["lat"])
    f = np.zeros((2, 1, 3, 3), np.float32)
    f[1, 0, 1, 1] = 0.01  # the centre cell, holding the source, at the second output time
    jobs = api.app.state.plume_jobs
    out = jobs.out_dir("fake")
    out.mkdir(parents=True)
    write_plume_file(out / "plume.nc", np.array([0.0, 3600.0]), x0 + np.array([-150.0, 0, 150]),
                     y0 + np.array([-150.0, 0, 150]), f, np.ones((2, 1)), np.ones((2, 1)), PlumeRequest(**REQ))
    done = Future()
    done.set_result({"rows": 3, "cols": 3})
    jobs.runs["fake"] = done

    assert api.plume_frame("fake", "0") == {"idx": [], "val": []}
    assert api.plume_frame("fake", "1") == {"idx": [4], "val": [0.01]}
    assert api.plume_frame("fake", "max")["idx"] == [4]
    r = api.plume_receptor("fake", lon=SOURCE["lon"], lat=SOURCE["lat"])
    assert (r["row"], r["col"]) == (1, 1) and r["fraction"] == pytest.approx([0, 0.01])
    with pytest.raises(HTTPException):
        api.plume_receptor("fake", lon=-122.0, lat=47.0)
    with pytest.raises(HTTPException):
        api.plume_frame("unknown", "0")
