"""Surface-current arrows for the map: about one model element per screen cell, from the local hourly files."""
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import netCDF4
import numpy as np

CELL_PX = 30  # one arrow per CELL_PX x CELL_PX screen pixels


class Currents:
    def __init__(self, files: dict[datetime, Path]):
        self.files = files
        with netCDF4.Dataset(next(iter(files.values()))) as nc:
            lon = nc["lonc"][:].astype(np.float64)
            self.lon = np.where(lon > 180, lon - 360, lon)  # SSCOFS stores 0-360
            self.lat = nc["latc"][:].astype(np.float64)
        # Web Mercator pixel coordinates at zoom 0 (a 256 px world), the same grid Leaflet draws on
        phi = np.radians(self.lat)
        self.mx = (self.lon + 180) / 360 * 256
        self.my = (1 - np.log(np.tan(phi) + 1 / np.cos(phi)) / np.pi) / 2 * 256
        # Playback asks for one view at many hours, so cache both halves of a request
        self.pick = lru_cache(maxsize=32)(self._pick)
        self.field = lru_cache(maxsize=12)(self._field)

    def _pick(self, west: float, south: float, east: float, north: float, zoom: float) -> np.ndarray:
        """Indices of the element nearest the centre of each screen cell in the view.

        Cells are fixed to the global pixel grid, so arrows stay put when the map is panned.
        """
        idx = np.flatnonzero((self.lon >= west) & (self.lon <= east) & (self.lat >= south) & (self.lat <= north))
        s = 2.0 ** zoom / CELL_PX
        x, y = self.mx[idx] * s, self.my[idx] * s
        ix, iy = np.floor(x), np.floor(y)
        d = (x - ix - 0.5) ** 2 + (y - iy - 0.5) ** 2
        cell = ix.astype(np.int64) << 32 | iy.astype(np.int64)
        order = np.lexsort((d, cell))
        first = np.unique(cell[order], return_index=True)[1]
        return idx[order[first]]

    def _field(self, hour: datetime) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with netCDF4.Dataset(self.files[hour]) as nc:
            nc.set_auto_mask(False)
            return nc["u"][0], nc["v"][0], nc["wet_cells"][0]

    def arrows(self, hour: datetime, west: float, south: float, east: float, north: float, zoom: float) -> dict:
        """JSON-ready arrows (lon, lat, u, v in m/s) for the view, skipping dry elements."""
        i = self.pick(west, south, east, north, zoom)
        u, v, wet = self.field(hour)
        i = i[wet[i] > 0]
        return {
            "time": hour.isoformat(),
            "lon": self.lon[i].round(5).tolist(),
            "lat": self.lat[i].round(5).tolist(),
            "u": u[i].astype(np.float64).round(3).tolist(),
            "v": v[i].astype(np.float64).round(3).tolist(),
        }
