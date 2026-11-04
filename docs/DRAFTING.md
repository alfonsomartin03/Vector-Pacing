# Potential-drafting detection (drafting model `vp-draft-1.0`)

The **Drafting check** tab reviews multi-rider position data from events where drafting is restricted,
such as individual time trials and non-drafting triathlon bike legs. It reports **potential drafting**
for a person to review. It **does not** confirm a rules violation, and it never issues or suggests
penalties.

It combines two independent kinds of evidence:

* **GPS following check** (two or more riders): sustained following inside a configurable zone,
  described below.
* **Within-rider power screen** (any rider whose `.fit` file has power, even a single ride): sections
  where the rider held speed on clearly less power than in their own comparable sections. It comes
  from [RaceGuard](https://github.com/alfonsomartin03/RaceGuard), merged into this repository; see
  [Power screen](#power-screen-within-rider). Where both kinds of evidence overlap in time for the
  same rider, they are linked in the report.

The drafting check is independent of the pacing simulator and optimizer. It does not model pack
dynamics or multi-rider tactics.

Code: [`backend/vector_pacing/drafting.py`](../backend/vector_pacing/drafting.py) (GPS check),
[`power_screen.py`](../backend/vector_pacing/power_screen.py) (power screen),
[`review.py`](../backend/vector_pacing/review.py) (`.fit` upload → both checks),
[`drafting_api.py`](../backend/vector_pacing/drafting_api.py) (HTTP),
[`cli.py`](../backend/vector_pacing/cli.py) (command line),
[`frontend/src/drafting/`](../frontend/src/drafting) (UI).

Command line (installed with the backend):

```bash
vector-pacing-review samples/drafting_with_power/R01.fit samples/drafting_with_power/R02.fit --course samples/drafting_with_power/course.gpx
```

Add `--json` for the full JSON report, `--output FILE` to write it to a file and `--settings '{"zone_length_m": 12}'`
to change detection settings. The exit status is 2 for unreadable input.

## Rules are configurable, not universal

Different events and governing bodies use different zone lengths, passing times and exemptions, and
they revise them. Vector Pacing therefore ships **no named governing-body presets**. Their current
official rules could not be verified for this release.

The bundled presets are labelled *Illustrative* and are explicitly not taken from any governing body:

| Preset | Zone | Allowed pass | Drop-back | Lateral |
| --- | --- | --- | --- | --- |
| Illustrative — standard (default) | 10 m | 20 s | 20 s | 2 m |
| Illustrative — long zone | 15 m | 30 s | 30 s | 2 m |
| Illustrative — short zone | 7 m | 15 s | 15 s | 1.5 m |

For a real event, enter that event's published rules. If governing-body presets are added later,
each one must record its source document and effective date.

### Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| Zone length | 10 m | Course distance behind a rider that counts as following. |
| Allowed passing time | 20 s | Time a rider may spend in the zone while overtaking. |
| Drop-back allowance | 20 s | Time an overtaken rider has to leave the new leader's zone. |
| Lateral separation | 2 m | Riders further apart sideways are not following. Used only with precise data (next row). |
| Accuracy needed for lateral use | 1 m | Both riders must report accuracy ≤ this for lateral separation to be used. |
| Exempt course sections | none (demo: turnaround 3950–4085 m) | Time with the follower inside these ranges is not counted. |
| Assumed accuracy | 5 m | Used where samples carry no `accuracy_m`. |
| Clock misalignment tolerance | 0.2 s | Assumed device clock offset; adds `speed × tolerance` to the gap uncertainty. |
| Max interpolation gap | 5 s | Longer gaps between samples are treated as missing. |
| Merge gap | 5 s | Brief exits from the zone (or missing data) up to this long do not split an interval. |
| Smoothing window | 5 s | Rolling median applied to positions. |
| Analysis step | 1 s | Common time grid. |

## Import: `.fit` rides

Upload **one `.fit` activity file per rider** — the file the head unit records, or the "original
file" export from Garmin Connect, Strava, Wahoo, etc. Optionally add **one `.gpx` course file**; it
provides the course line every rider is projected onto. Without it, the first rider's own track
(thinned to ~5 m spacing) is used as the course line, and the scenario description says so. A neutral
course file is better: a rider's track carries that rider's GNSS error.

* From each FIT file, `record` messages with `timestamp`, `position_lat` and `position_long` are read;
  `gps_accuracy` (metres) is used as the 1-σ horizontal accuracy where the device records it. Where it
  is missing, the *assumed accuracy* setting is used and the rider is marked "accuracy not reported".
  Most consumer devices do not record it. For the power screen, `speed`/`enhanced_speed`, `power`,
  `cadence`, `heart_rate`, `altitude`/`enhanced_altitude` and `distance` are read too; if a file has no
  speed, it is derived from GPS positions (noisier, and reported).
* Records without a GPS fix (e.g. indoor sections, tunnels) are skipped and counted as a warning.
* Rider ids come from the file names (`R01.fit` → `R01`); duplicates get a `-2`, `-3`… suffix.
* FIT timestamps are absolute UTC, so riders are synchronised by clock. Head-unit clocks are GPS-
  synchronised; the *timestamp tolerance* setting covers residual misalignment.
* The FIT container is decoded by a small built-in reader (`backend/vector_pacing/fit.py`): normal and
  compressed-timestamp headers, developer fields, either byte order and chained files are supported,
  and the file checksum is verified. Corrupted or truncated files are rejected with a message.
* Limits: 20 MB per file, 1–200 riders (the GPS check needs at least 2), 500 000 positions in total,
  24 h per rider. Live tracking integrations are not supported.

Examples (regenerate with `backend/scripts/generate_fit_samples.py`):
* [`samples/drafting_two_riders/`](../samples/drafting_two_riders/) (`A.fit`, `B.fit`, `course.gpx`):
  positions only, no accuracy recorded.
* [`samples/drafting_with_power/`](../samples/drafting_with_power/) (`R01.fit`, `R02.fit`,
  `course.gpx`): 15 min with power, speed and cadence. R02 sits 5 m behind R01 for 90 s on ~100 W less
  than its own solo power. Expected: one potential-drafting interval (E001) linked to one power anomaly
  (P001). `R02.fit` alone shows the single-ride power screen.
* `samples/drafting_invalid.fit`: a truncated file.

API: `POST /api/drafting/analyze-fit` (multipart: repeated `files`, optional `course`, `settings` as a
JSON string). Internally the files are converted to the `vector-pacing-riders/1` structure that the
bundled demo scenario
([`backend/vector_pacing/data/drafting_demo.json`](../backend/vector_pacing/data/drafting_demo.json))
uses; `POST /api/drafting/analyze` still accepts that structure for programmatic use.

## Method

1. **Course positions.** Samples are converted to a local metric frame and projected onto the course
   line. Projection uses continuity:
   * Each sample is matched only within a window around the rider's previous course position.
   * Course segments close to the sample are grouped into contiguous sections, and the section
     nearest the rider's *predicted* position is chosen. The position within that section is purely
     geometric.
   * The first sample is matched using the rider's initial heading.

   This keeps riders on the correct lane of an out-and-back course or switchback, even when the
   lanes are only metres apart.
2. **Synchronisation.** Each rider is linearly interpolated onto a common time grid. Grid points
   bracketed by samples further apart than the max interpolation gap are *missing*. This handles
   misaligned timestamps and irregular sampling. A rolling median reduces GNSS jitter.
3. **Zone test** for every ordered pair (follower F, leader L) at every grid time:
   * `0 < gap ≤ zone length`, where `gap = course position(L) − course position(F)`
   * both riders are moving (> 1 m/s) in the same direction (velocity vectors agree)
   * the straight-line distance is consistent with the course gap (≤ zone + 3σ)
   * the riders are not laterally separated, a test applied only when both accuracies permit it

   The combined 1-σ gap uncertainty is `σ = √(acc_F² + acc_L² + (clock tolerance × speed)²)`.
4. **Intervals.** In-zone times are joined into intervals, bridging gaps up to the merge gap. Each
   interval records:
   * how it started: approached from behind, or *just overtaken*
   * how it ended: follower passed, gap opened, usable data stopped, or zone conditions ended
5. **Classification** of each interval, with counted time = duration − time in exempt sections:

   | Class | Rule |
   | --- | --- |
   | Exempt section | Counted time is zero (entirely inside exempt sections). |
   | Inconclusive (data quality) | Data quality is *insufficient* (below), so following can't be told from passing or riding apart. Nearby inconclusive intervals of the same two riders, in either order, are merged and marked "order uncertain". |
   | Overtake within allowance | Counted ≤ allowed passing time, and the follower ended ahead. |
   | Drop-back within allowance | Started just after being overtaken; counted ≤ drop-back allowance. |
   | Brief encounter | Counted ≤ the allowance, no pass. |
   | Inconclusive | Over the allowance, but the mean probability that the gap was inside the zone is below 60 % (e.g. riding right at the zone edge). |
   | **Potential drafting** | Over the allowance with adequate data. Slow passes (pass completed after the allowance) are included and noted. |

   Data quality, using the median σ over the interval and its missing-data fraction:
   * **good**: σ ≤ zone/3 and ≤ 10 % missing
   * **limited**: σ ≤ zone/2 and ≤ 30 % missing
   * **insufficient**: anything worse

   The probability inside the zone is the mean of `Φ((zone − gap)/σ) − Φ(−gap/σ)`, treating the gap
   error as Gaussian.
6. **Proximity that is not following** is listed separately, so the distinction is visible. These
   are riders within the zone length of each other in a straight line who fail the zone test as:
   * **separate course section**: course gap > zone + 3σ, e.g. outbound vs. return lane, or
     adjacent switchbacks
   * **opposite direction**: velocity vectors point opposite ways
   * **side by side**: lateral separation, with precise data only
   * **stationary**

## Reported for each interval

The report for each interval includes:
* follower and leader IDs, the classification, and start/end time
* duration, counted time and exempt time
* missing-data time
* gap median, minimum and maximum, plus the 1σ uncertainty and the probability inside the zone
* data quality and whether lateral data was used
* course range
* the supporting time series (course gap, uncertainty, straight-line distance, lateral separation,
  follower position) for 15 s either side
* a list of data-quality limitations specific to that interval

For `.fit` uploads each interval also lists `power_anomaly_ids`: power anomalies of the follower that
overlap it in time.

Exports (JSON and CSV) include all of this plus the settings, the illustrative-settings note,
general limitations, per-rider data quality, the power screen and the disclaimer.

## Power screen (within rider)

Ported unchanged from RaceGuard's matched-section evidence model (`raceguard.analysis`; version
`raceguard-matched-sections-1`). Drafting reduces the aerodynamic power a rider needs for a given
speed. The screen compares each stable part of a ride with the **same rider's** riding elsewhere in
that ride, so no generic CdA can create a flag by itself.

1. **Frames.** A centred 7 s window smooths speed, power, grade and acceleration. Grade comes from
   elevation over ≥ 20 m. Coasting, soft pedalling (below max(75 W, 35 % of the rider's median
   power)), cadence below 30 rpm, ±4 s around pedalling transitions, speeds below 8 m/s and recording
   gaps (> 5 s) are excluded. Travel direction is used only when GPS movement is coherent (no
   turnarounds or stationary wander).
2. **Sections.** Stable 10 s sections, sampled every 5 s so an event between fixed boundaries is not
   diluted. Within a section, speed may vary by ≤ 1.5 m/s and grade by ≤ 2 %.
3. **Comparison.** Each section is compared with the rider's sections 30 s – 30 min away that are
   within 20° of direction, 0.75 % of grade and 0.2 m/s² of acceleration. Same-speed sections
   (± 0.75 m/s) are preferred. Otherwise sections within 3.5 m/s are speed-normalised with a v³
   aerodynamic adjustment, and the thresholds rise. The rolling, climbing and acceleration terms use
   75 kg + 8 kg, Crr 0.004 and 97 % drivetrain efficiency.
4. **Flag.** The median reference must exceed measured power by ≥ 45 W **and** 15 % (60 W and 20 %
   when speed-normalised), and by 2.5 × a robust spread of the references. The deficit may not exceed
   70 % of the aerodynamic share of the reference. At least 2 reference sections are needed.
5. **Anomalies.** Flagged frames are joined (gaps ≤ 5 s). Anything shorter than 15 s is dropped. The
   **review priority** is 0.55 + 0.14 × duration score (duration / 45 s) + 0.15 × power score (relative
   deficit / 0.35), capped at 0.95. It is a heuristic ranking, **not a probability**.

Each anomaly reports the rider, time, duration, average speed, measured vs. reference power, the
number of comparable sections, whether speed normalisation was needed, the mean position and the
linked GPS interval(s). Riders without power are listed as skipped.

**What the power screen can't tell apart:** tailwind or gusts, a change of riding position or
equipment, road surface, and power-meter error all look like drafting. A ride with no repeated
comparable conditions (short, hilly or constantly changing) gives no anomalies and says so. The
thresholds come from RaceGuard and have not yet been validated against labelled solo and drafting
rides.

## Bundled scenario

`Out-and-back ITT — illustrative drafting scenario` is synthetic and deterministic. Regenerate it
with `backend/scripts/generate_drafting_demo.py`. It contains 12 riders on an 8 km out-and-back
course with lanes 8 m apart. Expected results with the default settings and the illustrative
turnaround exemption:

| Riders | Case | Result |
| --- | --- | --- |
| R02 → R01 | Sustained following at ~6–7 m for ~2.5 min | **Potential drafting** (only flag) |
| R04 → R03, R03 → R04 | Pass taking ~18 s, then drop-back | Overtake / drop-back within allowance |
| R06 → R05 | Approaches to 8 m for ~15 s, falls back | Brief encounter |
| R08 ↔ R07 | Near the zone edge, 9 m GNSS accuracy, irregular timestamps, dropouts | Inconclusive (not flagged) |
| R09 vs. R10–R12 | R09 abandons and rides back against traffic | Opposite direction (not following) |
| R12 / R11 | Side by side 3 m apart with 0.3 m accuracy | Lateral separation (not following) |
| outbound vs. return | Riders pass 8 m apart on opposite lanes | Separate course section (not following) |
| R12 → R10 | Normal pass | Overtake / drop-back within allowance |

## Limitations

* Positions are GNSS estimates. Smoothing, interpolation and projection all introduce error.
  Multipath near buildings or trees is not modelled.
* Course positions depend on the course line. Riders far from it (> 50 m is warned) or on sections
  that overlap exactly can be misplaced.
* Device clock offsets beyond the configured tolerance are not detected.
* Lateral separation is rarely usable with consumer GNSS (accuracy typically 2–5 m). Without it,
  riders side by side can't be told from riders in line.
* The model knows nothing of road layout, obstacles, crashes, marshal instructions, motorbikes or
  vehicles, or other context a commissaire would consider.
* The GPS check does not estimate the aerodynamic benefit of following. The power screen looks for a
  power deficit, but cannot separate drafting from wind (see above).
* Thresholds are configurable and the defaults are illustrative. Results depend on them.
