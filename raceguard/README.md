# ITT Draft Detection System

A telemetry-based race-integrity analysis system for identifying sections of individual time trials that may warrant review for possible drafting.

## Overview

Individual time trials rely on riders maintaining required separation distances, but race officials cannot continuously observe every competitor across an entire course.

This project explores whether rider telemetry can help officials identify portions of a race that are statistically or physically consistent with possible drafting.

The system does **not** automatically determine whether a rider committed a drafting violation.

Instead, it acts as a screening system:

    Race Telemetry
          ↓
    Automated Analysis
          ↓
    Suspicious Segment Detection
          ↓
    Evidence / Confidence Report
          ↓
    Commissaire Review

The goal is to help officials determine **where to look**, not replace human judgment.

## Input Data

Initial versions would analyze FIT files containing:

- GPS coordinates
- Timestamp
- Speed
- Power
- Cadence
- Heart rate
- Elevation

Additional optional information could include:

- Rider mass
- Bike mass
- CdA
- Crr
- Weather
- Wind direction
- Wind speed
- Course data
- Rider start time

Analyzing multiple riders from the same race would substantially improve detection capability.

## Detection Concept

Drafting reduces the aerodynamic power required to maintain a given velocity.

The system can search for periods where observed rider behavior differs significantly from what would normally be expected from:

    Power
    Speed
    Gradient
    Wind
    Rider characteristics

Example:

    Segment: 31:42–32:17

    Duration:             35 s
    Average speed:        47.8 km/h
    Average power:        238 W
    Expected solo power:  327 W

    Estimated discrepancy: -89 W

    Classification:
    Segment warrants review

This would NOT automatically mean drafting occurred.

Possible alternative explanations could include:

- Tailwind
- GPS error
- Incorrect CdA estimate
- Road surface
- Sensor error
- Vehicle interference
- Incorrect elevation data

## Multi-Rider Detection

When multiple competitors provide telemetry, the system becomes much more powerful.

Rider trajectories can be reconstructed using:

    latitude
    longitude
    timestamp

Start-time differences can then be normalized into absolute race time.

The system could search for periods where:

    Rider A
       ↓
    < proximity threshold
       ↓
    Rider B

for an extended duration.

Potential evidence:

    Rider separation
    Relative velocity
    Duration
    Power reduction
    Speed similarity
    Course position

## Detection Pipeline

    FIT Files
        │
        ▼
    FIT Parser
        │
        ▼
    Data Cleaning
        │
        ▼
    Timestamp Alignment
        │
        ▼
    GPS Trajectory Reconstruction
        │
        ▼
    Rider Proximity Analysis
        │
        ├─────────────┐
        ▼             ▼
    Physics Model   Power Analysis
        │             │
        └──────┬──────┘
               ▼
        Anomaly Detection
               │
               ▼
        Suspicious Segments
               │
               ▼
        Commissaire Dashboard

## Physics Model

Expected solo power can be approximated using:

    P =
    ½ρCdA·v³
    + Crr·m·g·v
    + m·g·v·sin(θ)

Additional models can account for:

- Headwind
- Tailwind
- Crosswind
- Acceleration
- Drivetrain losses

The difference between measured power and estimated solo power can then be examined.

    ΔP = P_measured - P_expected

Large sustained discrepancies could become one component of the detection model.

## Suspicion Model

The system should combine multiple signals rather than using a single threshold.

Example:

    Draft Evidence Score

    GPS proximity        ────────
    Duration             ───────
    Power discrepancy    ──────
    Speed correlation    ────────
    Wind confidence      ─────
    Sensor confidence    ───────

                        ↓

                 Review Priority

Importantly, the system should expose the evidence contributing to each flag.

## Example Output

    POSSIBLE DRAFTING SEGMENT

    Rider: #142
    Time: 31:42–32:17
    Duration: 35 s
    Course position: 23.7–24.2 km

    Nearest competitor:
    Rider #138

    Estimated separation:
    7–11 m

    Speed correlation:
    0.94

    Average power:
    238 W

    Expected solo power:
    327 W

    Confidence in telemetry:
    High

    Recommendation:
    Review race footage / official observations for this segment.

## Commissaire Dashboard

The interface could display:

    ┌──────────────────────────────┐
    │       COURSE MAP             │
    │                              │
    │      ───────⚠──────          │
    │                              │
    └──────────────────────────────┘

    Rider #142

    Power     ─────────╲____────
    Speed     ──────────────────
    Distance  ──────⚠───────────

Flags could be clicked to inspect the underlying telemetry.

## Potential Tech Stack

### Analysis

- Python
- Pandas
- NumPy
- SciPy

### FIT Processing

- fitparse
- Garmin FIT SDK

### Geospatial Analysis

- GeoPandas
- Shapely
- PostGIS

### Backend

- FastAPI

### Database

- PostgreSQL
- PostGIS

### Frontend

- React
- TypeScript
- Mapbox / Leaflet
- D3 / Recharts

## Development Roadmap

### Phase 1 — FIT Processing

- Upload FIT file
- Extract telemetry
- Clean GPS data
- Visualize route
- Display power/speed/elevation

### Phase 2 — Cycling Physics

- Calculate expected power
- Account for gradient
- Account for aerodynamic drag
- Compare expected vs measured power

### Phase 3 — Anomaly Detection

Identify periods where:

    observed power << expected solo power

while maintaining unusually high velocity.

### Phase 4 — Multi-Rider Analysis

- Upload entire race field
- Align timestamps
- Reconstruct rider positions
- Calculate rider-to-rider proximity
- Detect prolonged proximity

### Phase 5 — Evidence Scoring

Combine:

- Distance
- Duration
- Speed correlation
- Power discrepancy
- Course geometry
- Environmental uncertainty

into a review-priority model.

### Phase 6 — Commissaire Dashboard

Build:

- Interactive course map
- Rider timeline
- Flagged segments
- Telemetry graphs
- Rider comparisons
- Evidence explanations

### Phase 7 — Validation

Create controlled experiments using:

    Solo riding
    Legal passing
    Intentional drafting
    Vehicle following
    Tailwind riding
    GPS degradation

Measure:

- False-positive rate
- False-negative rate
- Detection precision
- Detection recall

## Important Design Principle

The software should never claim:

    "Rider X cheated."

Instead:

    "Telemetry between 31:42 and 32:17 contains characteristics consistent with possible drafting and warrants review."

Race penalties should remain decisions made by officials using all available evidence.

## Long-Term Ideas

- Real-time race monitoring
- Live GPS trackers
- Automated rider-pair detection
- Video synchronization
- Motorcycle/vehicle detection
- Weather-station integration
- Wind-field modeling
- Machine-learning classification
- Race-wide drafting heatmaps
- Automatic pass detection
- Team time-trial analysis
- Integration with timing systems

## Research Questions

This project could investigate:

1. How accurately can consumer GPS estimate rider separation?
2. Can power and speed data distinguish drafting from environmental effects?
3. How long must two rider trajectories overlap before drafting becomes statistically distinguishable from a legal pass?
4. Can multiple telemetry signals reduce false positives?
5. How reliably can drafting be detected without telemetry from the rider being followed?

## Project Status

**Status:** Foundational backbone implemented; working prototype in active development.

The core package establishes typed telemetry, rider, evidence, and analysis models plus
an auditable cycling-physics function. The implementation intentionally keeps screening
results separate from adjudication: every result is phrased as a review recommendation.

### Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
```

## Working Prototype

RaceGuard accepts CSV and FIT activities and returns locations for an official to review.
The current detector has two evidence paths:

1. **Within-rider power comparison.** A centered seven-second window smooths speed, power,
   grade, and acceleration. Coasting, soft pedaling, cadence dropouts, and nearby transitions
   are excluded. Stable ten-second sections, sampled every five seconds so events between
   fixed boundaries are not diluted, are compared with sections from the same rider
   at similar grade, acceleration, and travel direction, at least 30 seconds apart. Matching
   speed is preferred. If speed differs by at most 3.5 m/s, a speed-cubed aerodynamic
   adjustment is used with a higher threshold. The median of at least two reference sections
   must exceed the candidate by at least 45 W and 15%, or 60 W and 20% for speed-normalized
   comparisons. The difference must also exceed 2.5 times a robust reference spread and
   persist for at least 15 seconds. No generic rider CdA can create a single-rider flag.
2. **Trailing-rider GPS.** When multiple riders are uploaded, synchronized samples are checked
   for a rider moving behind another in the same direction and within a narrow lateral band.
   The same ahead/behind pairing must remain close for more than ten seconds before it counts.
   Sustained trailing can produce a review flag even without power. Reports label both riders
   and show each rider's speed and power alongside their mean separation. A nearby rider alongside
   or behind the target is not treated as a drafting leader.

FIT files commonly provide timestamps, GPS, speed, distance, elevation, power, and cadence,
but fields can be absent or recorded irregularly. When explicit gradient is unavailable,
RaceGuard estimates it from elevation change over at least 20 meters. [Garmin's FIT Activity
specification](https://developer.garmin.com/fit/articles/file-types/activity.html) describes
the available record fields and irregular sampling. The power adjustment uses the cycling
force terms validated by [Martin et al.](https://pubmed.ncbi.nlm.nih.gov/28121252/).
The aerodynamic plausibility check is informed by [wind-tunnel and simulation work on
drafting](https://link.springer.com/article/10.1007/s12283-021-00345-2).

This is a screening heuristic, not a calibrated probability or a determination of drafting.
Wind speed and yaw, changes in rider position, road surface, and sensor error are not resolved
by an ordinary FIT file. A short activity with no comparable riding sections produces no
single-rider power flag. Thresholds need validation against labeled solo and drafting rides
before operational use; see [field aerodynamics research](https://www.jsc-journal.com/index.php/JSC/article/view/168)
for why missing wind measurements matter.

### Analyze from the command line

The included sample contains two nearby riders and can be run without third-party packages:

```bash
PYTHONPATH=src python -m raceguard.cli examples/sample_race.csv
PYTHONPATH=src python -m raceguard.cli examples/sample_race.csv --json --output report.json
```

CSV columns are `rider_id`, ISO-8601 `timestamp`, `latitude`, `longitude`, and either
`speed_mps` or `speed_kph`. Optional columns are `power_w`, `elevation_m`, `cadence_rpm`,
`heart_rate_bpm`, `distance_m`, and decimal `gradient` (for example, `0.05` for 5%).

### Run the review console

```bash
python -m pip install -e .
uvicorn raceguard.api:app --reload
```

Open `http://127.0.0.1:8000`, upload telemetry, and inspect the activity assessment,
evidence score, review locations, and contributing evidence. Up to 20 CSV/FIT activities
can be selected in one submission and are analyzed together. CSV files can contain one or
more riders; each FIT file receives an editable rider label defaulted from its filename.
Review locations are returned chronologically so an official can follow the activity timeline.

### Deploy to Vercel

The FastAPI entrypoint and runtime dependencies are declared in `pyproject.toml`. Connect the
GitHub repository to Vercel with the repository root as the Root Directory, leave the Framework
Preset on automatic detection, and do not set a custom Build Command or Output Directory. Vercel
will serve the FastAPI application, including the dashboard at `/` and analysis endpoint at
`/api/analyze`.
Uploaded files are processed locally in a temporary file and are deleted after analysis.
Interactive API documentation is available at `http://127.0.0.1:8000/docs`.

FIT input accepts a rider identifier when using the command line:

```bash
raceguard activity.fit --rider-id 142
```

### Prototype limitations

- Multi-rider proximity allows samples up to two seconds apart, but recording clocks must
  represent the same real-world time.
- Wind speed and direction are not inferred from FIT data. Weather data can be integrated
  later using each segment's coordinates and timestamp.
- Segment coordinates and timestamps are included in the result contract so a future weather
  provider can supply local wind speed and direction without changing the upload workflow.
- Consumer GPS uncertainty can be similar to the distances under review.
- Scores are heuristic review priorities and require validation against controlled trials.
- Data is processed in memory; persistence, authentication, and race administration are not
  part of this local prototype.

## Motivation

Race officials cannot physically observe every rider throughout an individual time trial.

This project explores whether telemetry can act as an additional screening tool:

**Can GPS, power, speed, course, and environmental data automatically identify sections of an individual time trial that warrant human review for possible drafting?**
