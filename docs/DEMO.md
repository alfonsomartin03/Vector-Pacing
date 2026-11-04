# Demo script (≈3 minutes)

Prerequisite: `make install` once, then `make serve` and open <http://127.0.0.1:8000>.

1. **Start.** Point out the empty state and the "Model estimates only" footer. Click **Try demo course**.
   The *Vector Valley TT (demo)* loads: 32.0 km, ~317 m climbing, −5.7 % to +6.9 %.
2. **Rider settings.** Defaults: CP 280 W, W′ 20 kJ, 72 kg + 8.5 kg, CdA 0.28 m², Crr 0.004.
   Hover/read the help text under each field. Type `abc` in CP to show inline validation and the
   disabled run buttons, then restore `280`.
3. **Baseline.** Click **Simulate baseline**. Auto baseline = 286 W constant (the highest whole-watt
   power that keeps W′ ≥ 0), estimated 52:05, feasible. Show the W′ chart draining to ~1 kJ at the finish.
4. **Optimize.** Click **Optimize pacing** (~2 s). Expected: optimized 51:04, feasible,
   **−61.2 s** under the model. Show the power chart (harder on the km 9–11 climb and the late kicker,
   easier on descents) and the W′ chart touching but never crossing zero. Open **Solver details** —
   SLSQP may report the iteration limit; the best *verified* feasible plan is used anyway.
5. **Stale protection.** Change W′ to `15`. Banner: "Results are out of date", charts dim, export
   disabled. Click **Optimize pacing** again to refresh.
6. **Infeasible input.** Choose **Manual** baseline at `400` W → Optimize. Baseline shows
   "Infeasible", W′ reaches about −302 kJ (not clamped), and optimization refuses with an explanation.
   Switch back to **Auto**.
7. **Upload.** Click **Upload GPX…** and choose `samples/hill_climb_two_segments.gpx`. Notes explain
   joined segments, removed duplicate points and interpolated elevation. Optimize again.
   Then try `samples/invalid_malformed.gpx` to show the error + "Choose another file".
8. **Export.** Click **Export JSON (full)** and **Export CSV (per step)**. Both include parameters,
   units, app version 1.0.0 and model `vp-model-1.0`.
