# Demo script (≈5 minutes)

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

## Drafting check (≈2 minutes)

9. **Open the tab.** Click **Drafting check**, then **Try multi-rider demo**. Point out the
   "Illustrative settings" notice and the illustrative turnaround exemption (3950–4085 m). Expected
   counts:
   * Potential drafting **1**
   * Inconclusive **4**
   * Overtake 2, Drop-back 2, Brief encounter 1, Exempt 2
10. **Inspect the flag.** E001 (R02 behind R01) is selected:
    * about 159 s, median gap ~7 m, ±3 m, 78 % probability inside the zone, quality "good"
    * the gap chart stays under the dashed 10 m zone edge with its ±1σ band
    * limitations list the exempt seconds and that lateral data could not be used
11. **Distinctions.** Inspect an inconclusive R08 → R07 interval: ±13 m uncertainty, order uncertain,
    explicitly *not* a flag. Tick **Show non-flagged intervals** to see:
    * R04 passing R03 within 20 s (overtake + drop-back)
    * R06's brief encounter

    Open **Proximity not treated as following** to see:
    * opposite-direction crossings with R09, who rides back
    * the R11/R12 side-by-side pair
    * outbound/return riders on separate course sections
12. **Adjust rules.** Choose the preset **Illustrative — long zone (15 m / 30 s)**. Results go stale
    and export is disabled. Click **Re-run analysis**: inconclusive drops to 3, and R02 → R01 is still
    the only flag.
13. **Upload.** Click **Upload rides (.fit)…** and select all three files in
    `samples/drafting_two_riders/` (`A.fit`, `B.fit`, `course.gpx`). No accuracy is recorded, so the
    5 m assumption applies, and the result is **inconclusive**, not a flag. Open *Data-quality
    settings*, set *Assumed accuracy* to 1 m and re-run: now 1 potential drafting. Then upload `A.fit`
    together with `samples/drafting_invalid.fit` to show the validation error.
14. **Export.** Click **Export JSON (full)** and **Export CSV (intervals)**. Both include the
    settings, the illustrative note, per-interval limitations and the disclaimer.
