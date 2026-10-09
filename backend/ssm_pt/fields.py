"""ensure_hours(hours): range-read surface fields from S3 into an atomic, idempotent per-hour local cache.

Also: depth averaging of layered SSCOFS velocities, for the depth-averaged plume files.
"""
import numpy as np


def depth_average(u: np.ndarray, siglev: np.ndarray) -> np.ndarray:
    """(time, siglay, nele) layer values -> (time, nele) water-column mean, weighting each layer by its sigma thickness.

    siglev holds the layer interfaces from the surface (0) to the bed (-1); SSCOFS uses the same levels at every node.
    """
    dsig = -np.diff(siglev)
    return np.tensordot(u, dsig / dsig.sum(), axes=([1], [0]))
