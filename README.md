# Vector Pacing

**Cycling pacing simulator and optimizer — v1.0.0 (demonstration release).**

Load a course, describe the rider, simulate a constant-power ride, let the optimizer propose a
variable-power plan, compare the two, and export the result.

> **Model estimates only.** Results come from a simplified steady-state physics model and a CP/W′
> balance approximation with the parameters you enter. They are not validated predictions and are
> not medical or training advice. See [docs/MODEL.md](docs/MODEL.md).

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

Open <http://127.0.0.1:8000> and click **Try demo course**, then **Optimize pacing**.

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
backend/vector_pacing/data/demo_course.gpx   bundled demo (regenerate: backend/scripts/generate_demo_course.py)
backend/tests/           pytest: parsing edge cases, physics, W′, simulation rules, optimizer, API
frontend/src/            React app, API client, validation, export, charts; vitest tests in src/test
samples/                 GPX files for upload testing (incl. a deliberately malformed one)
docs/                    MODEL.md · ARCHITECTURE.md · DEMO.md · RELEASE_CHECKLIST.md
```

## Documentation

* [Model assumptions and limitations](docs/MODEL.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Demo script](docs/DEMO.md)
* [Release notes](RELEASE_NOTES.md)
* [Release checklist](docs/RELEASE_CHECKLIST.md)

## Known limitations

No wind, drafting, acceleration or inertia. Air density and CP are constant. W′ recovery is a simple
approximation. The optimizer is local and works on segments, so it does not guarantee a global
optimum. Estimates come without uncertainty bounds. Details are in
[docs/MODEL.md §7](docs/MODEL.md#7-known-limitations).

## Deferred ideas (not part of v1.0)

The original concept went further. The following are **not implemented** and are not planned for
this release:

* FIT file parsing, recorded-ride replay, live telemetry and WebSocket streaming
* Time-series database storage (PostgreSQL/TimescaleDB)
* Heart rate, cadence and other sensor channels, and Bluetooth/ANT+, Garmin or smart-trainer integration
* Real-time pacing recommendations during a ride
* Wind/weather, drafting, multi-rider and team time-trial simulation
* Monte Carlo uncertainty, machine-learning fatigue models, automatic CdA estimation
* Maps, cloud deployment, Docker and accounts

The original motivating question still stands: **can physics, physiology and numerical optimization
together say how to ride a course as fast as possible?** v1.0 answers it for the offline,
pre-race planning case and leaves the live-telemetry case unaddressed.
