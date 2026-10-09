"""Warm-worker process pools for particle and plume runs; run id is a hash of the normalized request."""
import hashlib
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from ssm_pt.engine.base import RunRequest


def _warm():
    import ssm_pt.engine.oceantracker_engine  # noqa: F401  # pay the oceantracker import before the first run


def _run(req_json: str, data_dir: str, out_dir: str) -> dict:
    from ssm_pt.engine.oceantracker_engine import OceanTrackerEngine
    return OceanTrackerEngine(Path(data_dir)).run(RunRequest.model_validate_json(req_json), Path(out_dir))


def run_plume(req_json: str, data_dir: str, out_dir: str) -> dict:
    """Plume run; the gridded result stays on disk in out_dir, only its summary comes back."""
    from ssm_pt.engine.plume import PlumeEngine, PlumeRequest
    return PlumeEngine(Path(data_dir)).run(PlumeRequest.model_validate_json(req_json), Path(out_dir))


def run_id(req: BaseModel) -> str:
    return hashlib.sha256(req.model_dump_json().encode()).hexdigest()[:16]


class Jobs:
    """In-memory run registry. Identical requests share one result; results are kept, not re-run,
    because OceanTracker runs are not reproducible. Each Jobs has its own worker pool, so long plume
    runs do not queue ahead of particle runs."""

    def __init__(self, data_dir: Path, runs_dir: Path, workers: int = 1, runner=_run):
        self.data_dir, self.runs_dir, self.runner = data_dir, runs_dir, runner
        self.pool = ProcessPoolExecutor(max_workers=workers)
        self.pool.submit(_warm)
        self.runs: dict[str, Future] = {}

    def submit(self, req: BaseModel) -> str:
        rid = run_id(req)
        f = self.runs.get(rid)
        if f is None or (f.done() and f.exception() is not None):  # new, or retry a failure
            self.runs[rid] = self.pool.submit(self.runner, req.model_dump_json(), str(self.data_dir),
                                              str(self.runs_dir / rid))
        return rid

    def out_dir(self, rid: str) -> Path:
        return self.runs_dir / rid

    def status(self, rid: str) -> dict | None:
        f = self.runs.get(rid)
        if f is None:
            return None
        if not f.done():
            return {"id": rid, "status": "running" if f.running() else "queued"}
        if f.exception() is not None:
            return {"id": rid, "status": "failed", "error": str(f.exception())}
        return {"id": rid, "status": "done"}

    def result(self, rid: str) -> dict | None:
        f = self.runs.get(rid)
        return f.result() if f is not None and f.done() and f.exception() is None else None
