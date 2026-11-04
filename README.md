# Vector Pacing

**Cycling pacing simulator with a potential-drafting check — a static demo that runs entirely in your
browser (v1.1.0).**

Live site (after the Pages workflow has run): <https://alfonsomartin03.github.io/Vector-Pacing/>

Two independent tools in one page:

* **Pacing simulator.** Load a course and describe the rider. Simulate a constant-power ride, let a
  simple terrain-aware heuristic propose a variable-power plan, compare the two and export the result.
* **Drafting check.** Review intervals of *potential* drafting in a multi-rider time-trial scenario
  on a timeline and course view, adjust the event rules and export the findings.

There is no backend, API server, account or paid service. All calculations run in TypeScript in a
Web Worker. Demo data is bundled with the site, and files you choose are read locally and never
uploaded.

> **Model estimates only.** Pacing results come from a simplified steady-state physics model and a
> CP/W′ balance approximation with the parameters you enter. They are not validated predictions and
> are not medical or training advice. See [docs/MODEL.md](docs/MODEL.md).
>
> **Potential drafting only.** Drafting intervals are prompts for human review based on GNSS
> estimates and configurable, illustrative settings. They are not confirmed rules violations, and no
> penalty is implied. See [docs/DRAFTING.md](docs/DRAFTING.md).

## What the demo does

**Pacing**

* **Courses:** the bundled demo course (32 km, ~317 m climbing) or any GPX file with a track or
  route. Missing elevation, duplicate points, multiple segments and malformed files are handled with
  clear messages.
* **Rider inputs:** CP, W′, masses, CdA, Crr, drivetrain efficiency, air density, speed limit,
  coasting threshold, baseline mode (Auto or Manual) and optimizer bounds and segment count.
* **Constant-power baseline:** the same physics, W′-balance and coasting/speed-limit/stall rules as
  the original Python model. On the demo course the TypeScript port reproduces the Python results
  exactly (Auto baseline 286 W, 3125.42 s).
* **Variable-power plan:** a bounded, browser-friendly heuristic (see below), checked by the same
  simulator and reported only if it is verified feasible and at least 0.5 s faster.
* **Comparison and export:** elevation, power, speed and W′ charts; a summary; a per-segment plan
  table; solver details; full JSON and per-step CSV export.

**Drafting check**

* The bundled 12-rider illustrative ITT scenario, analysed in the browser. The TypeScript port gives
  the same intervals, classifications and encounters as the Python reference on this scenario.
* Configurable zone length, passing and drop-back times, lateral separation, exempt sections and
  data-quality settings, with illustrative presets.
* Upload of one `.fit` ride per rider (two or more) plus an optional course `.gpx`, decoded in the
  browser, or of a `vector-pacing-riders/1` JSON document (example:
  [`samples/drafting_two_riders.json`](samples/drafting_two_riders.json)). On the `.fit` samples the
  results match the Python reference.
* Timeline, course view, per-interval gap chart, dismissed encounters, data quality by rider, and
  JSON/CSV export.

## Scope reductions in the browser version

Moving from the Python backend to a static site changed the following. The original Python code is
still in [`backend/`](backend/) as a reference. It is not needed to build or run the demo.

| Area | Before (Python API) | Now (browser) |
|---|---|---|
| Pacing optimizer | SciPy SLSQP (gradient-based, constrained) | Bounded two-stage heuristic: grade-proportional seeds, then pairwise power transfers between segments, every candidate fully simulated. Not equivalent to SLSQP: on the demo course it finds a 57.3 s gain where SLSQP found 61.2 s. |
| Drafting uploads | One or more `.fit` rides plus an optional course `.gpx` | Two or more `.fit` rides (positions only) plus an optional `.gpx`, or one `vector-pacing-riders/1` JSON file. A single ride is no longer accepted, because it was only useful for the power screen. |
| Power screen (RaceGuard) | Within-rider power anomalies for `.fit` rides with power | Removed: the UI, exports and FIT power decoding. |
| Command line | `vector-pacing-review` | Only in the Python reference. |
| Content hashes | SHA-256 | A short non-cryptographic hash, used only to detect changed inputs. |

## Run locally

Requirements: Node ≥ 20.19 with npm. `make` is optional.

```bash
make install
```

```bash
make dev
```

Then open <http://127.0.0.1:5173>, click **Try demo course** and then **Optimize pacing**. For the
drafting check, open the **Drafting check** tab and click **Try multi-rider demo**.

Without `make`:

```bash
cd frontend && npm ci && npm run dev
```

## Build and check

```bash
make check
```

This runs ESLint, `tsc`, the Vitest suite and the production build, the same checks the Pages
workflow runs before deploying. `make test` runs the tests only.

To serve the production build exactly as GitHub Pages will, under `/Vector-Pacing/`:

```bash
make pages-preview
```

Then open <http://127.0.0.1:4173/Vector-Pacing/>.

## Deploy to GitHub Pages

1. In the repository's **Settings → Pages**, set **Source** to **GitHub Actions**.
2. Push to `main`, or run the **Deploy GitHub Pages** workflow by hand.

The workflow ([.github/workflows/pages.yml](.github/workflows/pages.yml)) installs dependencies, runs
lint, type checks and tests, builds with `VITE_BASE_PATH` set to the repository's Pages path, and
publishes `frontend/dist`. No secrets, variables or other services are needed. Assets, the bundled
data and the Web Worker are all loaded relative to that base path, so a custom domain (base `/`) also
works.

## Repository layout

```
frontend/src/engine/     browser engine: course.ts (GPX), simulate.ts (physics, W′, rules),
                         optimize.ts (heuristic), pacing.ts, drafting.ts, fit.ts (FIT reader),
                         worker.ts, client.ts
frontend/src/data/       bundled demo course (GPX) and drafting scenario (JSON)
frontend/src/            React UI; drafting UI in src/drafting; Vitest tests in src/test
samples/                 GPX, FIT and JSON files for upload testing (including deliberately invalid ones)
backend/                 original Python implementation (reference only, not used by the demo)
docs/                    MODEL.md · DRAFTING.md · ARCHITECTURE.md · DEMO.md
```

## Documentation

* [Model assumptions and limitations](docs/MODEL.md)
* [Drafting check: method, import format, settings, limitations](docs/DRAFTING.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Demo script](docs/DEMO.md)
* [Release notes](RELEASE_NOTES.md)

## Known limitations

**Pacing**

* No wind, drafting benefit, acceleration or inertia.
* Air density and CP are constant, and W′ recovery is a simple approximation.
* The plan comes from a local heuristic. It is not guaranteed to be optimal and can differ from the
  Python reference's SLSQP result.
* Estimates come without uncertainty bounds.

**Drafting check**

* It relies on GNSS positions and the course line.
* With typical consumer accuracy it cannot tell riders side by side from riders in line.
* It knows nothing about road context.
* It does not ship any official rule set.

## Python reference (optional)

The original FastAPI + NumPy/SciPy implementation is kept in `backend/` for comparison: it includes
the SLSQP optimizer, the RaceGuard power screen and the `vector-pacing-review` command.
It needs Python ≥ 3.11:

```bash
make reference-install
```

```bash
make reference-test
```
