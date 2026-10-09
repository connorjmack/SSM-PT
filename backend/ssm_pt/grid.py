"""Extract the static SSCOFS mesh once and derive the domain-outline GeoJSON from boundary edges.

Also: clipping the mesh to a set of elements, for the Sequim-box 3D files.
"""
import numpy as np


def clip_mesh(nv: np.ndarray, elements: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """0-based nv (3, nele) and the elements to keep -> (their nodes, sorted; their nv renumbered to those nodes)."""
    kept = nv[:, elements]
    nodes = np.unique(kept)
    return nodes, np.searchsorted(nodes, kept)
