// Quasi-steady-state physics, CP/W′ balance and course simulation.
// TypeScript port of backend/vector_pacing/{physics,athlete,simulate}.py with the same rules:
//
//   η·P = v·(½ρ·CdA·v² + m·g·Crr·cosθ + m·g·sinθ)      (no wind, no inertia)
//
// solved per step for the non-negative root of a·v³ + b·v − η·P = 0 (Cardano + 2 Newton steps).
// W′ balance uses the Skiba differential model, integrated exactly per step, and is never clamped.
// Per-step rules: coast (0 W) when gravity alone reaches the coasting threshold; cap speed at the
// limit (reducing applied power); speed < 1 m/s is a stall; a W′ balance below 0 is infeasible.
import type { CourseModel } from './course';
import { EngineError } from './util';

export const G = 9.80665;
const RESIDUAL_TOLERANCE_W = 1e-6;
export const STALL_SPEED_MS = 1.0;
export const FEASIBILITY_TOLERANCE_J = 1e-6;

export interface Rider {
  rider_mass_kg: number;
  bike_mass_kg: number;
  cda_m2: number;
  crr: number;
  drivetrain_efficiency: number;
  cp_w: number;
  w_prime_j: number;
}

export interface Environment {
  air_density: number;
  max_speed_kmh: number;
  coast_speed_kmh: number;
}

export const DEFAULT_RIDER: Rider = {
  rider_mass_kg: 72,
  bike_mass_kg: 8.5,
  cda_m2: 0.28,
  crr: 0.004,
  drivetrain_efficiency: 0.975,
  cp_w: 280,
  w_prime_j: 20_000,
};

export const DEFAULT_ENV: Environment = { air_density: 1.225, max_speed_kmh: 80, coast_speed_kmh: 60 };

/** Steady speed [m/s] for wheel power `pw` ≥ 0 with resistance a·v³ + b·v. Throws on a bad root. */
export function solveSpeed(pw: number, a: number, b: number): number {
  if (!(a > 0) || !Number.isFinite(a)) throw new EngineError('Aerodynamic coefficient must be positive.');
  if (!(pw >= 0) || !Number.isFinite(pw)) throw new EngineError('Wheel power must be finite and non-negative.');
  const p = b / a;
  const q = -pw / a;
  const disc = (q / 2) ** 2 + (p / 3) ** 3;
  let v: number;
  if (disc >= 0) {
    const s = Math.sqrt(disc);
    v = Math.cbrt(-q / 2 + s) + Math.cbrt(-q / 2 - s);
  } else {
    // Three real roots (only on descents, p < 0): take the largest.
    const arg = Math.min(1, Math.max(-1, ((3 * q) / (2 * p)) * Math.sqrt(-3 / p)));
    v = 2 * Math.sqrt(-p / 3) * Math.cos(Math.acos(arg) / 3);
  }
  v = Math.max(0, v);
  for (let k = 0; k < 2; k++) {
    const f = a * v ** 3 + b * v - pw;
    const df = 3 * a * v * v + b;
    if (df > 1e-12 && v > 0) v = Math.max(0, v - f / df);
  }
  const residual = Math.abs(a * v ** 3 + b * v - pw);
  if (!Number.isFinite(v) || residual > RESIDUAL_TOLERANCE_W * Math.max(1, pw)) {
    throw new EngineError(`Speed solver failed to converge (residual ${residual.toPrecision(3)} W).`);
  }
  return v;
}

/** W′ balance [J] at the end of each step, starting full. */
export function wPrimeBalance(power: ArrayLike<number>, dt: ArrayLike<number>, cp: number, wPrime: number): Float64Array {
  if (!(cp > 0) || !(wPrime > 0)) throw new EngineError('CP and W′ must be positive.');
  const out = new Float64Array(power.length);
  let w = wPrime;
  const inv = 1 / wPrime;
  for (let i = 0; i < power.length; i++) {
    const p = power[i]!;
    if (p > cp) w -= (p - cp) * dt[i]!;
    else w = wPrime - (wPrime - w) * Math.exp(-(cp - p) * dt[i]! * inv);
    out[i] = w;
  }
  return out;
}

export interface SimRun {
  planned: Float64Array;
  power: Float64Array; // applied
  speed: Float64Array; // m/s
  dt: Float64Array;
  wbal: Float64Array;
  coasting: Uint8Array;
  capped: Uint8Array;
  totalTime: number;
  minWbal: number;
  feasible: boolean;
  violations: string[];
}

export class Simulator {
  readonly n: number;
  readonly a: number;
  readonly b: Float64Array;
  readonly ds: Float64Array;
  readonly vTerm: Float64Array;
  readonly coasting: Uint8Array;
  readonly pHold: Float64Array;
  readonly vMax: number;

  constructor(
    readonly course: CourseModel,
    readonly rider: Rider,
    readonly env: Environment,
  ) {
    const n = course.grade.length;
    this.n = n;
    const m = rider.rider_mass_kg + rider.bike_mass_kg;
    this.a = 0.5 * env.air_density * rider.cda_m2;
    this.b = new Float64Array(n);
    this.ds = new Float64Array(n);
    this.vTerm = new Float64Array(n);
    this.coasting = new Uint8Array(n);
    this.pHold = new Float64Array(n);
    this.vMax = env.max_speed_kmh / 3.6;
    const vCoast = env.coast_speed_kmh / 3.6;
    for (let i = 0; i < n; i++) {
      const th = Math.atan(course.grade[i]!);
      const b = m * G * (rider.crr * Math.cos(th) + Math.sin(th));
      this.b[i] = b;
      this.ds[i] = course.distance_m[i + 1]! - course.distance_m[i]!;
      this.vTerm[i] = Math.sqrt(Math.max(0, -b / this.a));
      this.coasting[i] = this.vTerm[i]! >= vCoast ? 1 : 0;
      const hold = this.a * this.vMax ** 3 + b * this.vMax;
      this.pHold[i] = Math.max(0, hold / rider.drivetrain_efficiency);
    }
  }

  run(plan: ArrayLike<number>): SimRun {
    const n = this.n;
    if (plan.length !== n) throw new EngineError('Power plan length does not match the course.');
    const eta = this.rider.drivetrain_efficiency;
    const planned = Float64Array.from(plan);
    const power = new Float64Array(n);
    const speed = new Float64Array(n);
    const dt = new Float64Array(n);
    const capped = new Uint8Array(n);
    let stallAt = -1;
    for (let i = 0; i < n; i++) {
      const pl = planned[i]!;
      if (!Number.isFinite(pl) || pl < 0) throw new EngineError('Planned power must be finite and non-negative.');
      let applied = this.coasting[i] ? 0 : pl;
      let v: number;
      if (this.coasting[i]) v = this.vTerm[i]!;
      else {
        try {
          v = solveSpeed(applied * eta, this.a, this.b[i]!);
        } catch (e) {
          throw new EngineError(`The physics solver failed at step ${i}: ${(e as Error).message}`);
        }
      }
      if (v > this.vMax) {
        v = this.vMax;
        capped[i] = 1;
        applied = Math.min(applied, this.pHold[i]!);
      }
      if (v < STALL_SPEED_MS && stallAt < 0) stallAt = i;
      power[i] = applied;
      speed[i] = v;
      dt[i] = this.ds[i]! / Math.max(v, 1e-3);
    }
    let totalTime = 0;
    for (let i = 0; i < n; i++) totalTime += dt[i]!;
    if (!Number.isFinite(totalTime)) throw new EngineError('The simulation produced a non-finite step time.');

    const violations: string[] = [];
    if (stallAt >= 0) {
      violations.push(
        `Speed drops below ${(STALL_SPEED_MS * 3.6).toFixed(1)} km/h at km ${(this.course.distance_m[stallAt]! / 1000).toFixed(2)}; the rider would stall at this power.`,
      );
    }
    const wbal = wPrimeBalance(power, dt, this.rider.cp_w, this.rider.w_prime_j);
    let minWbal = Infinity;
    let firstNeg = -1;
    for (let i = 0; i < n; i++) {
      if (wbal[i]! < minWbal) minWbal = wbal[i]!;
      if (firstNeg < 0 && wbal[i]! < -FEASIBILITY_TOLERANCE_J) firstNeg = i;
    }
    if (firstNeg >= 0) {
      violations.push(
        `W′ balance is exhausted at km ${(this.course.distance_m[firstNeg + 1]! / 1000).toFixed(2)} and reaches ${(minWbal / 1000).toFixed(2)} kJ; this effort exceeds the modelled capacity.`,
      );
    }
    return {
      planned,
      power,
      speed,
      dt,
      wbal,
      coasting: this.coasting.slice(),
      capped,
      totalTime,
      minWbal,
      feasible: violations.length === 0,
      violations,
    };
  }

  runConstant(p: number): SimRun {
    return this.run(new Float64Array(this.n).fill(p));
  }

  /** Highest constant power in [lo, hi] that stays feasible (bisection), or null if `lo` is infeasible. */
  maxFeasibleConstantPower(lo: number, hi: number, tol = 0.05): number | null {
    if (!this.runConstant(lo).feasible) return null;
    if (this.runConstant(hi).feasible) return hi;
    for (let k = 0; k < 60 && hi - lo > tol; k++) {
      const mid = 0.5 * (lo + hi);
      if (this.runConstant(mid).feasible) lo = mid;
      else hi = mid;
    }
    return lo;
  }
}

export function averagePower(r: SimRun): number {
  let e = 0;
  for (let i = 0; i < r.power.length; i++) e += r.power[i]! * r.dt[i]!;
  return e / r.totalTime;
}

/** Approximate normalized power: 4th-power mean of the 30 s rolling mean of 1 Hz power. */
export function normalizedPower(r: SimRun): number {
  const n = r.power.length;
  const tEnd = r.totalTime;
  const samples = Math.ceil(tEnd);
  if (samples < 30) return averagePower(r);
  const p1 = new Float64Array(samples);
  let j = 0;
  let edge = r.dt[0]!; // end time of step j
  for (let s = 0; s < samples; s++) {
    while (j < n - 1 && edge <= s) {
      j++;
      edge += r.dt[j]!;
    }
    p1[s] = r.power[j]!;
  }
  let sum = 0;
  for (let s = 0; s < 30; s++) sum += p1[s]!;
  let acc = 0;
  let count = 0;
  for (let s = 30; ; s++) {
    acc += (sum / 30) ** 4;
    count++;
    if (s >= samples) break;
    sum += p1[s]! - p1[s - 30]!;
  }
  return (acc / count) ** 0.25;
}
