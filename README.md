# Vector Pacing

**Cycling pacing simulator and optimizer, with a potential-drafting check — v1.0.0 (demonstration release).**

Two independent tools in one local app:

* **Pacing simulator.** Load a course and describe the rider. Simulate a constant-power ride, let the
  optimizer propose a variable-power plan, compare the two, and export the result.
* **Drafting check.** Load timestamped multi-rider positions from an event where drafting is
  restricted (e.g. an ITT). Review intervals of *potential* drafting on a timeline and course view,
  adjust the event rules, and export the findings.

> **Model estimates only.** Pacing results come from a simplified steady-state physics model and a
> CP/W′ balance approximation with the parameters you enter. They are not validated predictions and
> are not medical or training advice. See [docs/MODEL.md](docs/MODEL.md).
>
> **Potential drafting only.** Drafting intervals are prompts for human review based on GNSS
> estimates and configurable, illustrative settings. They are not confirmed rules violations, and no
> penalty is implied. See [docs/DRAFTING.md](docs/DRAFTING.md).

## What v1.0 does

* **Courses.** A bundled, deterministic demo course (32 km, ~317 m climbing, works offline) or
  your own GPX upload. Parsing is safe (`defusedxml`) and handles namespaces, multiple track segments,
  routes, duplicate points and missing elevation. Malformed, empty, zero-length and oversized files
  get clear error messages.
* **Physics.** Aero drag, rolling resistance, gravity, drivetrain efficiency and rider + bike mass.
  Explicit rules for coasting on steep descents, a braking speed limit, stalls and solver failures.
* **Athlete model.** Critical power and W′ with W′-balance depletion and recovery tracked along the
  course. Exhaustion is reported as infeasible and the negative balance is shown, not clamped.
* **Baseline.** Constant power, either picked automatically (the highest feasible constant power) or
  entered manually. The power used is always shown.
* **Optimization.** Terrain-aware segments (2–30), power bounds, and W′ ≥ 0 at every step, solved by
  SLSQP within a fixed compute budget. Each proposed plan is checked again in the same simulator
  before it is reported. If the gain is negligible or the solver fails, the app says so.
* **Interface.** Charts of elevation, power, speed and W′ against distance; a summary of time,
  feasibility and time difference; a per-segment plan table; solver details. The UI includes
  loading, empty, error, retry, cancel and reset states. Results go stale when inputs change. The
  layout supports keyboard use and narrow screens.
* **Export.** Full JSON with inputs, units, versions, summaries, segments and per-step series, plus
  a per-step CSV that compares the two plans.
* **Drafting check** (separate from pacing):
  * **Data.** A bundled deterministic 12-rider ITT scenario, or upload of one `.fit` ride per rider
    (plus an optional course `.gpx`).
  * **Detection.** Sustained following within a configurable zone. Lateral separation is used when
    the position accuracy supports it.
  * **Power screen** (from [RaceGuard](https://github.com/alfonsomartin03/RaceGuard), merged here).
    Rides with power are checked for sections where the rider held speed on clearly less power than
    in their own comparable sections. This works with a single ride, and findings are linked with
    overlapping GPS intervals.
  * **Command line.** `vector-pacing-review R01.fit R02.fit --course course.gpx` prints a text
    report (`--json` for the full report).
  * **What it tells apart.** Following is distinguished from overtakes, drop-backs, brief
    encounters, opposite-direction riders, side-by-side riders and nearby separate course sections.
  * **Data quality.** Missing samples, misaligned timestamps, GNSS uncertainty and insufficient
    accuracy are accounted for. Low-quality intervals are "inconclusive", not flagged.
  * **Rules.** Zone length, passing time, drop-back time and exempt sections are configurable.
    Presets are illustrative only.
  * **Review and export.** A timeline, a course view and per-interval gap charts with limitations.
    JSON/CSV export includes the settings and data-quality limitations.

## Quick start

Requirements: Python ≥ 3.11, Node ≥ 20.19 (with npm), `make`. Runtime needs no network access or
accounts (installing dependencies does need network).

```bash
make install
```

Single-process demo (builds the UI, then serves UI + API):

```bash
make serve
```

Open <http://127.0.0.1:8000> and click **Try demo course**, then **Optimize pacing**. For the
drafting check, open the **Drafting check** tab and click **Try multi-rider demo**.

Development mode (API with reload on :8000, Vite dev server on :5173):

```bash
make dev
```

Without `make`:

```bash
python3 -m venv backend/.venv && backend/.venv/bin/pip install -e "backend[dev]"
cd frontend && npm ci && npm run build && cd ..
backend/.venv/bin/uvicorn vector_pacing.api:app --app-dir backend --port 8000
```

## Checks

```bash
make check
```

This runs ruff, mypy, pytest, eslint, tsc, vitest and the production build, the same checks as
[CI](.github/workflows/ci.yml). Run `make test` for tests only.

## Repository layout

```
backend/vector_pacing/   course.py · physics.py · athlete.py · simulate.py · optimize.py · api.py
                         drafting.py · power_screen.py · review.py · fit.py · drafting_api.py · cli.py
                         (potential-drafting check: GPS following + RaceGuard power screen; independent of pacing)
backend/vector_pacing/data/   demo_course.gpx · drafting_demo.json  (regenerate with backend/scripts/*.py)
backend/tests/           pytest: parsing, physics, W′, simulation, optimizer, drafting rules, API
frontend/src/            React app; drafting UI in src/drafting; vitest tests in src/test
samples/                 GPX and .fit files for upload testing (incl. deliberately invalid ones)
docs/                    MODEL.md · DRAFTING.md · ARCHITECTURE.md · DEMO.md · RELEASE_CHECKLIST.md
```

## Documentation

* [Model assumptions and limitations](docs/MODEL.md)
* [Drafting check: method, import format, settings, limitations](docs/DRAFTING.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Demo script](docs/DEMO.md)
* [Release notes](RELEASE_NOTES.md)
* [Release checklist](docs/RELEASE_CHECKLIST.md)

## Known limitations

**Pacing:**
* No wind, drafting benefit, acceleration or inertia.
* Air density and CP are constant, and W′ recovery is a simple approximation.
* The optimizer is local and works on segments, so it does not guarantee a global optimum.
* Estimates come without uncertainty bounds.

Details are in [docs/MODEL.md §7](docs/MODEL.md#7-known-limitations).

**Drafting check:**
* It relies on GNSS positions and the course line.
* With typical consumer accuracy it cannot tell riders side by side from riders in line.
* It knows nothing about road context.
* It does not ship any official rule set.

Details are in [docs/DRAFTING.md](docs/DRAFTING.md#limitations).

## Deferred ideas (not part of v1.0)

The original concept went further. The following are **not implemented** and are not planned for
this release:

* Recorded-ride replay, live telemetry and WebSocket streaming (FIT files are read for the drafting
  check only, not for pacing)
* Time-series database storage (PostgreSQL/TimescaleDB)
* Heart rate, cadence and other sensor channels, and Bluetooth/ANT+, Garmin or smart-trainer integration
* Real-time pacing recommendations during a ride
* Wind/weather, aerodynamic drafting benefit, pack dynamics, tactical multi-rider optimization and
  team time-trial simulation (the drafting *check* only detects potential drafting; it does not model
  its effect)
* Live rider tracking integrations and named governing-body rule presets
* Monte Carlo uncertainty, machine-learning fatigue models, automatic CdA estimation
* Maps, cloud deployment, Docker and accounts

The original motivating question still stands: **can physics, physiology and numerical optimization
together say how to ride a course as fast as possible?** v1.0 answers it for the offline,
pre-race planning case and leaves the live-telemetry case unaddressed.
