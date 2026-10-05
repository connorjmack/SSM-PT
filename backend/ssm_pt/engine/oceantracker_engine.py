"""Tracker adapter that runs OceanTracker on cached SSCOFS fields."""
import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("OCEANTRACKER_NUMBA_CACHING", "1")  # must be set before oceantracker import

import numpy as np
from oceantracker.main import OceanTracker
from oceantracker.read_output.python import load_output_files
from oceantracker.reader.FVCOM_reader import FVCOMreader
from oceantracker.reader.util import hydromodel_grid_transforms as gt
from pyproj import Transformer

from ssm_pt.engine.base import RunRequest

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
TO_LONLAT = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)


class SSCOFS2DReader(FVCOMreader):
    """FVCOMreader handles element-centred fields only in 3D; add the 2D (time, nele) case."""

    def _construct_hori_grid_variables(self):
        super()._construct_hori_grid_variables()
        grid = self.grid
        if not self.info["is3D"]:
            grid["cell_center_weights"] = gt.calculate_inv_dist_weights_at_node_locations(
                grid["x"], grid["x_center"], grid["node_to_tri_map"], grid["tri_per_node"])

    def read_file_var_as_4D_nodal_values(self, var_name, var_info, nt=None):
        if self.info["is3D"] or "nele" not in var_info["dims"]:
            return super().read_file_var_as_4D_nodal_values(var_name, var_info, nt=nt)
        grid = self.grid
        data = self.dataset.read_variable(var_name, nt=nt).data
        if not var_info["time_varying"]:
            data = data[np.newaxis, ...]
        # (time, nele) -> (time, nele, 1) so the element-to-node transform sees a z axis
        data = gt.get_nodal_values_from_weighted_cell_values(
            np.ascontiguousarray(data[:, :, np.newaxis]), grid["node_to_tri_map"],
            grid["tri_per_node"], grid["cell_center_weights"])
        return data[:, :, :, np.newaxis]  # (time, node, z=1, component)


class OceanTrackerEngine:
    """Runs 2D surface tracking on slim SSCOFS files in data_dir (UTM 10N metres)."""

    def __init__(self, data_dir: Path, file_mask: str = "sscofs.*.fields.n*.nc", dt: float = 120.0):
        self.data_dir, self.file_mask, self.dt = Path(data_dir), file_mask, dt

    def run(self, req: RunRequest, out_dir: Path) -> dict:
        x, y = TO_UTM.transform(*req.release.coordinates)
        start = req.start.astimezone(UTC).replace(tzinfo=None).isoformat()
        ot = OceanTracker()
        ot.settings(run_output_dir=str(out_dir), time_step=self.dt, NUMBA_cache_code=True,
                    write_tracks=True, max_run_duration=req.duration_h * 3600)
        ot.add_class("reader", class_name=f"{__name__}.SSCOFS2DReader", input_dir=str(self.data_dir),
                     file_mask=self.file_mask, geographic_coords=False)
        ot.add_class("dispersion", A_H=req.diffusivity_m2s)
        ot.add_class("tracks_writer", update_interval=req.output_interval_min * 60)
        ot.add_class("release_groups", name="release", points=[[x, y]], start=start,
                     pulse_size=req.n_particles, release_radius=req.release_radius_m)
        case_info = ot.run()
        if case_info is None:  # OceanTracker logs its errors and returns None instead of raising
            log = (Path(out_dir) / "run_log.txt").read_text()
            if "No points are inside domain" in log:
                raise ValueError("The release point is on land or outside the model domain.")
            raise RuntimeError(f"OceanTracker failed; see {out_dir}/error_warnings.err")
        return tracks_to_json(load_output_files.load_track_data(case_info))


def tracks_to_json(t: dict) -> dict:
    """OceanTracker track arrays -> ISO times and (time, particle) lon/lat (5 dp, null if NaN) and status."""
    lon, lat = TO_LONLAT.transform(t["x"][:, :, 0], t["x"][:, :, 1])

    def rounded(a):
        a = np.round(a, 5).astype(object)
        a[~np.isfinite(a.astype(float))] = None
        return a.tolist()

    return {
        "times": [datetime.fromtimestamp(s, UTC).strftime("%Y-%m-%dT%H:%M:%SZ") for s in t["time"]],
        "lon": rounded(lon),
        "lat": rounded(lat),
        "status": t["status"].astype(int).tolist(),
    }
