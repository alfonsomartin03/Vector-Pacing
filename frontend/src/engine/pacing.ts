// Pacing entry point: builds the same response shapes the old /api/simulate and /api/optimize returned,
// computed entirely in the browser from the course and the rider inputs.
import type { RequestParams } from '../params';
import type { OptimizeResponse, SegmentRow, SimResult, SimulateResponse } from '../types';
import { courseInfo, type CourseModel } from './course';
import { MAX_EVALUATIONS, MAX_WALL_TIME_S, METHOD, MIN_REPORTABLE_IMPROVEMENT_S, optimizePacing } from './optimize';
import { averagePower, normalizedPower, Simulator, type Environment, type Rider, type SimRun } from './simulate';
import { EngineError, round, roundAll } from './util';
import { APP_VERSION, MODEL_VERSION } from './version';

export const DISCLAIMER =
  'Model estimates only. Results come from a simplified physics and W′ model with assumed parameters; they are not validated predictions and not medical or training advice.';

export interface CourseSource {
  kind: 'demo' | 'gpx';
  filename?: string;
}

function num(group: Record<string, number>, key: string): number {
  const v = group[key];
  if (typeof v !== 'number' || !Number.isFinite(v)) throw new EngineError(`Missing or invalid input: ${key}.`);
  return v;
}

function setup(course: CourseModel, params: RequestParams) {
  const r = params.rider;
  const e = params.environment;
  const o = params.optimizer;
  const rider: Rider = {
    rider_mass_kg: num(r, 'rider_mass_kg'),
    bike_mass_kg: num(r, 'bike_mass_kg'),
    cda_m2: num(r, 'cda_m2'),
    crr: num(r, 'crr'),
    drivetrain_efficiency: num(r, 'drivetrain_efficiency'),
    cp_w: num(r, 'cp_w'),
    w_prime_j: num(r, 'w_prime_kj') * 1000,
  };
  const env: Environment = {
    air_density: num(e, 'air_density'),
    max_speed_kmh: num(e, 'max_speed_kmh'),
    coast_speed_kmh: num(e, 'coast_speed_kmh'),
  };
  if (env.coast_speed_kmh > env.max_speed_kmh) throw new EngineError('The coasting threshold must not exceed the speed limit.');
  if (rider.cp_w <= 0 || rider.w_prime_j <= 0 || rider.cda_m2 <= 0 || env.air_density <= 0) {
    throw new EngineError('CP, W′, CdA and air density must be positive.');
  }
  const settings = {
    nSegments: Math.round(num(o, 'n_segments')),
    minPowerW: Math.ceil((rider.cp_w * num(o, 'min_power_pct_cp')) / 100),
    maxPowerW: Math.floor((rider.cp_w * num(o, 'max_power_pct_cp')) / 100),
  };
  return { sim: new Simulator(course, rider, env), settings };
}

function baselineOf(params: RequestParams, sim: Simulator, s: { minPowerW: number; maxPowerW: number }) {
  if (params.baseline.mode === 'manual') {
    const p = params.baseline.power_w;
    if (typeof p !== 'number' || !Number.isFinite(p) || p <= 0) throw new EngineError('Enter a manual baseline power.');
    return { power: p, run: sim.runConstant(p), note: `Manual constant power of ${p.toFixed(0)} W.` };
  }
  const best = sim.maxFeasibleConstantPower(s.minPowerW, s.maxPowerW);
  if (best === null) {
    throw new EngineError(
      `No constant power at or above the lower bound (${s.minPowerW.toFixed(0)} W) keeps W′ above zero on this course. Lower the minimum power bound or use a manual baseline.`,
    );
  }
  const p = Math.floor(best);
  let note = `Auto: the highest whole-watt constant power that keeps W′bal ≥ 0 over the course (${p} W).`;
  if (best >= s.maxPowerW) note += " Limited by the optimizer's upper power bound.";
  return { power: p, run: sim.runConstant(p), note };
}

export function resultPayload(r: SimRun, course: CourseModel): SimResult {
  let distTime = 0;
  let coastT = 0;
  let capT = 0;
  for (let i = 0; i < r.dt.length; i++) {
    distTime += r.speed[i]! * r.dt[i]!;
    if (r.coasting[i]) coastT += r.dt[i]!;
    if (r.capped[i]) capT += r.dt[i]!;
  }
  const elapsed: number[] = [];
  let t = 0;
  for (const d of r.dt) elapsed.push((t += d));
  return {
    total_time_s: round(r.totalTime, 2),
    average_speed_kmh: round((distTime / r.totalTime) * 3.6, 2),
    average_power_w: round(averagePower(r), 1),
    normalized_power_w: round(normalizedPower(r), 1),
    min_w_bal_j: round(r.minWbal, 1),
    final_w_bal_j: round(r.wbal[r.wbal.length - 1]!, 1),
    feasible: r.feasible,
    violations: r.violations,
    coasting_fraction: round(coastT / r.totalTime, 4),
    speed_capped_fraction: round(capT / r.totalTime, 4),
    series: {
      distance_m: roundAll(course.distance_m.slice(0, -1), 1),
      elapsed_s: roundAll(elapsed, 2),
      planned_power_w: roundAll(r.planned, 1),
      power_w: roundAll(r.power, 1),
      speed_kmh: roundAll(
        Array.from(r.speed, (v) => v * 3.6),
        2,
      ),
      w_bal_j: roundAll(r.wbal, 1),
    },
  };
}

function meta(source: CourseSource, params: RequestParams, s: { minPowerW: number; maxPowerW: number }) {
  return {
    app_version: APP_VERSION,
    model_version: MODEL_VERSION,
    disclaimer: DISCLAIMER,
    inputs: { course: { kind: source.kind, filename: source.filename ?? null }, ...params },
    derived: { min_power_w: s.minPowerW, max_power_w: s.maxPowerW },
  };
}

export function simulatePacing(course: CourseModel, source: CourseSource, params: RequestParams): SimulateResponse {
  const { sim, settings } = setup(course, params);
  const b = baselineOf(params, sim, settings);
  return {
    meta: meta(source, params, settings),
    course: courseInfo(course),
    baseline: { power_w: b.power, selection: b.note, ...resultPayload(b.run, course) },
  };
}

export function optimizePacingRequest(course: CourseModel, source: CourseSource, params: RequestParams): OptimizeResponse {
  const { sim, settings } = setup(course, params);
  if (settings.minPowerW >= settings.maxPowerW) throw new EngineError('The minimum power bound must be below the maximum power bound.');
  const b = baselineOf(params, sim, settings);
  const out = optimizePacing(sim, b.run, b.power, settings);
  const opt = out.result && out.status !== 'failed' ? resultPayload(out.result, course) : null;
  const sumDt = (r: SimRun, a: number, z: number) => {
    let t = 0;
    for (let i = a; i < z; i++) t += r.dt[i]!;
    return t;
  };
  const segments: SegmentRow[] = out.segments.map((seg, k) => {
    const row: SegmentRow = {
      index: seg.index,
      start_m: round(seg.start_m, 1),
      end_m: round(seg.end_m, 1),
      mean_grade_pct: round(seg.mean_grade * 100, 2),
      baseline_power_w: b.power,
      baseline_time_s: round(sumDt(b.run, seg.start, seg.stop), 2),
    };
    if (opt && out.plan && out.result) {
      const r = out.result;
      const t = sumDt(r, seg.start, seg.stop);
      let e = 0;
      for (let i = seg.start; i < seg.stop; i++) e += r.power[i]! * r.dt[i]!;
      row.planned_power_w = out.plan[k]!;
      row.applied_avg_power_w = round(e / t, 1);
      row.optimized_time_s = round(t, 2);
    }
    return row;
  });
  return {
    meta: meta(source, params, settings),
    course: courseInfo(course),
    baseline: { power_w: b.power, selection: b.note, ...resultPayload(b.run, course) },
    optimization: {
      status: out.status,
      message: out.message,
      notes: out.notes,
      time_saved_s: opt && out.result ? round(b.run.totalTime - out.result.totalTime, 2) : null,
      solver: {
        method: METHOD,
        success: out.converged,
        message: out.solverMessage,
        iterations: out.iterations,
        evaluations: out.evaluations,
        elapsed_s: round(out.elapsedS, 3),
        max_evaluations: MAX_EVALUATIONS,
        max_wall_time_s: MAX_WALL_TIME_S,
        min_reportable_improvement_s: MIN_REPORTABLE_IMPROVEMENT_S,
      },
      segments,
      result: opt,
    },
  };
}
