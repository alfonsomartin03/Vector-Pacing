"""HTTP transport layer (FastAPI). Domain logic lives in the sibling modules.

Endpoints (all JSON, SI units unless a field name says otherwise):

* ``GET  /api/health``          – status and version information
* ``GET  /api/defaults``        – default rider/environment/optimizer settings
* ``GET  /api/courses/demo``    – the bundled demo course profile
* ``POST /api/courses/parse``   – upload a GPX file (multipart) → course profile
* ``POST /api/simulate``        – constant-power baseline simulation
* ``POST /api/optimize``        – baseline + optimized plan, verified

The API is stateless: requests carry the course either as ``{"kind": "demo"}``
or as ``{"kind": "gpx", "gpx_text": "..."}``.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import MODEL_VERSION, __version__
from .course import MAX_GPX_BYTES, Course, CourseError, load_demo_course, parse_gpx
from .optimize import (
    MAX_EVALUATIONS,
    MAX_WALL_TIME_S,
    MIN_REPORTABLE_IMPROVEMENT_S,
    OptimizerSettings,
    optimize_pacing,
)
from .simulate import Environment, Rider, SimulationError, SimulationResult, Simulator

DISCLAIMER = (
    "Model estimates only. Results come from a simplified physics and W′ model with assumed parameters; "
    "they are not validated predictions and not medical or training advice."
)

# ---------------------------------------------------------------- schemas


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RiderIn(_Strict):
    rider_mass_kg: float = Field(72.0, ge=30, le=200, description="Rider mass [kg]")
    bike_mass_kg: float = Field(8.5, ge=3, le=40, description="Bike + equipment mass [kg]")
    cda_m2: float = Field(0.28, ge=0.1, le=0.8, description="Drag area CdA [m²]")
    crr: float = Field(0.004, ge=0.001, le=0.03, description="Rolling resistance coefficient")
    drivetrain_efficiency: float = Field(0.975, ge=0.8, le=1.0, description="Drivetrain efficiency (0–1)")
    cp_w: float = Field(280.0, ge=50, le=700, description="Critical power [W]")
    w_prime_kj: float = Field(20.0, ge=1, le=60, description="W′ capacity [kJ]")


class EnvironmentIn(_Strict):
    air_density: float = Field(1.225, ge=0.7, le=1.5, description="Air density [kg/m³]")
    max_speed_kmh: float = Field(80.0, ge=20, le=130, description="Speed limit (braking) [km/h]")
    coast_speed_kmh: float = Field(60.0, ge=20, le=130, description="Coasting threshold [km/h]")


class BaselineIn(_Strict):
    mode: Literal["auto", "manual"] = "auto"
    power_w: float | None = Field(None, ge=10, le=1500, description="Constant power when mode is manual [W]")

    @model_validator(mode="after")
    def _manual_needs_power(self) -> BaselineIn:
        if self.mode == "manual" and self.power_w is None:
            raise ValueError("power_w is required when baseline mode is 'manual'")
        return self


class OptimizerIn(_Strict):
    n_segments: int = Field(12, ge=2, le=30, description="Number of pacing segments")
    min_power_pct_cp: float = Field(60.0, ge=10, le=100, description="Lower power bound [% of CP]")
    max_power_pct_cp: float = Field(130.0, ge=100, le=250, description="Upper power bound [% of CP]")


class CourseRef(_Strict):
    kind: Literal["demo", "gpx"]
    gpx_text: str | None = Field(None, max_length=MAX_GPX_BYTES)
    filename: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _gpx_needs_text(self) -> CourseRef:
        if self.kind == "gpx" and not self.gpx_text:
            raise ValueError("gpx_text is required when course kind is 'gpx'")
        return self


class RunRequest(_Strict):
    course: CourseRef
    rider: RiderIn = RiderIn()
    environment: EnvironmentIn = EnvironmentIn()
    baseline: BaselineIn = BaselineIn()
    optimizer: OptimizerIn = OptimizerIn()


# ---------------------------------------------------------------- helpers


def _r(x: np.ndarray | list[float], nd: int) -> list[float]:
    return [round(float(v), nd) for v in x]


def course_payload(course: Course) -> dict[str, Any]:
    return {
        "name": course.name,
        "source": course.source,
        "content_hash": course.content_hash,
        "length_m": round(course.length_m, 1),
        "elevation_gain_m": round(course.elevation_gain_m, 1),
        "elevation_loss_m": round(course.elevation_loss_m, 1),
        "step_m": round(course.step_m, 3),
        "n_steps": course.n_steps,
        "raw_point_count": course.raw_point_count,
        "min_grade_pct": round(float(course.grade.min()) * 100, 2),
        "max_grade_pct": round(float(course.grade.max()) * 100, 2),
        "warnings": course.warnings,
        "profile": {
            "distance_m": _r(course.distance_m, 1),
            "elevation_m": _r(course.elevation_m, 2),
            "grade_pct": _r(course.grade * 100, 2),
        },
    }


def result_payload(r: SimulationResult) -> dict[str, Any]:
    return {
        "total_time_s": round(r.total_time_s, 2),
        "average_speed_kmh": round(float(np.sum(r.speed_ms * r.dt_s) / r.total_time_s) * 3.6, 2),
        "average_power_w": round(r.average_power_w(), 1),
        "normalized_power_w": round(r.normalized_power_w(), 1),
        "min_w_bal_j": round(r.min_w_bal_j, 1),
        "final_w_bal_j": round(float(r.w_bal_j[-1]), 1),
        "feasible": r.feasible,
        "violations": r.violations,
        "coasting_fraction": round(float(np.sum(r.dt_s[r.coasting]) / r.total_time_s), 4),
        "speed_capped_fraction": round(float(np.sum(r.dt_s[r.speed_capped]) / r.total_time_s), 4),
        "series": {
            "distance_m": _r(r.distance_m, 1),
            "elapsed_s": _r(r.elapsed_s, 2),
            "planned_power_w": _r(r.planned_power_w, 1),
            "power_w": _r(r.power_w, 1),
            "speed_kmh": _r(r.speed_ms * 3.6, 2),
            "w_bal_j": _r(r.w_bal_j, 1),
        },
    }


def _load_course(ref: CourseRef) -> Course:
    if ref.kind == "demo":
        return load_demo_course()
    assert ref.gpx_text is not None
    return parse_gpx(ref.gpx_text, source=(ref.filename or "upload"))


def _setup(req: RunRequest) -> tuple[Course, Simulator, OptimizerSettings]:
    if req.environment.coast_speed_kmh > req.environment.max_speed_kmh:
        raise HTTPException(422, "The coasting threshold must not exceed the speed limit.")
    course = _load_course(req.course)
    r = req.rider
    rider = Rider(
        rider_mass_kg=r.rider_mass_kg,
        bike_mass_kg=r.bike_mass_kg,
        cda_m2=r.cda_m2,
        crr=r.crr,
        drivetrain_efficiency=r.drivetrain_efficiency,
        cp_w=r.cp_w,
        w_prime_j=r.w_prime_kj * 1000.0,
    )
    e = req.environment
    env = Environment(air_density=e.air_density, max_speed_kmh=e.max_speed_kmh, coast_speed_kmh=e.coast_speed_kmh)
    o = req.optimizer
    settings = OptimizerSettings(
        n_segments=o.n_segments,
        min_power_w=float(math.ceil(r.cp_w * o.min_power_pct_cp / 100.0)),
        max_power_w=float(math.floor(r.cp_w * o.max_power_pct_cp / 100.0)),
    )
    return course, Simulator(course, rider, env), settings


def _baseline(req: RunRequest, sim: Simulator, settings: OptimizerSettings) -> tuple[float, SimulationResult, str]:
    if req.baseline.mode == "manual":
        assert req.baseline.power_w is not None
        p = float(req.baseline.power_w)
        return p, sim.run_constant(p), f"Manual constant power of {p:.0f} W."
    best = sim.max_feasible_constant_power(settings.min_power_w, settings.max_power_w)
    if best is None:
        raise HTTPException(
            422,
            f"No constant power at or above the lower bound ({settings.min_power_w:.0f} W) keeps W′ above zero "
            "on this course. Lower the minimum power bound or use a manual baseline.",
        )
    p = float(math.floor(best))
    note = f"Auto: the highest whole-watt constant power that keeps W′bal ≥ 0 over the course ({p:.0f} W)."
    if best >= settings.max_power_w:
        note += " Limited by the optimizer's upper power bound."
    return p, sim.run_constant(p), note


def _meta(req: RunRequest, settings: OptimizerSettings) -> dict[str, Any]:
    return {
        "app_version": __version__,
        "model_version": MODEL_VERSION,
        "disclaimer": DISCLAIMER,
        "inputs": req.model_dump(exclude={"course": {"gpx_text"}}),
        "derived": {"min_power_w": settings.min_power_w, "max_power_w": settings.max_power_w},
    }


# ---------------------------------------------------------------- app

app = FastAPI(title="Vector Pacing API", version=__version__)


@app.exception_handler(CourseError)
async def _course_error(_: Request, exc: CourseError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "kind": "course_error"})


@app.exception_handler(SimulationError)
async def _sim_error(_: Request, exc: SimulationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc), "kind": "simulation_error"})


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    msgs = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
        msgs.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return JSONResponse(
        status_code=422,
        content={"detail": "Invalid input — " + "; ".join(msgs), "kind": "validation_error", "errors": msgs},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app_version": __version__, "model_version": MODEL_VERSION}


@app.get("/api/defaults")
def defaults() -> dict[str, Any]:
    return {
        "rider": RiderIn().model_dump(),
        "environment": EnvironmentIn().model_dump(),
        "baseline": BaselineIn().model_dump(),
        "optimizer": OptimizerIn().model_dump(),
    }


@app.get("/api/courses/demo")
def demo_course() -> dict[str, Any]:
    return course_payload(load_demo_course())


@app.post("/api/courses/parse")
async def parse_course(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    data = await file.read(MAX_GPX_BYTES + 1)
    name = (file.filename or "upload.gpx")[:200]
    if not name.lower().endswith(".gpx"):
        raise HTTPException(422, "Only .gpx files are supported.")
    course = parse_gpx(data, source=name)
    return course_payload(course)


@app.post("/api/simulate")
def simulate(req: RunRequest) -> dict[str, Any]:
    course, sim, settings = _setup(req)
    power, base, note = _baseline(req, sim, settings)
    return {
        "meta": _meta(req, settings),
        "course": course_payload(course),
        "baseline": {"power_w": power, "selection": note, **result_payload(base)},
    }


@app.post("/api/optimize")
def optimize(req: RunRequest) -> dict[str, Any]:
    course, sim, settings = _setup(req)
    if settings.min_power_w >= settings.max_power_w:
        raise HTTPException(422, "The minimum power bound must be below the maximum power bound.")
    power, base, note = _baseline(req, sim, settings)
    out = optimize_pacing(sim, base, power, settings)
    opt_payload = None
    if out.result is not None and out.status != "failed":
        opt_payload = result_payload(out.result)
    segments = []
    for k, seg in enumerate(out.segments):
        s = seg.step_slice
        row: dict[str, Any] = {
            "index": seg.index,
            "start_m": round(seg.start_m, 1),
            "end_m": round(seg.end_m, 1),
            "mean_grade_pct": round(seg.mean_grade * 100, 2),
            "baseline_power_w": power,
            "baseline_time_s": round(float(base.dt_s[s].sum()), 2),
        }
        if opt_payload is not None and out.plan_w is not None and out.result is not None:
            r = out.result
            t = float(r.dt_s[s].sum())
            row |= {
                "planned_power_w": float(out.plan_w[k]),
                "applied_avg_power_w": round(float(np.sum(r.power_w[s] * r.dt_s[s]) / t), 1),
                "optimized_time_s": round(t, 2),
            }
        segments.append(row)
    return {
        "meta": _meta(req, settings),
        "course": course_payload(course),
        "baseline": {"power_w": power, "selection": note, **result_payload(base)},
        "optimization": {
            "status": out.status,
            "message": out.message,
            "notes": out.notes,
            "time_saved_s": (
                round(base.total_time_s - out.result.total_time_s, 2) if opt_payload and out.result else None
            ),
            "solver": {
                "method": "SLSQP (scipy.optimize.minimize)",
                "success": out.solver_success,
                "message": out.solver_message,
                "iterations": out.iterations,
                "evaluations": out.evaluations,
                "elapsed_s": round(out.elapsed_s, 3),
                "max_evaluations": MAX_EVALUATIONS,
                "max_wall_time_s": MAX_WALL_TIME_S,
                "min_reportable_improvement_s": MIN_REPORTABLE_IMPROVEMENT_S,
            },
            "segments": segments,
            "result": opt_payload,
        },
    }


# Serve the built frontend (frontend/dist) when present: single-process demo mode.
_dist = Path(os.environ.get("VECTOR_PACING_STATIC", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if _dist.is_dir() and (_dist / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "Not found")
        candidate = (_dist / path).resolve()
        if path and candidate.is_file() and _dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
