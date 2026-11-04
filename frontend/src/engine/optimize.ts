// Browser pacing heuristic: a bounded two-stage local search over terrain-aware segments.
//
// This is NOT the SciPy SLSQP optimizer of the Python reference, and it does not claim to find the same
// plan. Every candidate plan is evaluated with the same simulator used for the baseline.
//
//  1. Split the course into N terrain-aware segments (same Douglas–Peucker-style split as the reference)
//     and start from the constant-power baseline.
//  2. Seed: try "push climbs, ease descents" plans P_k = clip(c + α·grade_k, min, max) for a few slopes α;
//     for each α, bisect c to the highest level that stays feasible. Keep the fastest if it beats the baseline.
//  3. Refine: each iteration probes +E and −E joules on every segment (a long segment gets a small power
//     change, a short one a larger change). It then tries
//       - a pure increase on one segment if it stays feasible (spare W′), otherwise
//       - transfers: the increase on segment i with a cut on segment j sized so that the W′ balance,
//         predicted step by step from the two probes, stays above the floor.
//     The most promising candidates are fully simulated; the fastest feasible one is kept. When nothing
//     helps, E is halved; the search stops below MIN_MOVE_J or at the evaluation/time budget.
//  4. The plan is rounded down to whole watts and re-simulated; only that verified result is reported,
//     with the same "improved / no meaningful improvement / failed" rules as the reference.
//
// Feasible means: power within the bounds and W′ balance ≥ a small reserve at every step. Like any local
// search, it can stop at a plan that is not the global optimum.
import type { SimRun, Simulator } from './simulate';
import { now } from './util';

export const MIN_REPORTABLE_IMPROVEMENT_S = 0.5;
export const MAX_EVALUATIONS = 4000;
export const MAX_WALL_TIME_S = 15;
const RESERVE_FRACTION = 0.002;
const START_STEP_FRACTION_CP = 0.1;
const PAIR_CANDIDATES = 12;
const MAX_CUT_SCALE = 4;
const MIN_MOVE_J = 200; // stop when no move of this much energy (≈1 W for ~3 min) helps
const SEED_SLOPES = 12; // α = 1/12 … 12/12 of SEED_MAX_SLOPE_PCT_CP
const SEED_MAX_SLOPE_PCT_CP = 24; // up to 24 % of CP per 1 % of grade
const SEED_BISECTIONS = 20;
export const METHOD = 'Bounded pairwise power-transfer search (browser heuristic)';

export type Status = 'improved' | 'no_improvement' | 'failed';

export interface OptimizerSettings {
  nSegments: number;
  minPowerW: number;
  maxPowerW: number;
  maxIterations?: number;
}

export interface Segment {
  index: number;
  start: number; // step-edge index
  stop: number; // step-edge index (exclusive step)
  start_m: number;
  end_m: number;
  mean_grade: number;
}

export interface OptimizationOutcome {
  status: Status;
  message: string;
  segments: Segment[];
  plan: number[] | null;
  result: SimRun | null;
  converged: boolean;
  solverMessage: string;
  iterations: number;
  evaluations: number;
  elapsedS: number;
  notes: string[];
}

/** Segment boundaries (step-edge indices) where the elevation profile bends most. */
export function terrainBoundaries(elevation: number[], nSegments: number, minSteps: number): number[] {
  let edges = [0, elevation.length - 1];
  while (edges.length - 1 < nSegments) {
    let best: [number, number] | null = null;
    for (let s = 0; s < edges.length - 1; s++) {
      const a = edges[s]!;
      const b = edges[s + 1]!;
      if (b - a < 2 * minSteps) continue;
      for (let i = a + minSteps; i <= b - minSteps; i++) {
        const chord = elevation[a]! + ((elevation[b]! - elevation[a]!) * (i - a)) / (b - a);
        const dev = Math.abs(elevation[i]! - chord);
        if (best === null || dev > best[0]) best = [dev, i];
      }
    }
    if (best === null) break;
    if (best[0] < 1.0) {
      // Flat: halve the longest splittable segment.
      let pick: [number, number] | null = null;
      for (let s = 0; s < edges.length - 1; s++) {
        const a = edges[s]!;
        const b = edges[s + 1]!;
        if (b - a >= 2 * minSteps && (pick === null || b - a > pick[1] - pick[0])) pick = [a, b];
      }
      best = [0, Math.floor((pick![0] + pick![1]) / 2)];
    }
    edges = [...edges, best[1]].sort((x, y) => x - y);
  }
  return edges;
}

export function makeSegments(sim: Simulator, nSegments: number): Segment[] {
  const nSteps = sim.n;
  const n = Math.max(1, Math.min(nSegments, nSteps));
  const minSteps = Math.max(1, Math.floor(nSteps / (3 * n)));
  const edges = terrainBoundaries(sim.course.elevation_m, n, minSteps);
  const d = sim.course.distance_m;
  const e = sim.course.elevation_m;
  return edges.slice(0, -1).map((start, k) => {
    const stop = edges[k + 1]!;
    return { index: k, start, stop, start_m: d[start]!, end_m: d[stop]!, mean_grade: (e[stop]! - e[start]!) / (d[stop]! - d[start]!) };
  });
}

export function expandPlan(segments: Segment[], segPower: number[], nSteps: number): Float64Array {
  const out = new Float64Array(nSteps);
  segments.forEach((s, k) => out.fill(segPower[k]!, s.start, s.stop));
  return out;
}

class BudgetExceeded extends Error {}

const diff = (a: Float64Array, b: Float64Array) => a.map((v, i) => v - b[i]!);

/**
 * Smallest multiple s of the down-probe that keeps base + up + s·down ≥ floor at every step
 * (null if impossible within MAX_CUT_SCALE probes).
 */
export function cutScale(base: Float64Array, up: Float64Array, down: Float64Array, floor: number): number | null {
  let s = 0;
  for (let i = 0; i < base.length; i++) {
    const short = floor - base[i]! - up[i]!;
    if (short <= 0) continue;
    if (!(down[i]! > 1e-9)) return null;
    s = Math.max(s, short / down[i]!);
    if (s > MAX_CUT_SCALE) return null;
  }
  return s;
}

export function optimizePacing(
  sim: Simulator,
  baseline: SimRun,
  baselinePower: number,
  settings: OptimizerSettings,
  budget: { maxEvaluations?: number; maxWallTimeS?: number } = {},
): OptimizationOutcome {
  const t0 = now();
  const maxEval = budget.maxEvaluations ?? MAX_EVALUATIONS;
  const maxWall = budget.maxWallTimeS ?? MAX_WALL_TIME_S;
  const maxIter = settings.maxIterations ?? 500;
  const segments = makeSegments(sim, settings.nSegments);
  const cp = sim.rider.cp_w;
  const reserve = RESERVE_FRACTION * sim.rider.w_prime_j;
  const lo = settings.minPowerW;
  const hi = settings.maxPowerW;
  const elapsed = () => (now() - t0) / 1000;
  const base = (status: Status, message: string): OptimizationOutcome => ({
    status,
    message,
    segments,
    plan: null,
    result: null,
    converged: false,
    solverMessage: '',
    iterations: 0,
    evaluations: 0,
    elapsedS: elapsed(),
    notes: [],
  });

  if (!baseline.feasible) {
    return base(
      'failed',
      'The baseline is infeasible, so there is no feasible starting point to optimize from. Lower the manual baseline power or use the Auto baseline.',
    );
  }
  if (!(lo <= baselinePower && baselinePower <= hi)) {
    return base(
      'failed',
      `The baseline power (${baselinePower.toFixed(0)} W) is outside the optimizer power bounds (${lo.toFixed(0)}–${hi.toFixed(0)} W). Widen the bounds or change the baseline.`,
    );
  }

  let evals = 0;
  const evaluate = (x: number[]): SimRun => {
    if (evals >= maxEval || elapsed() > maxWall) throw new BudgetExceeded();
    evals++;
    return sim.run(expandPlan(segments, x, sim.n));
  };
  const segTime = (r: SimRun, s: Segment) => {
    let t = 0;
    for (let i = s.start; i < s.stop; i++) t += r.dt[i]!;
    return t;
  };

  const nSeg = segments.length;
  let x = new Array<number>(nSeg).fill(baselinePower);
  let cur = baseline;
  // Accepted plans keep W′bal ≥ a small reserve (or ≥ the baseline's own minimum if that is lower).
  const floor = Math.min(reserve, baseline.minWbal);
  const accept = (r: SimRun) => r.feasible && r.minWbal >= floor;

  let iterations = 0;
  let converged = false;
  let solverMessage = '';
  try {
    // Stage 1: grade-proportional seeds P_k = clip(c + α·grade_k%, lo, hi). For each slope α the offset c
    // is bisected to the highest level that keeps W′ feasible (W′ use grows monotonically with c).
    const gradePct = segments.map((sg) => sg.mean_grade * 100);
    const gMin = Math.min(...gradePct);
    const gMax = Math.max(...gradePct);
    const planFor = (c: number, alpha: number) => gradePct.map((g) => Math.min(hi, Math.max(lo, c + alpha * g)));
    for (let a = 1; a <= SEED_SLOPES; a++) {
      const alpha = (a * SEED_MAX_SLOPE_PCT_CP * cp) / 100 / SEED_SLOPES;
      let cLo = lo - alpha * gMax;
      let cHi = hi - alpha * gMin;
      if (!accept(evaluate(planFor(cLo, alpha)))) continue;
      for (let k = 0; k < SEED_BISECTIONS && cHi - cLo > 0.5; k++) {
        const mid = 0.5 * (cLo + cHi);
        if (accept(evaluate(planFor(mid, alpha)))) cLo = mid;
        else cHi = mid;
      }
      const xs = planFor(cLo, alpha);
      const r = evaluate(xs);
      if (accept(r) && r.totalTime < cur.totalTime) {
        x = xs;
        cur = r;
      }
    }

    // Stage 2: pairwise refinement. Moves are sized in energy (joules), so a long segment gets a small
    // power change and a short one a larger change for the same W′ cost.
    let energy = Math.max(4, START_STEP_FRACTION_CP * cp) * (cur.totalTime / nSeg);
    while (iterations < maxIter) {
      iterations++;
      const T = cur.totalTime;
      const tSeg = segments.map((sg) => Math.max(segTime(cur, sg), 1e-9));
      const ups: { k: number; dp: number; gain: number; dw: Float64Array; r: SimRun }[] = [];
      const downs: { k: number; dp: number; loss: number; dw: Float64Array }[] = [];
      for (let k = 0; k < nSeg; k++) {
        const up = Math.min(hi - x[k]!, energy / tSeg[k]!);
        if (up > 0.5) {
          const xu = x.slice();
          xu[k] = x[k]! + up;
          const r = evaluate(xu);
          ups.push({ k, dp: up, gain: T - r.totalTime, dw: diff(r.wbal, cur.wbal), r });
        }
        const dn = Math.min(x[k]! - lo, energy / tSeg[k]!);
        if (dn > 0.5) {
          const xd = x.slice();
          xd[k] = x[k]! - dn;
          const r = evaluate(xd);
          downs.push({ k, dp: dn, loss: r.totalTime - T, dw: diff(r.wbal, cur.wbal) });
        }
      }
      let moved = false;
      // 1) Pure increases that stay feasible (spare W′).
      const pure = ups.filter((u) => u.gain > 1e-6 && accept(u.r)).sort((p, q) => q.gain - p.gain)[0];
      if (pure) {
        x[pure.k] = x[pure.k]! + pure.dp;
        cur = pure.r;
        moved = true;
      } else {
        // 2) Transfers: the up-probe on i paired with a cut on j. The cut is sized so that the W′ balance,
        //    predicted step by step from the two probes (linear superposition), stays above the floor
        //    everywhere. Candidates are ranked by predicted net time gain; the best are fully simulated
        //    and the fastest feasible one is kept.
        const cand: { i: number; up: number; j: number; dj: number; score: number }[] = [];
        for (const u of ups) {
          if (u.gain <= 0) continue;
          for (const d of downs) {
            if (d.k === u.k) continue;
            const scale = cutScale(cur.wbal, u.dw, d.dw, floor);
            if (scale === null) continue;
            const dj = Math.min(x[d.k]! - lo, Math.max(scale, 0.25) * d.dp);
            if (!(dj > 0)) continue;
            const score = u.gain - d.loss * (dj / d.dp);
            if (score > 1e-6) cand.push({ i: u.k, up: u.dp, j: d.k, dj, score });
          }
        }
        cand.sort((p, q) => q.score - p.score);
        let best: { x: number[]; r: SimRun } | null = null;
        for (const c of cand.slice(0, PAIR_CANDIDATES)) {
          const xn = x.slice();
          xn[c.i] = Math.min(hi, xn[c.i]! + c.up);
          xn[c.j] = Math.max(lo, xn[c.j]! - c.dj);
          const r = evaluate(xn);
          if (r.totalTime < (best?.r.totalTime ?? T - 1e-6) && accept(r)) best = { x: xn, r };
        }
        if (best) {
          x = best.x;
          cur = best.r;
          moved = true;
        }
      }
      if (!moved) {
        energy /= 2;
        if (energy < MIN_MOVE_J) {
          converged = true;
          solverMessage = 'No improving move was found at the smallest step size (local stopping point).';
          break;
        }
      }
    }
    if (!converged && !solverMessage) solverMessage = `Stopped at the iteration limit (${maxIter}).`;
  } catch (e) {
    if (!(e instanceof BudgetExceeded)) throw e;
    solverMessage = `Stopped at the computation budget (${maxEval} simulations / ${maxWall} s).`;
  }

  const notes = [
    'Browser heuristic: a bounded local search that moves power between terrain segments, checking every move with the full simulator. It is a simplified stand-in for the Python reference optimizer (SLSQP), not equivalent to it, and the plan is not guaranteed to be optimal.',
    'The plan respects the power bounds and keeps W′ balance at or above zero at every step under this model.',
  ];
  if (!converged) notes.push(`The search did not reach its stopping criterion (${solverMessage}); the best plan found so far is used.`);

  const plan = x.map((p) => Math.min(Math.floor(hi), Math.max(Math.ceil(lo), Math.floor(p + 1e-9))));
  const verified = sim.run(expandPlan(segments, plan, sim.n));
  const common = { segments, plan, result: verified, converged, solverMessage, iterations, evaluations: evals, elapsedS: elapsed(), notes };
  if (!verified.feasible) {
    return {
      ...common,
      status: 'failed',
      plan: null,
      message: 'The optimized plan failed independent verification (it violates the W′ model), so it is not reported.',
    };
  }
  const saving = baseline.totalTime - verified.totalTime;
  if (saving < MIN_REPORTABLE_IMPROVEMENT_S) {
    return {
      ...common,
      status: 'no_improvement',
      message: `No meaningful improvement was found (verified difference ${saving >= 0 ? '+' : ''}${saving.toFixed(1)} s, threshold ${MIN_REPORTABLE_IMPROVEMENT_S} s). The constant-power baseline is recommended.`,
    };
  }
  return {
    ...common,
    status: 'improved',
    message: `The variable-power plan is estimated to be ${saving.toFixed(1)} s faster than the baseline under this model.`,
  };
}
