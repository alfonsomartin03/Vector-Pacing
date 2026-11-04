"""HTTP layer for potential-drafting detection (kept separate from pacing endpoints)."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import __version__
from .course import MAX_GPX_BYTES
from .drafting import (
    DISCLAIMER,
    DRAFT_MODEL_VERSION,
    FORMAT_ID,
    ILLUSTRATIVE_PRESETS,
    DraftingAnalysis,
    DraftingSettings,
    ExemptSection,
    analyze,
    load_demo_scenario,
    parse_scenario,
)
from .fit import MAX_FIT_BYTES
from .review import PowerReview, power_payload, run_fit_review

router = APIRouter(prefix="/api/drafting", tags=["drafting"])

DEMO_EXEMPT_SECTIONS = [{"start_m": 3950.0, "end_m": 4085.0, "label": "Turnaround (illustrative)"}]
MAX_COURSE_POINTS_OUT = 1500


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExemptSectionIn(_Strict):
    start_m: float = Field(ge=0, le=1_000_000)
    end_m: float = Field(ge=0, le=1_000_000)
    label: str = Field("", max_length=60)

    @model_validator(mode="after")
    def _ordered(self) -> ExemptSectionIn:
        if self.end_m <= self.start_m:
            raise ValueError("end_m must be greater than start_m")
        return self


class DraftingSettingsIn(_Strict):
    zone_length_m: float = Field(10.0, ge=1, le=50, description="Longitudinal zone length [m]")
    lateral_separation_m: float = Field(2.0, ge=0.5, le=10, description="Lateral separation that ends following [m]")
    allowed_pass_s: float = Field(20.0, ge=0, le=300, description="Allowed time in zone while passing [s]")
    drop_back_s: float = Field(20.0, ge=0, le=300, description="Time allowed to drop back after being passed [s]")
    exempt_sections: list[ExemptSectionIn] = Field(default_factory=list, max_length=20)
    smoothing_s: float = Field(5.0, ge=1, le=30, description="Rolling-median window [s]")
    max_interpolation_gap_s: float = Field(5.0, ge=1, le=30, description="Longest gap bridged by interpolation [s]")
    merge_gap_s: float = Field(5.0, ge=0, le=30, description="Out-of-zone gap still treated as one interval [s]")
    timestamp_tolerance_s: float = Field(0.2, ge=0, le=5, description="Assumed clock misalignment [s]")
    assumed_accuracy_m: float = Field(5.0, ge=0.1, le=50, description="Accuracy used when not reported [m]")
    lateral_accuracy_max_m: float = Field(1.0, ge=0.1, le=5, description="Max accuracy for using lateral data [m]")
    analysis_step_s: float = Field(1.0, ge=0.5, le=5, description="Synchronisation grid step [s]")

    def to_domain(self) -> DraftingSettings:
        data = self.model_dump(exclude={"exempt_sections"})
        return DraftingSettings(
            **data,
            exempt_sections=tuple(ExemptSection(e.start_m, e.end_m, e.label) for e in self.exempt_sections),
        )


class DraftingScenarioRef(_Strict):
    kind: Literal["demo", "upload"]
    document: dict[str, Any] | None = None
    filename: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _needs_doc(self) -> DraftingScenarioRef:
        if self.kind == "upload" and self.document is None:
            raise ValueError("document is required when scenario kind is 'upload'")
        return self


class DraftingRequest(_Strict):
    scenario: DraftingScenarioRef
    settings: DraftingSettingsIn = DraftingSettingsIn()


def _f(v: float, nd: int = 2) -> float | None:
    return round(float(v), nd) if math.isfinite(float(v)) else None


def payload(a: DraftingAnalysis, settings_in: DraftingSettingsIn, power: PowerReview | None = None) -> dict[str, Any]:
    sc, grid = a.scenario, a.grid
    xy = a.course.xy
    stride = max(1, math.ceil(len(xy) / MAX_COURSE_POINTS_OUT))
    course_xy = np.vstack([xy[::stride], xy[-1:]])
    events: list[dict[str, Any]] = []
    for n, iv in enumerate(a.intervals, start=1):
        events.append(
            {
                "id": f"E{n:03d}",
                "follower": iv.follower,
                "leader": iv.leader,
                "classification": iv.classification,
                "flagged": iv.classification == "potential_drafting",
                "order_uncertain": iv.order_uncertain,
                "start_s": _f(grid[iv.i0], 1),
                "end_s": _f(grid[iv.i1], 1),
                "duration_s": _f(iv.duration_s, 1),
                "counted_s": _f(iv.counted_s, 1),
                "exempt_s": _f(iv.exempt_s, 1),
                "missing_s": _f(iv.missing_s, 1),
                "started_by": iv.started_by,
                "ended_by": iv.ended_by,
                "gap_median_m": _f(iv.gap_median_m),
                "gap_min_m": _f(iv.gap_min_m),
                "gap_max_m": _f(iv.gap_max_m),
                "sigma_median_m": _f(iv.sigma_median_m),
                "probability_in_zone": _f(iv.confident_inside_fraction, 3),
                "quality": iv.quality,
                "lateral_used": iv.lateral_used,
                "course_start_m": _f(iv.course_m[0], 1),
                "course_end_m": _f(iv.course_m[1], 1),
                "limitations": iv.limitations,
                "series": iv.series,
            }
        )
    counts: dict[str, int] = {}
    for e in events:
        cls = str(e["classification"])
        counts[cls] = counts.get(cls, 0) + 1
    enc_counts: dict[str, int] = {}
    for enc in a.encounters:
        enc_counts[enc.type] = enc_counts.get(enc.type, 0) + 1
    power_screen = power_payload(power, events) if power is not None else None
    if power is None:
        for e in events:
            e["power_anomaly_ids"] = []
    return {
        "meta": {
            "app_version": __version__,
            "drafting_model_version": DRAFT_MODEL_VERSION,
            "format": FORMAT_ID,
            "disclaimer": DISCLAIMER,
            "settings": settings_in.model_dump(),
            "settings_note": "Settings are user-configurable; bundled values are illustrative, not official rules.",
        },
        "scenario": {
            "name": sc.name,
            "description": sc.description,
            "illustrative": sc.illustrative,
            "source": sc.source,
            "content_hash": sc.content_hash,
            "start_time": sc.start_time,
            "duration_s": _f(float(grid[-1] - grid[0]), 1),
            "t_min_s": _f(float(grid[0]), 1),
            "t_max_s": _f(float(grid[-1]), 1),
            "course": {
                "name": sc.course_name,
                "length_m": _f(a.course.length_m, 1),
                "xy": [[_f(x, 1), _f(y, 1)] for x, y in course_xy.tolist()],
            },
            "riders": [
                {
                    "id": q.id,
                    "label": q.label,
                    "samples": q.samples,
                    "start_s": _f(q.start_s, 1),
                    "end_s": _f(q.end_s, 1),
                    "median_interval_s": _f(q.median_interval_s, 2),
                    "irregular_interval_fraction": _f(q.irregular_interval_fraction, 3),
                    "max_interval_s": _f(q.max_interval_s, 2),
                    "missing_fraction": _f(q.missing_fraction, 3),
                    "accuracy_median_m": _f(q.accuracy_median_m, 2),
                    "accuracy_reported": q.accuracy_reported,
                    "max_off_course_m": _f(q.max_off_course_m, 1),
                    "warnings": q.warnings,
                }
                for q in a.rider_quality
            ],
        },
        "limitations": a.limitations,
        "counts": counts,
        "events": events,
        "encounters": [
            {
                "rider_a": e.rider_a,
                "rider_b": e.rider_b,
                "type": e.type,
                "start_s": _f(grid[e.i0], 1),
                "end_s": _f(grid[e.i1], 1),
                "duration_s": _f(e.duration_s, 1),
                "min_distance_m": _f(e.min_distance_m),
                "median_course_gap_m": _f(e.median_course_gap_m, 1),
            }
            for e in a.encounters
        ],
        "encounter_counts": enc_counts,
        "power_screen": power_screen,
    }


@router.get("/presets")
def presets() -> dict[str, Any]:
    return {
        "defaults": DraftingSettingsIn().model_dump(),
        "presets": ILLUSTRATIVE_PRESETS,
        "demo_exempt_sections": DEMO_EXEMPT_SECTIONS,
        "note": (
            "All presets are illustrative examples. No governing-body presets are bundled because their "
            "current rules could not be verified for this release; enter your event's rules manually."
        ),
    }


@router.post("/analyze")
def analyze_endpoint(req: DraftingRequest) -> dict[str, Any]:
    if req.scenario.kind == "demo":
        scenario = load_demo_scenario()
    else:
        scenario = parse_scenario(req.scenario.document, source=req.scenario.filename or "upload")
    return payload(analyze(scenario, req.settings.to_domain()), req.settings)


@router.post("/analyze-fit")
async def analyze_fit_endpoint(
    files: Annotated[list[UploadFile], File(description="One .fit activity file per rider")],
    course: Annotated[UploadFile | None, File(description="Optional course line (.gpx)")] = None,
    settings: Annotated[str, Form(description="DraftingSettingsIn as JSON")] = "{}",
) -> dict[str, Any]:
    try:
        settings_in = DraftingSettingsIn.model_validate_json(settings)
    except ValidationError as exc:
        msgs = [f"settings.{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise HTTPException(422, "Invalid input — " + "; ".join(msgs)) from None
    riders: list[tuple[str, bytes]] = []
    for f in files:
        name = (f.filename or "rider.fit")[:200]
        if not name.lower().endswith(".fit"):
            raise HTTPException(422, f'"{name}" is not a .fit file. Upload one .fit activity per rider.')
        riders.append((name, await f.read(MAX_FIT_BYTES + 1)))
    course_file = None
    if course is not None and course.filename:
        cname = course.filename[:200]
        if not cname.lower().endswith(".gpx"):
            raise HTTPException(422, f'"{cname}": the course must be a .gpx file.')
        course_file = (cname, await course.read(MAX_GPX_BYTES + 1))
    review = run_fit_review(riders, course_file, settings_in.to_domain())
    return payload(review.analysis, settings_in, review.power)
