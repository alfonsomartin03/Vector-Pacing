# Cycling Telemetry & Race Optimization Engine

A real-time cycling telemetry, physiological modeling, and race optimization platform.

## Overview

This project aims to build a system capable of ingesting cycling telemetry data and using it to model an athlete's physiological state, simulate race scenarios, and calculate optimized pacing strategies.

The system will combine:

- Power
- Heart rate
- GPS
- Speed
- Cadence
- Elevation and gradient
- Athlete physiological parameters
- Course data

to create a real-time representation of rider state and predict how different pacing decisions affect race performance.

## Core Goals

### Telemetry Processing

Process cycling telemetry from sources such as:

- FIT files
- GPX files
- Recorded rides
- Simulated sensor streams
- Eventually live cycling sensors or cycling computer APIs

Recorded rides can be replayed as simulated live telemetry for development and testing.

### Physiological Modeling

Model athlete state using metrics such as:

- Critical Power (CP)
- W′
- W′ balance
- Energy expenditure
- Fatigue accumulation
- Recovery
- Estimated sustainable effort

Example:

    Power: 412 W
    CP: 291 W
    W′ Remaining: 7.3 / 18.7 kJ
    Gradient: 6.8%
    Estimated sustainable duration: 74 s

### Cycling Physics

Model the relationship between rider power and velocity using:

- Aerodynamic drag
- Rolling resistance
- Rider + bike mass
- Gradient
- Air density
- CdA
- Crr

Approximate power requirement:

    P = ½ρCdA·v³ + Crr·m·g·v + m·g·v·sin(θ)

### Race Simulation

Given:

- Athlete physiology
- Rider/bike characteristics
- GPX course
- Environmental conditions

simulate how different pacing strategies affect finishing time and physiological fatigue.

### Race Optimization

Determine a pacing strategy that minimizes total race time while respecting physiological constraints.

Example:

    40 km Time Trial

    Constant pacing:
    291 W
    Estimated time: 56:41

    Optimized pacing:
    Climbs:    315 W
    Flats:     287 W
    Descents:  245 W

    Estimated time: 55:47
    Improvement: 54 seconds

Potential optimization objective:

    minimize total_time

subject to constraints such as:

    W′bal(t) >= 0

and realistic limits on rider power and recovery.

## Planned Architecture

    FIT / GPX / Sensors
            │
            ▼
    Telemetry Ingestion
            │
            ▼
    Streaming Layer
       WebSockets
            │
            ▼
    Telemetry Server
            │
       ┌────┴─────┐
       ▼          ▼
    Database   Physiology Engine
                  │
                  ▼
             Race Simulator
                  │
                  ▼
             Optimizer
                  │
                  ▼
              Dashboard

## Potential Tech Stack

### Frontend

- React
- TypeScript
- D3 / Recharts
- Mapbox or Leaflet

### Backend

- Python
- FastAPI

### Data

- PostgreSQL
- TimescaleDB

### Streaming

- WebSockets
- Potential future MQTT/Kafka implementation

### Scientific Computing

- NumPy
- SciPy

### Infrastructure

- Docker
- GitHub Actions
- Cloud deployment

## Possible Repository Structure

    cycling-telemetry/
    │
    ├── apps/
    │   ├── dashboard/
    │   └── api/
    │
    ├── services/
    │   ├── telemetry/
    │   ├── physiology/
    │   └── optimizer/
    │
    ├── packages/
    │   ├── fit-parser/
    │   └── physics/
    │
    ├── simulator/
    ├── tests/
    ├── docker-compose.yml
    └── README.md

## Development Roadmap

### Phase 1 — Course Physics

- Parse GPX course data
- Calculate gradient
- Implement cycling physics model
- Predict speed from rider power
- Calculate simulated finishing time

### Phase 2 — Athlete Model

- Implement CP/W′ model
- Implement W′ balance
- Model fatigue and recovery
- Connect athlete parameters to simulation

### Phase 3 — Race Simulator

- Simulate an athlete riding an entire course
- Visualize power, speed, elevation, and W′
- Compare pacing strategies

### Phase 4 — Optimization

- Implement numerical pacing optimization
- Minimize predicted finishing time
- Apply physiological constraints
- Compare optimized vs constant-power strategies

### Phase 5 — Telemetry Engine

- Parse FIT files
- Replay recorded rides as live telemetry
- Stream data through WebSockets
- Store telemetry in a time-series database

### Phase 6 — Live Dashboard

Create a real-time interface displaying:

- Rider position
- Power
- Heart rate
- Speed
- Gradient
- W′ remaining
- Predicted sustainable effort
- Course progress

### Phase 7 — Live Hardware Integration

Explore integration with:

- Cycling power meters
- Heart-rate monitors
- Smart trainers
- Garmin devices/APIs
- Bluetooth/ANT+ sensors

## Long-Term Ideas

- Multi-rider race simulation
- Drafting detection and modeling
- Weather and wind integration
- Real-time pacing recommendations
- Race strategy comparison
- Monte Carlo race simulations
- Machine-learning fatigue models
- Automatic CdA estimation
- Team time-trial optimization
- Cloud-based telemetry processing

## Project Status

**Status:** Planning / Research

The initial development focus will be the course physics and race simulation engine before implementing real-time telemetry.

## Motivation

Cycling produces large amounts of telemetry, but most cycling software focuses on analyzing data after a ride.

This project explores a different question:

**Can real-time telemetry, physiological modeling, cycling physics, and numerical optimization be combined to determine how an athlete should race a course as fast as possible?**
