# Model assumptions (model `vp-model-1.0+web-heuristic-1`)

Vector Pacing produces **model estimates**. Every number depends on simplified physics, a simplified
physiology model and the parameters you enter. Nothing here has been validated against real rides, and
nothing here is medical or training advice.

The physics, W′ and simulation rules below are model `vp-model-1.0`, unchanged from the Python
reference. They are implemented in TypeScript in `frontend/src/engine/` and run in your browser. Only
the optimizer differs (§6), hence the `+web-heuristic-1` suffix.

Internal units are SI throughout (m, s, kg, W, J, m/s). The UI converts to km, km/h and kJ for display;
every displayed quantity is labelled with its unit.

## 1. Course processing — `frontend/src/engine/course.ts`

| Step | Behaviour |
| --- | --- |
| XML safety | Parsed with `defusedxml`; DTDs/entities (e.g. "billion laughs") are rejected. Max 10 MB, 200 000 points. |
| Namespaces | Matched by local tag name, so GPX 1.0, 1.1, prefixed and vendor-extended files all parse. |
| Track selection | All `<trk>/<trkseg>` in document order, concatenated. The straight-line gap between segments counts as distance (warning shown). Falls back to `<rte>/<rtept>` if there is no track. |
| Invalid coordinates | Missing, non-numeric or out-of-range lat/lon rejects the whole file (dropping points would silently change the course). |
| Duplicates | Consecutive points < 5 cm apart are dropped (warning shown). |
| Zero / short / huge | All points identical → "zero length" error. Courses < 200 m or > 300 km are rejected. |
| Missing elevation | Interpolated linearly by distance from neighbouring points (warning). Fewer than two points with elevation → course treated as flat (warning). Elevations outside −500…9000 m are treated as missing. |
| Distance | Haversine on a sphere of radius 6 371 008.8 m. |
| Resampling | Uniform grid with step `ds = max(5 m, length / 2000)` (≈2000 steps; 16 m for the 32 km demo). |
| Smoothing | Centred moving average over ≈100 m of elevation (edges repeated), to suppress GPS/barometer noise. |
| Gradient | `Δelevation / ds` per step, clipped to ±25 % (warning reports how many steps were clipped). |

## 2. Physics — `frontend/src/engine/simulate.ts`

Quasi-steady state: at each step the rider instantly reaches the speed at which wheel power equals
resistive power. Acceleration and rotational inertia are **not** modelled.

    η·P = v · ( ½·ρ·CdA·v²  +  m·g·Crr·cos θ  +  m·g·sin θ ),   θ = atan(grade)

* `P` crank power (W), `η` drivetrain efficiency, `ρ` air density, `CdA` drag area, `Crr` rolling
  resistance, `m` rider + bike mass, `g = 9.80665 m/s²`.
* **Still air** (no wind) and constant air density.
* The cubic `a·v³ + b·v − ηP = 0` has exactly one non-negative root for `P ≥ 0`. It is solved in closed
  form (Cardano / trigonometric form), vectorised, then polished with two Newton steps.
* **Solver failure**: a non-finite root, or a residual above `1e-6·max(1, P)` W, raises an error. The UI
  shows the explanation; no partial result is shown.

## 3. Simulation rules — `frontend/src/engine/simulate.ts`

Applied in this order at every step:

1. **Coasting (spin-out).** If the zero-power terminal speed on that step is ≥ the *coasting threshold*
   (default 60 km/h), the rider stops pedalling: applied power 0 W, speed = terminal speed.
2. **Pedalling.** Otherwise the planned power is applied and the steady speed solved.
3. **Speed limit.** If the speed exceeds the *speed limit* (default 80 km/h), speed is capped and the
   applied power reduced to exactly what holds the limit (0 W, i.e. braking, if gravity alone exceeds it).
4. **Stall.** A speed below 3.6 km/h makes the plan infeasible ("rider would stall").

Planned and applied power can differ (rules 1 and 3); time and W′ always use **applied** power. Both
are exported.

## 4. Athlete model — `frontend/src/engine/simulate.ts`

Critical power (CP) and W′ with the differential W′-balance model (Skiba et al., 2015), integrated
exactly over each step of constant power:

* `P > CP`: `W′bal ← W′bal − (P − CP)·dt` (linear depletion)
* `P ≤ CP`: `W′bal ← W′₀ − (W′₀ − W′bal)·exp(−(CP − P)·dt / W′₀)` (exponential recovery)

Recovery is an approximation; real recovery kinetics vary between athletes. The balance is **never
clamped**: if it goes below zero the plan is reported infeasible together with the km where W′ ran out
and the (negative) minimum value.

## 5. Baseline

Constant power over the whole course, run through the same simulator.

* **Auto (default):** the highest whole-watt constant power (within the optimizer bounds) that keeps
  `W′bal ≥ 0` everywhere, found by bisection then floored to whole watts. Its power is shown explicitly.
* **Manual:** any constant power. If it is infeasible this is shown, and optimization refuses to run
  from it.

## 6. Optimizer — `frontend/src/engine/optimize.ts`

The browser demo uses a **simplified, bounded heuristic**. It is *not* the SciPy SLSQP optimizer of
the Python reference (`backend/vector_pacing/optimize.py`) and does not claim to find the same plan.
On the demo course with default inputs it finds a plan 57.3 s faster than the baseline; SLSQP found
61.2 s.

* **Segments.** The course is split into *N* (2–30, default 12) **terrain-aware** segments: a greedy
  top-down piecewise-linear split of the elevation profile (Douglas–Peucker style), each segment at
  least 1/(3N) of the course. Flat courses are split evenly. This step is the same as in the reference.
* **Variables and feasibility.** There is one power target per segment, bounded by
  `[min %, max %] × CP` (default 60–130 %). A plan is feasible when `W′bal ≥ 0.2 % of W′` at **every**
  simulation step (or at least the baseline's own minimum, if that is lower).
* **Objective.** Total time, always measured by running the full simulator.
* **Stage 1, seeds.** Plans of the form `P_k = clip(c + α·grade_k, min, max)`, which push harder on
  climbs and ease off on descents, are tried for 12 slopes α (up to 24 % of CP per 1 % grade). For
  each α, the offset `c` is bisected to the highest feasible level. The fastest feasible seed replaces
  the baseline if it is faster.
* **Stage 2, refinement.** Each iteration probes adding and removing a fixed amount of *energy*
  (joules) on every segment, so a long segment gets a small power change and a short one a larger
  change. It then tries:
  * a pure increase on one segment, if it stays feasible (spare W′), otherwise
  * transfers: the increase on segment *i* paired with a cut on segment *j*. The cut is sized so
    that the W′ balance, predicted step by step from the two probes, stays above the floor.

  The 12 best-ranked candidates are fully simulated and the fastest feasible one is kept. When
  nothing helps, the step is halved. The search stops when the step falls below 200 J.
* **Budget.** At most 4000 simulations, 15 s and 500 iterations. On the demo course it typically
  needs about 900 simulations and well under a second.
* **Honesty rules** (unchanged from the reference):
  1. The plan is rounded **down** to whole watts (lower power can only raise W′bal), then
     **independently re-simulated**. Only that verified result is reported.
  2. If the verified saving is below 0.5 s, the result is "no meaningful improvement" and the baseline
     is recommended. If verification fails, nothing is reported as an improvement.
  3. Solver details show the method, whether the stopping criterion was reached, the number of
     simulations and the budget.
* **Assumptions and limits.** This is a local search. It can stop at a plan that is not the global
  optimum, and its result depends on the starting baseline, the seed family and the step sizes. The
  linear W′ prediction only ranks candidates; feasibility is always decided by the full simulation.

## 7. Known limitations

* No wind, drafting, cornering/braking for turns, accelerations, or rotational inertia.
* No altitude-dependent air density or CP; no temperature, fatigue drift or nutrition.
* W′ recovery uses a single-parameter approximation; individual recovery can differ substantially.
* Power targets are constant within segments; real riders vary power continuously.
* The browser optimizer is a heuristic local search (§6), not a proven optimum.
* Equal-time-weighted "normalized power" is an approximation (30 s rolling, 1 s resampling).
* Courses from GPS have noisy elevation; smoothing and clipping change the gradient you see.
* Results are deterministic estimates — not error-bounded predictions.
