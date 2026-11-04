# Release notes

## v1.0.0 — demonstration release (2026-11-03)

First and final planned release of Vector Pacing as a local demonstration. Scope is intentionally
closed at the workflow below; ideas beyond it are listed under "Deferred ideas" in the README.

**Workflows:**
* **Pacing:** open the app → load the bundled course or upload GPX → configure the rider → simulate
  a constant-power baseline → optimize pacing → compare → export.
* **Drafting check:** load the bundled multi-rider scenario or upload rider positions → adjust the
  detection settings → inspect potential-drafting intervals → export the findings.

### Included

* Safe GPX parsing (namespaces, multiple segments, routes, duplicates, missing elevation, size/length
  limits, XML-entity protection) with user-facing warnings and errors.
* Uniform resampling, ~100 m elevation smoothing, gradient clipping at ±25 %.
* Quasi-steady-state physics (aero, rolling, gravity, drivetrain) with a closed-form vectorised
  speed solver and explicit coasting, speed-limit, stall and solver-failure behaviour.
* CP/W′ balance (Skiba differential model, exact per-step integration); infeasibility detected,
  never clamped.
* Auto baseline (highest feasible whole-watt constant power) or manual baseline.
* Terrain-aware segment optimizer (SLSQP, bounded budget) with whole-watt rounding, independent
  verification, and honest "no meaningful improvement" / "failed" outcomes.
* React interface: demo entry point, validated settings, elevation/power/speed/W′ charts, comparison,
  segment table, solver details, stale-result protection, loading/error/retry/cancel/reset states,
  keyboard and mobile support.
* JSON and CSV export with parameters, units, app version `1.0.0` and model `vp-model-1.0`.
* **Potential-drafting check** (independent of pacing; model `vp-draft-1.0`):
  * `vector-pacing-riders/1` JSON import (row or columnar; `t` seconds or ISO 8601; optional accuracy).
  * Projection onto the course with continuity, so out-and-back lanes and adjacent sections stay
    apart.
  * Synchronisation onto a time grid with missing-data handling.
  * A zone test with uncertainty that includes clock tolerance, and lateral separation when the data
    is precise enough.
  * Classification into potential drafting, inconclusive, overtake, drop-back, brief encounter and
    exempt.
  * Separate listing of opposite-direction, side-by-side, separate-section and stationary proximity.
  * Configurable rules, with illustrative presets only (no governing-body presets).
  * Timeline, course view, per-interval gap chart, data-quality tables, and JSON/CSV export.
  * A bundled deterministic 12-rider scenario covering sustained following, a legal pass, a brief
    encounter and ambiguous GPS.
* 120 backend tests, 28 frontend tests, ruff/mypy/eslint/tsc, GitHub Actions CI.

### Reference result (demo course, default settings)

Baseline 286 W constant → 52:05 (feasible). Optimized 12-segment plan → 51:04 (feasible, verified).
Estimated difference −61.2 s **under this model**.

Drafting demo (default illustrative settings and turnaround exemption): 1 potential-drafting
interval (R02 behind R01, ~159 s at ~7 m); 4 inconclusive (poor GNSS); legal pass, drop-back and
brief encounter correctly not flagged.

### Known limitations

**Pacing** ([docs/MODEL.md §7](docs/MODEL.md#7-known-limitations)): no wind, drafting benefit or
inertia; simplified W′ recovery; local (not global) optimization; no uncertainty estimates.

**Drafting check** ([docs/DRAFTING.md](docs/DRAFTING.md#limitations)): relies on GNSS and the
course line. Lateral separation is rarely usable with consumer GNSS. There is no road or marshal
context, and no official rule presets. It flags only *potential* drafting for human review.
