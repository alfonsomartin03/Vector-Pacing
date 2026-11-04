// Focused tests for the in-browser engine. Reference values marked "Python" come from the original
// backend (backend/vector_pacing) run on the same inputs, to show the TypeScript port reproduces it.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import demoGpx from '../data/demo_course.gpx?raw';
import demoDrafting from '../data/drafting_demo.json?raw';
import { PRESETS } from '../drafting/presets';
import type { DraftingSettings } from '../drafting/types';
import { buildCourse, courseInfo, parseGpx, parseGpxPoints, type RawPoint } from '../engine/course';
import { analyzeScenario, parseScenario, runs } from '../engine/drafting';
import { fitCrc, readFit, scenarioFromFit } from '../engine/fit';
import { handle } from '../engine/handle';
import { cutScale, optimizePacing, terrainBoundaries } from '../engine/optimize';
import { optimizePacingRequest, simulatePacing } from '../engine/pacing';
import { DEFAULT_ENV, DEFAULT_RIDER, Simulator, solveSpeed, wPrimeBalance } from '../engine/simulate';
import { EngineError, erf, hashText, interp, median } from '../engine/util';
import { defaultForm, validate, type RequestParams } from '../params';

const sample = (name: string) => readFileSync(resolve(__dirname, '../../../samples', name), 'utf8');
const sampleBytes = (name: string) => new Uint8Array(readFileSync(resolve(__dirname, '../../../samples', name)));
const defaults = () => validate(defaultForm()).params!;
const withRider = (patch: Record<string, number>): RequestParams => {
  const p = defaults();
  return { ...p, rider: { ...p.rider, ...patch } };
};
const demo = () => parseGpx(demoGpx, 'demo');

/** Straight line due north with the given elevation per point (≈111 m per 0.001°). */
function line(n: number, ele: (i: number) => number | null, stepDeg = 0.001): RawPoint[] {
  return Array.from({ length: n }, (_, i) => ({ lat: 45 + i * stepDeg, lon: 7, ele: ele(i) }));
}
const flatCourse = (km = 10) => buildCourse(line(Math.round(km * 9) + 1, () => 100), { name: 'flat', source: 't', contentHash: 'h' });

describe('util', () => {
  it('interpolates like numpy.interp, clamping at the ends', () => {
    expect(interp(1.5, [0, 1, 2], [0, 10, 30])).toBe(20);
    expect(interp(-1, [0, 1], [5, 6])).toBe(5);
    expect(interp(9, [0, 1], [5, 6])).toBe(6);
  });
  it('takes medians of finite values only', () => {
    expect(median([3, 1, 2, Number.NaN])).toBe(2);
    expect(median([1, 2, 3, 4])).toBe(2.5);
    expect(median([Number.NaN])).toBeNaN();
  });
  it('approximates erf and hashes text deterministically', () => {
    expect(erf(0)).toBeCloseTo(0, 6);
    expect(erf(1)).toBeCloseTo(0.8427008, 6);
    expect(erf(-2)).toBeCloseTo(-0.9953223, 6);
    expect(hashText('abc')).toBe(hashText('abc'));
    expect(hashText('abc')).not.toBe(hashText('abd'));
  });
});

describe('physics and W′', () => {
  const a = 0.5 * 1.225 * 0.28;
  it('solves the steady-speed cubic with a tiny residual', () => {
    for (const [pw, b] of [
      [250, 80.5 * 9.80665 * 0.004],
      [400, 80.5 * 9.80665 * 0.08],
      [0, -80.5 * 9.80665 * 0.05], // descent, three-root branch
      [100, -80.5 * 9.80665 * 0.02],
    ] as const) {
      const v = solveSpeed(pw, a, b);
      expect(v).toBeGreaterThanOrEqual(0);
      expect(Math.abs(a * v ** 3 + b * v - pw)).toBeLessThan(1e-6 * Math.max(1, pw));
    }
    expect(solveSpeed(0, a, 3)).toBe(0); // no power on the flat → no speed
  });
  it('rejects invalid solver inputs', () => {
    expect(() => solveSpeed(-1, a, 1)).toThrow(EngineError);
    expect(() => solveSpeed(100, 0, 1)).toThrow(EngineError);
  });
  it('depletes W′ linearly above CP, recovers exponentially below, and never clamps', () => {
    const w = wPrimeBalance([380, 200, 600], [100, 60, 100], 280, 20_000);
    expect(w[0]).toBeCloseTo(10_000, 9);
    expect(w[1]).toBeCloseTo(20_000 - 10_000 * Math.exp((-80 * 60) / 20_000), 9);
    expect(w[2]).toBeLessThan(0);
  });
});

describe('course parsing', () => {
  it('parses the bundled demo exactly like the Python reference', () => {
    const info = courseInfo(demo());
    // Python: Vector Valley TT (demo), 32000.0867 m, 2000 steps, gain 316.85 m, grades −5.68…6.86 %.
    expect(info.name).toBe('Vector Valley TT (demo)');
    expect(info.length_m).toBe(32000.1);
    expect(info.n_steps).toBe(2000);
    expect(info.elevation_gain_m).toBe(316.8);
    expect(info.min_grade_pct).toBe(-5.68);
    expect(info.max_grade_pct).toBe(6.86);
    expect(info.warnings).toEqual([]);
  });
  it('treats a file without elevation as flat and says so', () => {
    const c = parseGpx(sample('flat_route_no_elevation.gpx'), 'flat.gpx');
    expect(c.grade.every((g) => g === 0)).toBe(true);
    expect(c.warnings.join(' ')).toMatch(/no usable elevation/);
  });
  it('reads multi-segment files and reports joined segments', () => {
    const c = parseGpx(sample('hill_climb_two_segments.gpx'), 'hill.gpx');
    expect(c.warnings.join(' ')).toMatch(/2 track segments were joined/);
    expect(Math.max(...c.grade)).toBeGreaterThan(0.03);
  });
  it('rejects malformed, non-GPX, DTD and empty files with clear messages', () => {
    expect(() => parseGpx(sample('invalid_malformed.gpx'), 'x')).toThrow(/not well-formed XML/);
    expect(() => parseGpx('<kml></kml>', 'x')).toThrow(/not GPX/);
    expect(() => parseGpx('<!DOCTYPE gpx [<!ENTITY a "b">]><gpx/>', 'x')).toThrow(/forbidden XML/);
    expect(() => parseGpx('', 'x')).toThrow(/empty/);
    expect(() => parseGpx('<gpx><trk><trkseg></trkseg></trk></gpx>', 'x')).toThrow(/No track or route points/);
    expect(() => parseGpx('<gpx><trk><trkseg><trkpt lat="91" lon="0"/><trkpt lat="0" lon="0"/></trkseg></trk></gpx>', 'x')).toThrow(/out-of-range/);
  });
  it('uses route points when there is no track', () => {
    const pts = line(20, (i) => i).map((p) => `<rtept lat="${p.lat}" lon="${p.lon}"><ele>${p.ele}</ele></rtept>`).join('');
    const c = parseGpx(`<gpx xmlns="http://www.topografix.com/GPX/1/1"><rte><name>R</name>${pts}</rte></gpx>`, 'r.gpx');
    expect(c.name).toBe('R');
    expect(c.warnings.join(' ')).toMatch(/route/);
  });
  it('removes duplicates, interpolates missing elevation and enforces length limits', () => {
    const pts = line(30, (i) => (i % 3 === 0 ? i : null));
    pts.splice(5, 0, { ...pts[4]! });
    const c = buildCourse(pts, { name: 'x', source: 't', contentHash: 'h' });
    expect(c.warnings.join(' ')).toMatch(/1 duplicate point/);
    expect(c.warnings.join(' ')).toMatch(/had no elevation; values were interpolated/);
    expect(() => buildCourse(line(2, () => 0), { name: 'x', source: 't', contentHash: 'h' })).toThrow(/at least 200 m/);
    expect(() => buildCourse([pts[0]!, pts[0]!], { name: 'x', source: 't', contentHash: 'h' })).toThrow(/zero length/);
  });
  it('clips implausible gradients after smoothing', () => {
    const c = buildCourse(line(40, (i) => (i > 20 ? 2000 : 0), 0.0005), { name: 'x', source: 't', contentHash: 'h' });
    expect(Math.max(...c.grade)).toBeLessThanOrEqual(0.25 + 1e-12);
    expect(c.warnings.join(' ')).toMatch(/clipped/);
  });
});

describe('simulation', () => {
  it('reproduces the Python Auto baseline on the demo course', () => {
    const r = simulatePacing(demo(), { kind: 'demo' }, defaults());
    // Python: 286 W constant, 3125.42 s, lowest W′ 1247.48 J, NP 286.0 W.
    expect(r.baseline.power_w).toBe(286);
    expect(r.baseline.total_time_s).toBe(3125.42);
    expect(r.baseline.min_w_bal_j).toBe(1247.5);
    expect(r.baseline.normalized_power_w).toBeCloseTo(286, 0);
    expect(r.baseline.feasible).toBe(true);
    expect(r.baseline.series.distance_m).toHaveLength(2000);
  });
  it('responds to rider inputs in the physically expected direction', () => {
    const manual = (patch: Record<string, number>) => {
      const p = withRider(patch);
      return simulatePacing(demo(), { kind: 'demo' }, { ...p, baseline: { mode: 'manual', power_w: 250 } }).baseline.total_time_s;
    };
    const base = manual({});
    expect(manual({ rider_mass_kg: 90 })).toBeGreaterThan(base);
    expect(manual({ cda_m2: 0.22 })).toBeLessThan(base);
    expect(manual({ crr: 0.008 })).toBeGreaterThan(base);
    // Higher CP raises the Auto baseline power and lowers the time.
    const auto = (cp: number) => simulatePacing(demo(), { kind: 'demo' }, withRider({ cp_w: cp })).baseline;
    expect(auto(320).power_w).toBeGreaterThan(auto(280).power_w);
    expect(auto(320).total_time_s).toBeLessThan(auto(280).total_time_s);
  });
  it('coasts on steep descents and caps speed at the limit', () => {
    const down = buildCourse(line(40, (i) => 1000 - i * 12), { name: 'd', source: 't', contentHash: 'h' });
    const sim = new Simulator(down, DEFAULT_RIDER, { ...DEFAULT_ENV, coast_speed_kmh: 40, max_speed_kmh: 50 });
    const r = sim.runConstant(300);
    expect(r.coasting.some(Boolean)).toBe(true);
    expect(Math.max(...r.speed) * 3.6).toBeLessThanOrEqual(50 + 1e-9);
    expect(r.capped.some(Boolean)).toBe(true);
    expect(r.power.every((p, i) => !r.coasting[i] || p === 0)).toBe(true);
  });
  it('reports a stall and an exhausted W′ as infeasible without clamping', () => {
    const steep = buildCourse(line(40, (i) => i * 20, 0.0005), { name: 's', source: 't', contentHash: 'h' });
    const slow = new Simulator(steep, DEFAULT_RIDER, DEFAULT_ENV).runConstant(20);
    expect(slow.feasible).toBe(false);
    expect(slow.violations[0]).toMatch(/stall/);
    const hard = new Simulator(flatCourse(), { ...DEFAULT_RIDER, w_prime_j: 5000 }, DEFAULT_ENV).runConstant(500);
    expect(hard.minWbal).toBeLessThan(0);
    expect(hard.violations.join(' ')).toMatch(/W′ balance is exhausted/);
  });
  it('explains when no constant power is feasible or inputs conflict', () => {
    // A 25 % wall: even the upper bound (130 % of a 50 W CP) stalls, so no Auto baseline exists.
    const wall = buildCourse(line(40, (i) => i * 40, 0.0005), { name: 'w', source: 't', contentHash: 'h' });
    expect(() => simulatePacing(wall, { kind: 'gpx' }, withRider({ cp_w: 50 }))).toThrow(/No constant power at or above the lower bound/);
    const p = defaults();
    expect(() =>
      simulatePacing(flatCourse(), { kind: 'gpx' }, { ...p, environment: { ...p.environment, coast_speed_kmh: 90, max_speed_kmh: 80 } }),
    ).toThrow(/coasting threshold/);
  });
});

describe('optimizer heuristic', () => {
  it('finds a verified, bounded, faster plan on the demo course', () => {
    const r = optimizePacingRequest(demo(), { kind: 'demo' }, defaults());
    const o = r.optimization;
    expect(o.status).toBe('improved');
    // The Python SLSQP reference saves 61.2 s here; the browser heuristic is simpler and need not match it.
    expect(o.time_saved_s!).toBeGreaterThan(30);
    expect(o.result!.feasible).toBe(true);
    expect(o.result!.min_w_bal_j).toBeGreaterThanOrEqual(0);
    expect(o.solver.method).toMatch(/heuristic/);
    expect(o.solver.evaluations).toBeLessThanOrEqual(o.solver.max_evaluations);
    expect(o.segments).toHaveLength(12);
    for (const s of o.segments) {
      expect(Number.isInteger(s.planned_power_w)).toBe(true);
      expect(s.planned_power_w!).toBeGreaterThanOrEqual(r.meta.derived.min_power_w);
      expect(s.planned_power_w!).toBeLessThanOrEqual(r.meta.derived.max_power_w);
    }
    // Terrain-aware: the steepest-climbing segment gets more power than the steepest descent.
    const byGrade = [...o.segments].sort((a, b) => a.mean_grade_pct - b.mean_grade_pct);
    expect(byGrade.at(-1)!.planned_power_w!).toBeGreaterThan(byGrade[0]!.planned_power_w!);
    expect(o.notes.join(' ')).toMatch(/not equivalent/);
  });
  it('changes the plan when rider inputs change', () => {
    const a = optimizePacingRequest(demo(), { kind: 'demo' }, defaults()).optimization;
    const b = optimizePacingRequest(demo(), { kind: 'demo' }, withRider({ w_prime_kj: 30, cp_w: 300 })).optimization;
    expect(b.segments.map((s) => s.planned_power_w)).not.toEqual(a.segments.map((s) => s.planned_power_w));
    expect(b.result!.total_time_s).toBeLessThan(a.result!.total_time_s);
  });
  it('recommends the baseline on a flat course where variable power cannot help', () => {
    const r = optimizePacingRequest(flatCourse(), { kind: 'gpx' }, defaults());
    expect(r.optimization.status).toBe('no_improvement');
    expect(r.optimization.message).toMatch(/baseline is recommended/);
  });
  it('fails clearly for an infeasible or out-of-bounds baseline and for crossed bounds', () => {
    const p = defaults();
    const infeasible = optimizePacingRequest(demo(), { kind: 'demo' }, { ...p, baseline: { mode: 'manual', power_w: 400 } });
    expect(infeasible.optimization.status).toBe('failed');
    expect(infeasible.optimization.message).toMatch(/baseline is infeasible/);
    const outside = optimizePacingRequest(demo(), { kind: 'demo' }, { ...p, baseline: { mode: 'manual', power_w: 100 } });
    expect(outside.optimization.message).toMatch(/outside the optimizer power bounds/);
    expect(() =>
      optimizePacingRequest(demo(), { kind: 'demo' }, { ...p, optimizer: { ...p.optimizer, min_power_pct_cp: 100, max_power_pct_cp: 100 } }),
    ).toThrow(/minimum power bound must be below/);
  });
  it('stays honest when the compute budget runs out', () => {
    const c = demo();
    const sim = new Simulator(c, DEFAULT_RIDER, DEFAULT_ENV);
    const base = sim.runConstant(286);
    const out = optimizePacing(sim, base, 286, { nSegments: 12, minPowerW: 168, maxPowerW: 364 }, { maxEvaluations: 30 });
    expect(out.converged).toBe(false);
    expect(out.solverMessage).toMatch(/computation budget/);
    expect(out.evaluations).toBeLessThanOrEqual(30);
    expect(out.result!.feasible).toBe(true); // still independently verified
  });
  it('splits terrain where the profile bends and predicts W′-neutral transfers', () => {
    const ele = Array.from({ length: 101 }, (_, i) => (i <= 50 ? i : 100 - i));
    expect(terrainBoundaries(ele, 2, 5)).toEqual([0, 50, 100]);
    const base = Float64Array.from([100, 50, 10]);
    expect(cutScale(base, Float64Array.from([-20, -20, -20]), Float64Array.from([5, 5, 5]), 0)).toBeCloseTo(2, 9);
    expect(cutScale(base, Float64Array.from([-20, -60, -20]), Float64Array.from([5, 0, 5]), 0)).toBeNull();
  });
});

describe('drafting analysis', () => {
  const scenario = () => parseScenario(JSON.parse(demoDrafting), 'demo', 'h');
  const settings = (patch: Partial<DraftingSettings> = {}): DraftingSettings => ({ ...PRESETS.defaults, ...patch });

  it('reproduces the Python reference on the bundled demo', () => {
    const r = analyzeScenario(scenario(), settings());
    // Python (default settings): counts and dismissed encounters for the 12-rider demo.
    expect(r.counts).toEqual({ potential_drafting: 1, overtake: 3, inconclusive: 5, brief_encounter: 1, drop_back: 2 });
    expect(r.encounter_counts).toEqual({ lateral_separation: 1, separate_course_section: 26, opposite_direction: 3 });
    expect(r.scenario.course.length_m).toBe(8032.7);
    const flagged = r.events.filter((e) => e.flagged);
    expect(flagged).toHaveLength(1);
    expect(flagged[0]!.limitations[0]).toMatch(/^Combined gap uncertainty ≈ ±/);
  });
  it('responds to exempt sections and zone settings like the reference', () => {
    const ex = analyzeScenario(scenario(), settings({ exempt_sections: PRESETS.demo_exempt_sections }));
    expect(ex.counts).toEqual({ potential_drafting: 1, exempt: 2, inconclusive: 4, brief_encounter: 1, overtake: 2, drop_back: 2 });
    const long = analyzeScenario(
      scenario(),
      settings({ zone_length_m: 15, allowed_pass_s: 30, drop_back_s: 30, exempt_sections: PRESETS.demo_exempt_sections }),
    );
    expect(long.counts).toEqual({ potential_drafting: 1, exempt: 2, inconclusive: 3, brief_encounter: 1, overtake: 2, drop_back: 2 });
    expect(long.encounter_counts.separate_course_section).toBe(44);
    expect(long.meta.settings.zone_length_m).toBe(15);
  });
  it('analyses the two-rider upload sample', () => {
    const text = sample('drafting_two_riders.json');
    const r = analyzeScenario(parseScenario(JSON.parse(text), 'drafting_two_riders.json', hashText(text)), settings());
    expect(r.scenario.riders.map((x) => x.id)).toEqual(['A', 'B']);
    expect(r.counts).toEqual({ inconclusive: 1 });
    expect(r.scenario.riders[0]!.warnings.join(' ')).toMatch(/Accuracy not reported/);
    // With better assumed accuracy the same following becomes a flag (as in the Python reference).
    const precise = analyzeScenario(parseScenario(JSON.parse(text), 'u', 'h'), settings({ assumed_accuracy_m: 1 }));
    expect(precise.counts).toEqual({ potential_drafting: 1 });
  });
  it('accepts row and columnar samples with ISO times and validates documents', () => {
    const course = { points: [[44, 5], [44, 5.01]] };
    const rows = (id: string, off: number) => ({
      id,
      samples: [0, 1, 2].map((k) => ({ time: `2026-01-01T00:00:0${k}Z`, lat: 44, lon: 5 + 0.0001 * (k + off) })),
    });
    const ok = parseScenario({ format: 'vector-pacing-riders/1', course, riders: [rows('a', 0), rows('b', 1)] }, 'u', 'h');
    expect(ok.startTime).toBe('2026-01-01T00:00:00Z');
    expect(ok.riders[1]!.t).toEqual([0, 1, 2]);
    const bad = (doc: unknown) => () => parseScenario(doc, 'u', 'h');
    expect(bad([])).toThrow(/JSON object/);
    expect(bad({ format: 'x' })).toThrow(/Unsupported format/);
    expect(bad({ format: 'vector-pacing-riders/1', course, riders: [rows('a', 0)] })).toThrow(/at least two riders/);
    expect(bad({ format: 'vector-pacing-riders/1', course, riders: [rows('a', 0), rows('a', 1)] })).toThrow(/Duplicate rider id/);
    expect(bad({ format: 'vector-pacing-riders/1', course, riders: [rows('a', 0), { id: 'b', samples: { t: [0, 1], lat: [44] } }] })).toThrow(
      /equal length/,
    );
  });
  it('joins in-zone runs across short gaps only', () => {
    expect(runs([true, true, false, true, false, false, false, true], 1)).toEqual([
      [0, 3],
      [7, 7],
    ]);
    expect(runs([false, false], 3)).toEqual([]);
  });
});

describe('.fit import', () => {
  const rides = (dir: string, names: string[]) => names.map((n) => ({ name: n, bytes: sampleBytes(`${dir}/${n}`) }));
  const courseOf = (dir: string) => {
    const { name, points } = parseGpxPoints(sample(`${dir}/course.gpx`));
    return { filename: 'course.gpx', name, points: points.map((p): [number, number] => [p.lat, p.lon]) };
  };
  const run = async (riders: { name: string; bytes: Uint8Array }[], course: ReturnType<typeof courseOf> | null) => {
    const r = await handle({ type: 'drafting', scenario: { kind: 'fit', riders, course }, settings: { ...PRESETS.defaults } });
    return r as Awaited<ReturnType<typeof analyzeScenario>>;
  };

  it('reads positions from the FIT samples and verifies the checksum', () => {
    const tr = readFit(sampleBytes('drafting_two_riders/A.fit'), 'A.fit');
    expect(tr.t).toHaveLength(240); // Python reference: 240 positioned records
    expect(tr.accuracy.every((a) => a === null)).toBe(true);
    expect(new Date(tr.t[0]! * 1000).toISOString()).toBe('2026-07-01T09:00:00.000Z');
    const bytes = sampleBytes('drafting_two_riders/A.fit');
    expect(fitCrc(bytes, 0, bytes.length - 2)).toBe(bytes[bytes.length - 2]! | (bytes[bytes.length - 1]! << 8));
    bytes[40] = bytes[40]! ^ 0xff;
    expect(() => readFit(bytes, 'x.fit')).toThrow(/checksum mismatch/);
  });

  it('rejects invalid FIT files with clear messages', () => {
    expect(() => readFit(sampleBytes('drafting_invalid.fit'), 'bad.fit')).toThrow('bad.fit: the FIT file is truncated.');
    expect(() => readFit(new TextEncoder().encode('hello world, not fit'), 'n.fit')).toThrow(/not a FIT file/);
    expect(() => readFit(new Uint8Array(), 'e.fit')).toThrow(/empty/);
    expect(() => scenarioFromFit({ riders: rides('drafting_two_riders', ['A.fit']), course: null })).toThrow(/at least two .fit files/);
  });

  it('matches the Python reference on the sample rides, with and without a course file', async () => {
    const withPower = rides('drafting_with_power', ['R01.fit', 'R02.fit']);
    for (const course of [courseOf('drafting_with_power'), null]) {
      const r = await run(withPower, course);
      // Python: one interval R02 → R01, potential drafting, grid 330–419, median gap 5.0 m.
      expect(r.events.map((e) => [e.follower, e.leader, e.classification, e.start_s, e.end_s, e.gap_median_m])).toEqual([
        ['R02', 'R01', 'potential_drafting', 330, 419, 5],
      ]);
      expect(r.scenario.start_time).toBe('2026-07-01T09:00:00Z');
      expect(r.scenario.course.name).toBe(course ? 'Flat 11 km' : 'Track of R01');
    }
    const two = await run(rides('drafting_two_riders', ['A.fit', 'B.fit']), courseOf('drafting_two_riders'));
    expect(two.events.map((e) => [e.follower, e.leader, e.classification])).toEqual([['B', 'A', 'inconclusive']]);
    expect(two.scenario.name).toBe('2 riders from .fit files');
    expect(two.scenario.riders[0]!.warnings.join(' ')).toMatch(/Accuracy not reported/);
  });

  it('thins the first track into a course line like the reference', () => {
    const { doc } = scenarioFromFit({ riders: rides('drafting_with_power', ['R01.fit', 'R02.fit']), course: null });
    expect((doc.course as { points: unknown[] }).points).toHaveLength(900); // Python: 900 points
    expect(doc.description).toMatch(/R01.fit's own track is used as the course line/);
  });
});
