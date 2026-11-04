# Release notes

## v1.0.0 — demonstration release (2026-11-03)

First and final planned release of Vector Pacing as a local demonstration. Scope is intentionally
closed at the workflow below; ideas beyond it are listed under "Deferred ideas" in the README.

**Workflow:** open the app → load the bundled course or upload GPX → configure the rider → simulate a
constant-power baseline → optimize pacing → compare → export.

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
* 76 backend tests, 17 frontend tests, ruff/mypy/eslint/tsc, GitHub Actions CI.

### Reference result (demo course, default settings)

Baseline 286 W constant → 52:05 (feasible). Optimized 12-segment plan → 51:04 (feasible, verified).
Estimated difference −61.2 s **under this model**.

### Known limitations

See [docs/MODEL.md §7](docs/MODEL.md#7-known-limitations). Most notably: no wind/drafting/inertia,
simplified W′ recovery, local (not global) optimization, no uncertainty estimates.
