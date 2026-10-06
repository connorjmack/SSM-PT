"""Tracker interface: run(request) -> tracks."""
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class PointRelease(BaseModel):
    type: Literal["Point"]
    coordinates: tuple[float, float]  # lon, lat (GeoJSON order)


# What is being tracked. Each class adds its own physics to surface advection plus diffusion.
# Sinking particles and larvae need 3D currents and wait for a 3D data layer.
class WaterParcel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["water"] = "water"


class Floating(BaseModel):
    """Floating material (oil, debris, kelp): also pushed by a fraction of the 10 m model wind."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["floating"]
    windage_pct: float = Field(3.0, gt=0, le=10)  # stands in for wave (Stokes) drift too; SSCOFS has no waves
    washes_ashore: bool = True  # stops for good at the model coastline; otherwise bounces back like water


class Decaying(BaseModel):
    """A substance that moves like water and decays exponentially with age (bacteria, some contaminants)."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["decaying"]
    half_life_h: float = Field(24.0, gt=0, le=1000)


Particle = Annotated[WaterParcel | Floating | Decaying, Field(discriminator="type")]


class RunRequest(BaseModel):
    release: PointRelease
    n_particles: int = Field(500, ge=1, le=5000)
    release_radius_m: float = Field(100.0, ge=0, le=5000)
    start: AwareDatetime
    duration_h: float = Field(gt=0, le=120)  # 5 days, the window on disk
    diffusivity_m2s: float = Field(1.0, ge=0, le=100)
    output_interval_min: int = Field(10, ge=2, le=180)
    depth_mode: Literal["surface"] = "surface"
    particle: Particle = WaterParcel()

    @field_validator("start")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return v.astimezone(UTC)  # one canonical form, so equal requests hash to one run id


class Tracker(Protocol):
    def run(self, req: RunRequest, out_dir: Path) -> dict:
        """Run particles and return JSON-ready tracks: times, lon/lat/status as (time, particle)."""
        ...
