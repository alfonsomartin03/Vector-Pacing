# Release notes

## 1.1.0 — browser-only GitHub Pages demo

The demo now runs entirely in the browser and deploys to GitHub Pages as a static site. No backend,
API server, account or paid service is needed.

* **Browser engine.** Course parsing, physics, W′ balance, the constant-power baseline, the drafting
  GPS check and the FIT position reader were ported to TypeScript (`frontend/src/engine/`) and run in
  a Web Worker. On the bundled data the port reproduces the Python results: the demo course Auto
  baseline is 286 W and 3125.42 s, the 12-rider drafting scenario gives identical intervals and
  encounters, and the `.fit` samples give the same intervals.
* **New optimizer.** SciPy SLSQP is replaced by a bounded two-stage heuristic: grade-proportional
  seeds, then W′-aware pairwise power transfers. Every candidate is fully simulated, and the final plan
  is verified independently. It is simpler and not equivalent to SLSQP: on the demo course it finds
  57.3 s where SLSQP found 61.2 s. The model id is now `vp-model-1.0+web-heuristic-1`.
* **Removed from the demo.** The RaceGuard power screen (UI, exports, FIT power decoding) and
  single-ride `.fit` uploads, which only the power screen used. Drafting uploads still take two or
  more `.fit` rides plus an optional `.gpx`, and now also a `vector-pacing-riders/1` JSON file (new
  sample: `samples/drafting_two_riders.json`).
* **Deployment.** The external API URL, the API setup notice, the CORS option and the dev-server API
  proxy are gone. The Pages workflow runs lint, type checks, tests and the build, then publishes
  `frontend/dist` under the repository's Pages path.
* **Python reference.** `backend/` is kept unchanged as a reference implementation. It is not needed
  to build or run the demo; `make reference-install` and `make reference-test` still work.
* **Tests.** New focused engine tests (parsing edge cases, physics, W′, simulation rules, optimizer
  honesty rules and budget, drafting parity and validation) and a worker-client test. The UI tests now
  run against the real engine and assert that no network requests are made.

## Unreleased (pre-1.1, Python backend)

* **RaceGuard merged in.** [RaceGuard](https://github.com/alfonsomartin03/RaceGuard) is merged into
  this repository with its history. Its within-rider power model is now the drafting check's **power
  screen** (`backend/vector_pacing/power_screen.py`). It flags sections where a rider held speed on
  clearly less power than in their own comparable sections. It runs on every uploaded `.fit` ride
  with power, including a single ride, and its anomalies are linked with overlapping GPS intervals in
  the UI, exports and API (`power_screen`, `power_anomaly_ids`). Output matches RaceGuard's on
  identical input, and its power tests are ported. RaceGuard's GPS trailing detector is superseded by
  the existing following check. Its CSV import, HTML dashboard, `fitparse` dependency and Vercel
  config were not carried over.
* **Command line.** `vector-pacing-review` (installed with the backend) prints or writes the same
  review as text or JSON.
* **FIT reader** also reads speed, power, cadence, heart rate, altitude and distance.
* New sample `samples/drafting_with_power/`, where both kinds of evidence agree.

* **Drafting check: `.fit` uploads.** Upload one `.fit` activity per rider (plus an optional course
  `.gpx`) instead of the `vector-pacing-riders/1` JSON document. FIT files are decoded by a built-in
  reader (no new dependency); `gps_accuracy` is used where recorded. The JSON structure remains
  available via `POST /api/drafting/analyze` for programmatic use. Samples are now
  `samples/drafting_two_riders/*.fit` + `course.gpx` and `samples/drafting_invalid.fit`.

## v1.0.0 — demonstration release (2026-11-03)

First and final planned release of Vector Pacing as a local demonstration. Scope is intentionally
closed at the workflow below; ideas beyond it are listed under "Deferred ideas" in the README.

**Workflows:**
* **Pacing:** open the app → load the bundled course or upload GPX → configure the rider → simulate
  a constant-power baseline → optimize pacing → compare → export.
* **Drafting check:** load the bundled multi-rider scenario or upload one `.fit` ride per rider → adjust the
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
