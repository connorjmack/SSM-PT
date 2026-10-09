"""Particle classes: request validation, and that each class changes the run as intended."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import netCDF4
import numpy as np
import pytest
from pydantic import ValidationError

from ssm_pt.engine.base import RunRequest

DATA = Path(__file__).resolve().parents[2] / "data/phase0/slim2d"
BASE = dict(release={"type": "Point", "coordinates": [-123.4, 48.25]},  # mid Strait of Juan de Fuca
            start="2026-10-01T00:00:00Z", duration_h=3, n_particles=5, release_radius_m=0, diffusivity_m2s=0)
# Hood Canal, 150 m off a steep shore in 77 m of water; spreading carries some particles to the coast
SHORE = dict(release={"type": "Point", "coordinates": [-122.9733, 47.5713]}, n_particles=40, diffusivity_m2s=10)


def test_default_is_water_parcel():
    assert RunRequest(**BASE).particle.type == "water"


def test_floating_defaults_to_three_percent_windage():
    assert RunRequest(**BASE, particle={"type": "floating"}).particle.windage_pct == 3.0


def test_floating_washes_ashore_by_default():
    assert RunRequest(**BASE, particle={"type": "floating"}).particle.washes_ashore is True


def test_decaying_needs_positive_half_life():
    assert RunRequest(**BASE, particle={"type": "decaying", "half_life_h": 12}).particle.half_life_h == 12
    with pytest.raises(ValidationError):
        RunRequest(**BASE, particle={"type": "decaying", "half_life_h": 0})


def test_unknown_class_rejected():
    with pytest.raises(ValidationError):
        RunRequest(**BASE, particle={"type": "larva"})


def test_parameters_of_other_classes_rejected():
    # a windage on a water parcel would be silently ignored and split the run cache
    with pytest.raises(ValidationError):
        RunRequest(**BASE, particle={"type": "water", "windage_pct": 3})


def test_comparison_kernels_skip_the_disk_cache():
    """They take a numba function as an argument, so numba can never reuse their cache from another process."""
    import ssm_pt.engine.oceantracker_engine  # noqa: F401  first: it turns numba caching on
    from numba.core.caching import NullCache
    from oceantracker.particle_properties.util import particle_comparisons_util as c

    assert all(isinstance(f._cache, NullCache) for f in (c._prop_compared_to_value, c._prop_subset_compared_to_value))


# ── Engine runs on the local SSCOFS files (about 10 s each) ──

needs_data = pytest.mark.skipif(not any(DATA.glob("*.nc")), reason=f"no SSCOFS files in {DATA}")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """Run a 3 h release as the given particle class, with BASE overrides; each combination runs once per module."""
    from ssm_pt.engine.oceantracker_engine import OceanTrackerEngine
    engine, done = OceanTrackerEngine(DATA), {}

    def go(particle: dict, **overrides) -> dict:
        key = json.dumps([particle, overrides], sort_keys=True)
        if key not in done:
            done[key] = engine.run(RunRequest(**BASE | overrides, particle=particle), tmp_path_factory.mktemp("run"))
        return done[key]
    return go


def final_xy(tracks) -> np.ndarray:
    """Mean final position of the particles, in metres east and north of the release."""
    lon0, lat0 = BASE["release"]["coordinates"]
    lon, lat = np.nanmean(np.array(tracks["lon"][-1], float)), np.nanmean(np.array(tracks["lat"][-1], float))
    return np.array([(lon - lon0) * 111_320 * np.cos(np.radians(lat0)), (lat - lat0) * 111_320])


def mean_wind_at_release() -> np.ndarray:
    """Model wind (u, v) at the element nearest the release, averaged over the run's hours."""
    lon0, lat0 = BASE["release"]["coordinates"]
    t0 = datetime.fromisoformat(BASE["start"])
    hours = {t0 + timedelta(hours=h) for h in range(BASE["duration_h"] + 1)}
    winds = []
    for f in sorted(DATA.glob("*.nc")):
        with netCDF4.Dataset(f) as nc:
            t = netCDF4.num2date(nc["time"][0], nc["time"].units, only_use_cftime_datetimes=False)
            if t.replace(tzinfo=t0.tzinfo) not in hours:
                continue
            lon = np.where(nc["lonc"][:] > 180, nc["lonc"][:] - 360, nc["lonc"][:])
            i = np.argmin((lon - lon0) ** 2 + (nc["latc"][:] - lat0) ** 2)
            winds.append([nc["uwind_speed"][0, i], nc["vwind_speed"][0, i]])
    return np.mean(winds, axis=0)


@needs_data
def test_floating_drifts_downwind_of_water_parcel(run):
    gap = final_xy(run({"type": "floating", "windage_pct": 3})) - final_xy(run({"type": "water"}))
    wind = mean_wind_at_release()
    expected = 0.03 * np.linalg.norm(wind) * BASE["duration_h"] * 3600
    assert np.linalg.norm(gap) > 0.3 * expected  # currents differ along the two paths, so only roughly
    assert gap @ wind / (np.linalg.norm(gap) * np.linalg.norm(wind)) > 0.5  # within 60 degrees of downwind


@needs_data
def test_decaying_halves_every_half_life(run):
    tracks = run({"type": "decaying", "half_life_h": 1})
    first, last = np.array(tracks["remaining"][0], float), np.array(tracks["remaining"][-1], float)
    assert np.allclose(first, 1, atol=1e-3)
    assert np.allclose(last, 0.5 ** BASE["duration_h"], atol=1e-3)


@needs_data
def test_water_parcel_has_no_decay(run):
    assert "remaining" not in run({"type": "water"})


@needs_data
def test_floating_washes_ashore_and_stays(run):
    from ssm_pt.engine.oceantracker_engine import ASHORE
    tracks = run({"type": "floating"}, **SHORE)
    ashore = np.array(tracks["status"]) == ASHORE
    assert ashore[-1].any()
    lon, lat = np.array(tracks["lon"], float), np.array(tracks["lat"], float)
    for p in np.flatnonzero(ashore[-1]):
        first = np.argmax(ashore[:, p])
        assert ashore[first:, p].all()
        assert (lon[first:, p] == lon[first, p]).all() and (lat[first:, p] == lat[first, p]).all()


@needs_data
def test_floating_can_be_kept_afloat(run):
    from ssm_pt.engine.oceantracker_engine import ASHORE
    tracks = run({"type": "floating", "washes_ashore": False}, **SHORE)
    assert not (np.array(tracks["status"]) == ASHORE).any()
