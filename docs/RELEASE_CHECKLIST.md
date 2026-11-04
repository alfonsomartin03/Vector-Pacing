# v1.0.0 release checklist

Evidence recorded 2026-11-03 on macOS (Python 3.14.6, Node 24.18.0). "Fresh copy" = the working tree
copied without `.venv`, `node_modules`, `dist` or caches, then `make install && make check`.

| # | Item | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Fresh install from documented commands | ✅ | Fresh copy: `make install` exit 0 |
| 2 | Backend lint / format / types | ✅ | `ruff check`, `ruff format --check`, `mypy --strict`: clean |
| 3 | Backend tests | ✅ | `pytest`: 76 passed |
| 4 | Frontend lint / types / tests | ✅ | `eslint`, `tsc --noEmit`: clean; `vitest`: 17 passed |
| 5 | Production build | ✅ | `vite build` succeeds |
| 6 | Single-process serve | ✅ | Fresh copy: `/api/health` ok, `/` serves the built UI |
| 7 | Demo works offline, no credentials | ✅ | Demo GPX is bundled; no runtime network calls |
| 8 | End-to-end demo in a real browser | ✅ | Demo → optimize: 52:05 vs 51:04, −61.2 s, both feasible |
| 9 | Uploaded course | ✅ | Browser upload of a generated GPX (−18.2 s); API run on `samples/hill_climb_two_segments.gpx` (−9.8 s, 3 warnings) |
| 10 | Invalid upload | ✅ | Malformed GPX in browser → clear error, previous course kept; tests cover empty, non-GPX, oversize, entity attack |
| 11 | Infeasible input | ✅ | Manual 400 W → W′ −302 kJ shown unclamped; optimization refuses with explanation |
| 12 | Solver failure / no improvement | ✅ | Tests: budget exhaustion reported, infeasible start refused, flat course → "no meaningful improvement" |
| 13 | Repeat runs | ✅ | Two consecutive browser optimizations identical; determinism tests |
| 14 | Stale results | ✅ | Editing W′ after a run → banner, dimmed results, export disabled (browser + test) |
| 15 | Export | ✅ | JSON contains inputs, units, versions, comparison, series; CSV 2000 steps with both strategies |
| 16 | Desktop and mobile layout | ✅ | Inspected at 1440×900 and 375×812; mobile overflow bug found and fixed |
| 17 | Keyboard navigation | ✅ (spot check) | Skip link → "Try demo course" → Enter loads course; all controls are native buttons/inputs |
| 18 | Contrast | ✅ (by design, not tool-audited) | Text/series colours chosen at ≥ 4.5:1 on white; no automated audit run |
| 19 | CI workflow | ⚠️ written, not run | `.github/workflows/ci.yml` mirrors `make check`; not executed on GitHub (nothing pushed) |
| 20 | Screen-reader pass | ⚠️ unverified | Semantics/labels in place; no assistive-technology testing performed |
| 21 | Windows / Linux hosts | ⚠️ unverified locally | CI targets Ubuntu; Windows not tested (`make` assumed) |
| 22 | Docs: setup, architecture, model, limitations, demo, release notes | ✅ | README, docs/, RELEASE_NOTES.md |

Not done by design: no push, tag, GitHub release or deployment (requires owner authorization).
