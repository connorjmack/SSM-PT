"""Phase 0: FVCOM reader subclass that supports 2D (surface-only) SSCOFS files."""
import numpy as np
from oceantracker.reader.FVCOM_reader import FVCOMreader
from oceantracker.reader.util import hydromodel_grid_transforms as gt


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
