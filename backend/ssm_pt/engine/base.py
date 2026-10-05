"""Tracker interface: run(request) -> tracks."""
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, Field, field_validator


class PointRelease(BaseModel):
    type: Literal["Point"]
    coordinates: tuple[float, float]  # lon, lat (GeoJSON order)


class RunRequest(BaseModel):
    release: PointRelease
    n_particles: int = Field(500, ge=1, le=5000)
    release_radius_m: float = Field(100.0, ge=0, le=5000)
    start: AwareDatetime
    duration_h: float = Field(gt=0, le=120)  # 5 days, the window on disk
    diffusivity_m2s: float = Field(1.0, ge=0, le=100)
    output_interval_min: int = Field(10, ge=2, le=180)
    depth_mode: Literal["surface"] = "surface"
    windage_pct: Literal[0] = 0

    @field_validator("start")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)  # one canonical form, so equal requests hash to one run id


class Tracker(Protocol):
    def run(self, req: RunRequest, out_dir: Path) -> dict:
        """Run particles and return JSON-ready tracks: times, lon/lat/status as (time, particle)."""
        ...
