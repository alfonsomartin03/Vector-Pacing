# Potential-drafting detection (drafting model `vp-draft-1.0`)

The **Drafting check** tab reviews multi-rider position data from events where drafting is restricted,
such as individual time trials and non-drafting triathlon bike legs. It reports **potential drafting**
for a person to review. It **does not** confirm a rules violation, and it never issues or suggests
penalties.

The drafting check is independent of the pacing simulator and optimizer. It does not estimate
aerodynamic benefit and does not model pack dynamics or multi-rider tactics.

Code: [`backend/vector_pacing/drafting.py`](../backend/vector_pacing/drafting.py) (domain),
[`drafting_api.py`](../backend/vector_pacing/drafting_api.py) (HTTP),
[`frontend/src/drafting/`](../frontend/src/drafting) (UI).

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

## Import format `vector-pacing-riders/1`

A single JSON document with a course line and timestamped positions for each rider. File uploads are
limited to 20 MB, 200 riders, 500 000 samples and 24 h per rider. Live tracking integrations are not
supported.

```json
{
  "format": "vector-pacing-riders/1",
  "name": "My event, wave 2",
  "description": "optional",
  "illustrative": false,
  "start_time": "2026-06-14T08:00:00Z",
  "course": { "name": "optional", "points": [[44.1, 5.2], [44.1001, 5.2004]] },
  "riders": [
    {
      "id": "R01",
      "label": "optional display name",
      "samples": [
        { "t": 0.0, "lat": 44.1, "lon": 5.2, "accuracy_m": 1.5 },
        { "t": 1.0, "lat": 44.10001, "lon": 5.20013, "accuracy_m": 1.5 }
      ]
    }
  ]
}
```

* `course.points`: `[lat, lon]` pairs in riding order (WGS-84 degrees). At least 2 points.
* Time: either `t` (seconds, any common origin) **or** `time` (ISO 8601; `Z`/offset or UTC assumed),
  used consistently in the whole document. `start_time` (optional) is shown as clock time for `t = 0`.
* `accuracy_m`: optional 1-σ horizontal position error in metres. Where it is missing, the
  *assumed accuracy* setting is used, and the rider is marked "accuracy not reported".
* `samples` may be a list of objects (row form) or an object of equal-length arrays (columnar form:
  `{"t": [...], "lat": [...], "lon": [...], "accuracy_m": [...]}`).
* Samples with `null` lat/lon are ignored with a warning. Unsorted samples are sorted, and
  duplicate timestamps are dropped. Both are reported in the data-quality table.
* At least two riders, each with at least two positioned samples. Rider ids must be unique.

Examples: [`samples/drafting_two_riders.json`](../samples/drafting_two_riders.json) (row form, ISO
times, no accuracy) and the bundled scenario
[`backend/vector_pacing/data/drafting_demo.json`](../backend/vector_pacing/data/drafting_demo.json)
(columnar form).

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

Exports (JSON and CSV) include all of this plus the settings, the illustrative-settings note,
general limitations, per-rider data quality and the disclaimer.

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
* The aerodynamic benefit of following is not estimated.
* Thresholds are configurable and the defaults are illustrative. Results depend on them.
