// Potential-drafting detection for events where drafting is restricted (e.g. ITTs), in the browser.
// TypeScript port of the GPS following check in backend/vector_pacing/drafting.py (+ the response
// shaping in drafting_api.py). Same method and settings:
//  1. Import a `vector-pacing-riders/1` document (course line + timestamped rider positions).
//  2. Project each sample onto the course line, continuity-constrained so out-and-back lanes are not mixed up.
//  3. Resample every rider onto a common time grid; gaps longer than max_interpolation_gap_s are missing;
//     a centred rolling median reduces jitter.
//  4. Zone test per ordered pair and time: 0 < gap ≤ zone, both moving, same direction, physical distance
//     ≤ zone + 3σ, not laterally separated (only when both accuracies are good enough).
//  5. Join in-zone times into intervals and classify them (overtake / drop-back / brief encounter /
//     potential drafting / inconclusive / exempt). Time inside exempt sections does not count.
//  6. Report nearby riders who were not following (separate section, opposite direction, side by side, stationary).
// .fit rides are converted to the same document in fit.ts. Not ported: the RaceGuard power screen.
import type {
  Classification,
  DraftEvent,
  DraftingResponse,
  DraftingSettings,
  EncounterType,
  ExemptSection,
} from '../drafting/types';
import { APP_VERSION } from './version';
import { EngineError, fmtG, interp, maxOf, median, minOf, phi, round, searchsorted } from './util';

export const FORMAT_ID = 'vector-pacing-riders/1';
export const DRAFT_MODEL_VERSION = 'vp-draft-1.0';
const EARTH_RADIUS_M = 6_371_008.8;
const MAX_RIDERS = 200;
const MAX_TOTAL_SAMPLES = 500_000;
const MAX_DURATION_S = 24 * 3600;
const MOVING_SPEED_MS = 1.0;
const OFF_COURSE_M = 50.0;
const PROJECTION_SPEED_MS = 25.0;
const PROJECTION_MARGIN_M = 50.0;
const MIN_CONFIDENCE = 0.6;
const MAX_COURSE_POINTS_OUT = 1500;

export const DISCLAIMER =
  'Potential drafting only. Intervals are derived from GNSS position estimates and the configured, illustrative settings. They are prompts for human review, not confirmed rules violations, and no penalty is implied.';

// ---------------------------------------------------------------- import

interface RiderTrack {
  id: string;
  label: string;
  t: number[];
  lat: number[];
  lon: number[];
  acc: number[]; // NaN where not reported
  accuracyReported: boolean;
  warnings: string[];
}

export interface Scenario {
  name: string;
  description: string;
  illustrative: boolean;
  source: string;
  contentHash: string;
  startTime: string | null;
  courseLatLon: [number, number][];
  courseName: string;
  riders: RiderTrack[];
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);

function numOf(v: unknown, what: string): number {
  if (typeof v !== 'number') throw new EngineError(`${what} must be a number.`);
  if (!Number.isFinite(v)) throw new EngineError(`${what} must be finite.`);
  return v;
}

function timeOf(v: unknown, what: string): number {
  if (typeof v === 'string') {
    const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : `${v}Z`;
    const ms = Date.parse(iso);
    if (Number.isNaN(ms)) throw new EngineError(`${what} is not an ISO 8601 timestamp: '${v}'.`);
    return ms / 1000;
  }
  return numOf(v, what);
}

function rowsOf(samples: unknown, rid: string): Obj[] {
  if (Array.isArray(samples)) {
    if (!samples.every(isObj)) throw new EngineError(`Rider ${rid}: every sample must be an object.`);
    return samples;
  }
  if (isObj(samples)) {
    const keys = Object.keys(samples);
    const lens = keys.map((k) => (Array.isArray(samples[k]) ? (samples[k] as unknown[]).length : -1));
    if (!keys.length || lens.includes(-1) || new Set(lens).size !== 1) {
      throw new EngineError(`Rider ${rid}: columnar samples must be arrays of equal length.`);
    }
    return Array.from({ length: lens[0]! }, (_, i) => Object.fromEntries(keys.map((k) => [k, (samples[k] as unknown[])[i]])));
  }
  throw new EngineError(`Rider ${rid}: 'samples' must be a list of objects or an object of arrays.`);
}

/** Validate a `vector-pacing-riders/1` document. Throws EngineError with a user-facing message. */
export function parseScenario(doc: unknown, source: string, contentHash: string): Scenario {
  if (!isObj(doc)) throw new EngineError('The document must be a JSON object.');
  if (doc.format !== FORMAT_ID) throw new EngineError(`Unsupported format: expected "format": "${FORMAT_ID}".`);
  const course = doc.course;
  if (!isObj(course) || !Array.isArray(course.points)) throw new EngineError("'course.points' must be a list of [lat, lon] pairs.");
  const pts: [number, number][] = course.points.map((p: unknown, i: number) => {
    if (!Array.isArray(p) || p.length < 2) throw new EngineError(`Course point ${i + 1} must be [lat, lon].`);
    const lat = numOf(p[0], `Course point ${i + 1} latitude`);
    const lon = numOf(p[1], `Course point ${i + 1} longitude`);
    if (Math.abs(lat) > 90 || Math.abs(lon) > 180) throw new EngineError(`Course point ${i + 1} is out of range.`);
    return [lat, lon];
  });
  if (pts.length < 2) throw new EngineError('The course needs at least two points.');
  const ridersDoc = doc.riders;
  if (!Array.isArray(ridersDoc) || ridersDoc.length < 2) throw new EngineError("'riders' must list at least two riders.");
  if (ridersDoc.length > MAX_RIDERS) throw new EngineError(`At most ${MAX_RIDERS} riders are supported.`);

  const origin = typeof doc.start_time === 'string' ? doc.start_time : null;
  const originTs = origin !== null ? timeOf(origin, 'start_time') : null;
  const seen = new Set<string>();
  let total = 0;
  let usesIso: boolean | null = null;
  const raw: { id: string; label: string; samples: [number, number, number, number | null][]; warns: string[] }[] = [];
  for (const r of ridersDoc) {
    if (!isObj(r) || typeof r.id !== 'string' || !r.id.trim()) throw new EngineError("Every rider needs a non-empty string 'id'.");
    const rid = r.id.trim().slice(0, 40);
    if (seen.has(rid)) throw new EngineError(`Duplicate rider id '${rid}'.`);
    seen.add(rid);
    const rows = rowsOf(r.samples, rid);
    total += rows.length;
    if (total > MAX_TOTAL_SAMPLES) throw new EngineError(`More than ${MAX_TOTAL_SAMPLES.toLocaleString('en-US')} samples in total.`);
    const samples: [number, number, number, number | null][] = [];
    const warns: string[] = [];
    let dropped = 0;
    rows.forEach((s, k) => {
      let t: number;
      let iso: boolean;
      if ('time' in s) {
        iso = true;
        t = timeOf(s.time, `Rider ${rid} sample ${k + 1} time`);
      } else if ('t' in s) {
        iso = false;
        t = numOf(s.t, `Rider ${rid} sample ${k + 1} t`);
      } else throw new EngineError(`Rider ${rid} sample ${k + 1} needs 't' (seconds) or 'time' (ISO 8601).`);
      if (usesIso === null) usesIso = iso;
      else if (usesIso !== iso) throw new EngineError("Use either 't' or 'time' consistently for all samples.");
      if (s.lat === null || s.lat === undefined || s.lon === null || s.lon === undefined) {
        dropped++;
        return;
      }
      const lat = numOf(s.lat, `Rider ${rid} latitude`);
      const lon = numOf(s.lon, `Rider ${rid} longitude`);
      if (Math.abs(lat) > 90 || Math.abs(lon) > 180) throw new EngineError(`Rider ${rid} sample ${k + 1} has out-of-range coordinates.`);
      let acc: number | null = null;
      if (s.accuracy_m !== null && s.accuracy_m !== undefined) {
        acc = numOf(s.accuracy_m, `Rider ${rid} accuracy_m`);
        if (acc <= 0 || acc > 1000) throw new EngineError(`Rider ${rid} sample ${k + 1}: accuracy_m must be in (0, 1000].`);
      }
      samples.push([t, lat, lon, acc]);
    });
    if (dropped) warns.push(`${dropped} sample(s) without a position were ignored.`);
    raw.push({ id: rid, label: String(r.label || rid).slice(0, 80), samples, warns });
  }
  const allT = raw.flatMap((r) => r.samples.map((s) => s[0]));
  if (!allT.length) throw new EngineError('No rider samples with positions were found.');
  const t0 = usesIso && originTs !== null ? originTs : usesIso ? minOf(allT) : 0;
  let startTime = origin;
  if (usesIso && startTime === null) startTime = new Date(t0 * 1000).toISOString().replace('.000Z', 'Z');

  const riders: RiderTrack[] = raw.map(({ id, label, samples, warns }) => {
    if (samples.length < 2) throw new EngineError(`Rider ${id} needs at least two samples with positions.`);
    const sorted = [...samples].sort((a, b) => a[0] - b[0]);
    const kept = sorted.filter((s, i) => i === 0 || s[0] - sorted[i - 1]![0] > 1e-6);
    if (kept.length < sorted.length) warns.push(`${sorted.length - kept.length} sample(s) with duplicate timestamps were ignored.`);
    if (sorted.some((s, i) => s[0] !== samples[i]![0])) warns.push('Samples were not in time order and were sorted.');
    const t = kept.map((s) => s[0] - t0);
    if (t[t.length - 1]! - t[0]! > MAX_DURATION_S) throw new EngineError(`Rider ${id} spans more than 24 hours.`);
    const reported = kept.every((s) => s[3] !== null);
    if (!reported && kept.some((s) => s[3] !== null)) warns.push('Accuracy was missing for some samples; the assumed accuracy was used there.');
    return {
      id,
      label,
      t,
      lat: kept.map((s) => s[1]),
      lon: kept.map((s) => s[2]),
      acc: kept.map((s) => (s[3] === null ? Number.NaN : s[3])),
      accuracyReported: reported,
      warnings: warns,
    };
  });
  return {
    name: String(doc.name || 'Uploaded rider data').slice(0, 120),
    description: String(doc.description || '').slice(0, 2000),
    illustrative: Boolean(doc.illustrative),
    source,
    contentHash,
    startTime,
    courseLatLon: pts,
    courseName: String(course.name || 'Course').slice(0, 120),
    riders,
  };
}

// ---------------------------------------------------------------- geometry

const rad = (d: number) => (d * Math.PI) / 180;

export class CourseLine {
  readonly lat0: number;
  readonly lon0: number;
  readonly xy: [number, number][];
  readonly segLen: number[];
  readonly cum: number[];
  readonly lengthM: number;

  constructor(latlon: [number, number][]) {
    this.lat0 = latlon.reduce((s, p) => s + p[0], 0) / latlon.length;
    this.lon0 = latlon.reduce((s, p) => s + p[1], 0) / latlon.length;
    const all = latlon.map(([la, lo]) => this.toXy(la, lo));
    const xy = [all[0]!];
    for (let i = 1; i < all.length; i++) {
      if (Math.hypot(all[i]![0] - all[i - 1]![0], all[i]![1] - all[i - 1]![1]) > 1e-3) xy.push(all[i]!);
    }
    if (xy.length < 2) throw new EngineError('The course has zero length.');
    this.xy = xy;
    this.segLen = xy.slice(1).map((p, i) => Math.hypot(p[0] - xy[i]![0], p[1] - xy[i]![1]));
    this.cum = [0];
    for (const l of this.segLen) this.cum.push(this.cum[this.cum.length - 1]! + l);
    this.lengthM = this.cum[this.cum.length - 1]!;
  }

  toXy(lat: number, lon: number): [number, number] {
    return [EARTH_RADIUS_M * rad(lon - this.lon0) * Math.cos(rad(this.lat0)), EARTH_RADIUS_M * rad(lat - this.lat0)];
  }

  /** Nearest point on segment k: [course position, signed left offset, distance]. */
  private onSeg(p: [number, number], k: number): [number, number, number] {
    const a = this.xy[k]!;
    const dx = this.xy[k + 1]![0] - a[0];
    const dy = this.xy[k + 1]![1] - a[1];
    const ln = this.segLen[k]!;
    const rx = p[0] - a[0];
    const ry = p[1] - a[1];
    const u = Math.min(1, Math.max(0, (rx * dx + ry * dy) / (ln * ln)));
    const dist = Math.hypot(p[0] - (a[0] + dx * u), p[1] - (a[1] + dy * u));
    return [this.cum[k]! + u * ln, (dx * ry - dy * rx) / ln, dist];
  }

  /**
   * Project p onto segments lo..hi-1. Without a prediction the nearest point wins (ties → earliest).
   * With `sPred`, near-equal matches are grouped into contiguous sections and the section closest to
   * the prediction is chosen; the point within it is the geometric nearest.
   */
  project(p: [number, number], lo: number, hi: number, sPred: number | null = null, acc = 1): [number, number, number] {
    const res: [number, number, number][] = [];
    let best = Infinity;
    for (let k = lo; k < hi; k++) {
      const r = this.onSeg(p, k);
      res.push(r);
      if (r[2] < best) best = r[2];
    }
    if (sPred === null) {
      let pick = -1;
      for (let j = 0; j < res.length; j++) if (res[j]![2] <= best + 1e-9 && (pick < 0 || res[j]![0] < res[pick]![0])) pick = j;
      return res[pick]!;
    }
    const tol = best + Math.max(3 * acc, 10);
    let choice: [number, number, number] | null = null;
    let choiceCost = Infinity;
    let groupBest = -1;
    const close = () => {
      if (groupBest < 0) return;
      const r = res[groupBest]!;
      const cost = Math.abs(r[0] - sPred) + r[2];
      if (cost < choiceCost) {
        choiceCost = cost;
        choice = r;
      }
      groupBest = -1;
    };
    for (let j = 0; j < res.length; j++) {
      if (res[j]![2] <= tol) {
        if (groupBest < 0 || res[j]![2] < res[groupBest]![2]) groupBest = j;
      } else close();
    }
    close();
    return choice!;
  }

  segmentRange(sLo: number, sHi: number): [number, number] {
    const lo = Math.max(0, searchsorted(this.cum, sLo, 'right') - 1);
    const hi = Math.min(this.segLen.length, searchsorted(this.cum, sHi, 'left') + 1);
    return [lo, Math.max(hi, lo + 1)];
  }
}

interface Projected {
  s: number[];
  offset: number[];
  off: number[];
  x: number[];
  y: number[];
}

function projectTrack(course: CourseLine, r: RiderTrack, assumedAcc: number, startAmbiguity = 10): Projected {
  const xy = r.t.map((_, i) => course.toXy(r.lat[i]!, r.lon[i]!));
  const n = xy.length;
  const s = new Array<number>(n);
  const offset = new Array<number>(n);
  const off = new Array<number>(n);
  // First sample: among near-equal matches pick the course direction that best fits the initial heading.
  const all = course.segLen.map((_, k) => {
    const [ss, , dd] = course.project(xy[0]!, k, k + 1);
    return { k, ss, dd };
  });
  const dmin = minOf(all.map((c) => c.dd));
  const cand = all.filter((c) => c.dd <= dmin + startAmbiguity);
  const movedIdx = xy.findIndex((p) => Math.hypot(p[0] - xy[0]![0], p[1] - xy[0]![1]) > 20);
  let k: { k: number; ss: number; dd: number };
  if (movedIdx >= 0 && cand.length > 1) {
    const h = [xy[movedIdx]![0] - xy[0]![0], xy[movedIdx]![1] - xy[0]![1]];
    const hn = Math.hypot(h[0]!, h[1]!);
    const align = cand.map((c) => {
      const dx = course.xy[c.k + 1]![0] - course.xy[c.k]![0];
      const dy = course.xy[c.k + 1]![1] - course.xy[c.k]![1];
      return (dx * h[0]! + dy * h[1]!) / (course.segLen[c.k]! * hn);
    });
    const amax = maxOf(align);
    const best = cand.filter((_, i) => align[i]! >= amax - 0.05);
    k = best.reduce((a, b) => (b.dd < a.dd ? b : a));
  } else {
    k = cand.reduce((a, b) => (b.ss < a.ss ? b : a));
  }
  let [lo, hi] = course.segmentRange(k.ss - 1, k.ss + 1);
  [s[0], offset[0], off[0]] = course.project(xy[0]!, lo, hi);
  let vPrev = 0;
  for (let i = 1; i < n; i++) {
    const dt = r.t[i]! - r.t[i - 1]!;
    const w = PROJECTION_SPEED_MS * dt + PROJECTION_MARGIN_M;
    [lo, hi] = course.segmentRange(s[i - 1]! - w, s[i - 1]! + w);
    const acc = Number.isFinite(r.acc[i]!) ? r.acc[i]! : assumedAcc;
    [s[i], offset[i], off[i]] = course.project(xy[i]!, lo, hi, s[i - 1]! + vPrev * dt, Math.max(acc, 0.5));
    vPrev = Math.min(PROJECTION_SPEED_MS, Math.max(-PROJECTION_SPEED_MS, (s[i]! - s[i - 1]!) / dt));
  }
  return { s, offset, off, x: xy.map((p) => p[0]), y: xy.map((p) => p[1]) };
}

// ---------------------------------------------------------------- synchronisation

function rollingNanMedian(x: number[], w: number): number[] {
  if (w <= 1 || x.length < w) return x;
  const h = w >> 1;
  return x.map((v, i) => {
    if (!Number.isFinite(v)) return Number.NaN;
    return median(x.slice(Math.max(0, i - h), Math.min(x.length, i + h + 1)));
  });
}

function gradient(v: number[], step: number): number[] {
  const n = v.length;
  if (n < 2) return v.map(() => 0);
  return v.map((_, i) => (i === 0 ? (v[1]! - v[0]!) / step : i === n - 1 ? (v[n - 1]! - v[n - 2]!) / step : (v[i + 1]! - v[i - 1]!) / (2 * step)));
}

interface GridTrack {
  s: number[];
  offset: number[];
  x: number[];
  y: number[];
  acc: number[];
  vx: number[];
  vy: number[];
  valid: boolean[];
}

function resample(r: RiderTrack, p: Projected, grid: number[], st: DraftingSettings): { g: GridTrack; active: number } {
  const t = r.t;
  const n = t.length;
  const inside = grid.map((g) => g >= t[0]! && g <= t[n - 1]!);
  let valid = grid.map((g, i) => {
    if (!inside[i]) return false;
    const j = searchsorted(t, g, 'right');
    const jl = Math.min(Math.max(j - 1, 0), n - 1);
    const jr = Math.min(Math.max(j, 0), n - 1);
    const exact = Math.abs(g - t[jl]!) <= 1e-9 + 1e-5 * Math.abs(t[jl]!);
    return exact || t[jr]! - t[jl]! <= st.max_interpolation_gap_s + 1e-9;
  });
  const accRaw = r.acc.map((a) => (Number.isFinite(a) ? a : st.assumed_accuracy_m));
  const at = (v: number[]) => grid.map((g, i) => (valid[i] ? interp(g, t, v) : Number.NaN));
  let w = Math.max(1, Math.round(st.smoothing_s / st.analysis_step_s));
  if (w % 2 === 0) w += 1;
  const s = rollingNanMedian(at(p.s), w);
  const offset = rollingNanMedian(at(p.offset), w);
  const x = rollingNanMedian(at(p.x), w);
  const y = rollingNanMedian(at(p.y), w);
  const acc = at(accRaw);
  const vx = gradient(x, st.analysis_step_s);
  const vy = gradient(y, st.analysis_step_s);
  valid = valid.map((v, i) => v && Number.isFinite(s[i]!) && Number.isFinite(vx[i]!) && Number.isFinite(vy[i]!));
  return { g: { s, offset, x, y, acc, vx, vy, valid }, active: inside.filter(Boolean).length };
}

// ---------------------------------------------------------------- detection

interface PairData {
  gap: number[];
  dist: number[];
  sigma: number[];
  latSep: number[];
  latReliable: boolean[];
  valid: boolean[];
}

interface Interval {
  follower: string;
  leader: string;
  classification: Classification;
  i0: number;
  i1: number;
  startedBy: string;
  endedBy: string;
  duration: number;
  counted: number;
  exempt: number;
  missing: number;
  gapMedian: number;
  gapMin: number;
  gapMax: number;
  sigmaMedian: number;
  conf: number;
  quality: 'good' | 'limited' | 'insufficient';
  latUsed: boolean;
  limitations: string[];
  series: DraftEvent['series'];
  courseM: [number, number];
  orderUncertain: boolean;
}

interface EncounterRaw {
  a: string;
  b: string;
  type: EncounterType;
  i0: number;
  i1: number;
  duration: number;
  minDist: number;
  medianGap: number;
}

/** Contiguous true runs; runs separated by ≤ `bridge` false samples are merged. */
export function runs(mask: boolean[], bridge: number): [number, number][] {
  const out: [number, number][] = [];
  let start = -1;
  let prev = -1;
  for (let k = 0; k < mask.length; k++) {
    if (!mask[k]) continue;
    if (start < 0) start = k;
    else if (k - prev - 1 > bridge) {
      out.push([start, prev]);
      start = k;
    }
    prev = k;
  }
  if (start >= 0) out.push([start, prev]);
  return out;
}

const inSections = (s: number, secs: ExemptSection[]) => secs.some((x) => s >= x.start_m && s <= x.end_m);
const finite = (a: number[]) => a.filter(Number.isFinite);
const clean = (a: number[], nd = 2) => a.map((v) => (Number.isFinite(v) ? round(v, nd) : null));

function classify(f: string, ld: string, i0: number, i1: number, grid: number[], A: GridTrack, pc: PairData, st: DraftingSettings): Interval {
  const step = st.analysis_step_s;
  const L = st.zone_length_m;
  const { gap, sigma, valid } = pc;
  const look = Math.max(1, Math.round(10 / step));
  const firstValid = (from: number, dir: 1 | -1): number | null => {
    for (let k = from, c = 0; c < look; k += dir, c++) if (k >= 0 && k < grid.length && valid[k]) return gap[k]!;
    return null;
  };
  const before = firstValid(i0 - 1, -1);
  const after = firstValid(i1 + 1, 1);
  const startedBy = before !== null && before <= 0 ? 'overtaken' : 'approached';
  const endedBy = after === null ? 'data_unavailable' : after <= 0 ? 'follower_passed' : after > L ? 'gap_opened' : 'zone_conditions_ended';

  const idx = Array.from({ length: i1 - i0 + 1 }, (_, k) => i0 + k);
  const duration = idx.length * step;
  const exempt = idx.filter((i) => inSections(Number.isFinite(A.s[i]!) ? A.s[i]! : -1e12, st.exempt_sections)).length * step;
  const counted = duration - exempt;
  const vIdx = idx.filter((i) => valid[i]);
  const missing = (idx.length - vIdx.length) * step;
  const g = vIdx.map((i) => gap[i]!);
  const sg = vIdx.map((i) => sigma[i]!);
  const sigmaMed = sg.length ? median(sg) : Infinity;
  const conf = g.length ? g.reduce((s, gi, k) => s + phi((L - gi) / sg[k]!) - phi(-gi / sg[k]!), 0) / g.length : 0;
  const missingFrac = missing / duration;
  const quality = sigmaMed <= L / 3 && missingFrac <= 0.1 ? 'good' : sigmaMed <= L / 2 && missingFrac <= 0.3 ? 'limited' : 'insufficient';
  const latUsed = vIdx.length > 0 && vIdx.every((i) => pc.latReliable[i]);
  const allowance = startedBy === 'overtaken' ? st.drop_back_s : st.allowed_pass_s;
  let cls: Classification;
  if (counted <= 0) cls = 'exempt';
  else if (quality === 'insufficient') cls = 'inconclusive';
  else if (counted <= allowance) cls = endedBy === 'follower_passed' ? 'overtake' : startedBy === 'overtaken' ? 'drop_back' : 'brief_encounter';
  else if (conf < MIN_CONFIDENCE) cls = 'inconclusive';
  else cls = 'potential_drafting';

  const lim = [`Combined gap uncertainty ≈ ±${sigmaMed.toFixed(1)} m (1σ) against a ${fmtG(L)} m zone.`];
  if (conf < 1) lim.push(`Estimated probability that the gap was inside the zone: ${Math.round(conf * 100)}% (averaged over the interval).`);
  if (missing > 0) lim.push(`${missing.toFixed(0)} s without usable positions were bridged inside this interval.`);
  if (exempt > 0) lim.push(`${exempt.toFixed(0)} s fell inside exempt course sections and were not counted.`);
  if (!latUsed) {
    lim.push('Lateral separation could not be used (position accuracy too low); riders side by side cannot be distinguished from riders in line.');
  }
  if (endedBy === 'data_unavailable') lim.push('The interval ends where usable data stops (dropout or end of track), so its end is uncertain.');
  if (cls === 'potential_drafting' && endedBy === 'follower_passed') {
    lim.push(`The follower eventually passed, but after more than the allowed ${fmtG(st.allowed_pass_s)} s.`);
  }
  if (cls === 'inconclusive') {
    lim.push('Data quality is too low to tell following from passing or riding apart; this is not a potential-drafting flag. Review manually if needed.');
  }

  const ctx = Math.max(1, Math.round(15 / step));
  const c0 = Math.max(0, i0 - ctx);
  const c1 = Math.min(grid.length - 1, i1 + ctx);
  const cs = <T,>(a: T[]) => a.slice(c0, c1 + 1);
  const sVals = finite(idx.map((i) => A.s[i]!));
  return {
    follower: f,
    leader: ld,
    classification: cls,
    i0,
    i1,
    startedBy,
    endedBy,
    duration,
    counted,
    exempt,
    missing,
    gapMedian: g.length ? median(g) : Number.NaN,
    gapMin: g.length ? minOf(g) : Number.NaN,
    gapMax: g.length ? maxOf(g) : Number.NaN,
    sigmaMedian: sigmaMed,
    conf,
    quality,
    latUsed,
    limitations: lim,
    series: {
      t_s: clean(cs(grid), 1),
      gap_m: clean(cs(gap)),
      sigma_m: clean(cs(sigma)),
      distance_m: clean(cs(pc.dist)),
      lateral_sep_m: clean(cs(pc.latSep.map((v, i) => (pc.latReliable[i] ? v : Number.NaN)))),
      follower_x: clean(cs(A.x), 1),
      follower_y: clean(cs(A.y), 1),
    },
    courseM: [sVals.length ? minOf(sVals) : Number.NaN, sVals.length ? maxOf(sVals) : Number.NaN],
    orderUncertain: false,
  };
}

function mergeInconclusive(
  intervals: Interval[],
  grid: number[],
  bridge: number,
  tracks: Map<string, GridTrack>,
  pairs: Map<string, PairData>,
  st: DraftingSettings,
): Interval[] {
  const out = intervals.filter((iv) => iv.classification !== 'inconclusive');
  const groups = new Map<string, Interval[]>();
  for (const iv of intervals) {
    if (iv.classification !== 'inconclusive') continue;
    const key = [iv.follower, iv.leader].sort().join('\u0000');
    groups.set(key, [...(groups.get(key) ?? []), iv]);
  }
  const flush = (cur: Interval[]) => {
    if (cur.length === 1) return void out.push(cur[0]!);
    const totals = new Map<string, number>();
    for (const c of cur) totals.set(`${c.follower}\u0000${c.leader}`, (totals.get(`${c.follower}\u0000${c.leader}`) ?? 0) + (c.i1 - c.i0 + 1));
    let bestKey = '';
    let bestN = -1;
    for (const [k, v] of totals) if (v > bestN) [bestKey, bestN] = [k, v];
    const [f, ld] = bestKey.split('\u0000') as [string, string];
    const m = classify(f, ld, minOf(cur.map((c) => c.i0)), maxOf(cur.map((c) => c.i1)), grid, tracks.get(f)!, pairs.get(bestKey)!, st);
    m.classification = 'inconclusive';
    m.orderUncertain = totals.size > 1;
    if (m.orderUncertain) {
      m.limitations.push(`${cur.length} short intervals in both orders were merged; which rider was ahead could not be determined reliably.`);
    }
    out.push(m);
  };
  for (const ivs of groups.values()) {
    ivs.sort((a, b) => a.i0 - b.i0);
    let cur = [ivs[0]!];
    for (const iv of ivs.slice(1)) {
      if (iv.i0 <= maxOf(cur.map((c) => c.i1)) + bridge) cur.push(iv);
      else {
        flush(cur);
        cur = [iv];
      }
    }
    flush(cur);
  }
  return out;
}

export function analyzeScenario(sc: Scenario, st: DraftingSettings): DraftingResponse {
  const course = new CourseLine(sc.courseLatLon);
  const step = st.analysis_step_s;
  const tMin = minOf(sc.riders.map((r) => r.t[0]!));
  const tMax = maxOf(sc.riders.map((r) => r.t[r.t.length - 1]!));
  const g0 = Math.floor(tMin);
  const nGrid = Math.ceil((tMax + 1e-9 - g0) / step);
  if (nGrid > 200_000) throw new EngineError('The scenario is too long for the chosen analysis step.');
  const grid = Array.from({ length: nGrid }, (_, i) => g0 + i * step);

  const tracks = new Map<string, GridTrack>();
  const riders: DraftingResponse['scenario']['riders'] = [];
  for (const r of sc.riders) {
    const proj = projectTrack(course, r, st.assumed_accuracy_m);
    const { g, active } = resample(r, proj, grid, st);
    tracks.set(r.id, g);
    const iv = r.t.slice(1).map((t, i) => t - r.t[i]!);
    const med = median(iv);
    const warns = [...r.warnings];
    const maxOff = maxOf(proj.off);
    if (maxOff > OFF_COURSE_M) warns.push(`Up to ${maxOff.toFixed(0)} m from the course line; course positions may be unreliable there.`);
    if (!r.accuracyReported) warns.push(`Accuracy not reported; ${fmtG(st.assumed_accuracy_m)} m assumed.`);
    const irregular = iv.length ? iv.filter((d) => Math.abs(d - med) > 0.2 * med).length / iv.length : 0;
    const missing = 1 - g.valid.filter(Boolean).length / Math.max(active, 1);
    if (irregular > 0.1) warns.push('Sampling is irregular; positions were interpolated onto the common time grid.');
    if (missing > 0.05) warns.push(`${Math.round(missing * 100)}% of this rider's time span has no usable position (dropouts).`);
    riders.push({
      id: r.id,
      label: r.label,
      samples: r.t.length,
      start_s: round(r.t[0]!, 1),
      end_s: round(r.t[r.t.length - 1]!, 1),
      median_interval_s: round(med, 2),
      irregular_interval_fraction: round(irregular, 3),
      max_interval_s: round(iv.length ? maxOf(iv) : 0, 2),
      missing_fraction: round(missing, 3),
      accuracy_median_m: round(median(r.acc.map((a) => (Number.isFinite(a) ? a : st.assumed_accuracy_m))), 2),
      accuracy_reported: r.accuracyReported,
      max_off_course_m: round(maxOff, 1),
      warnings: warns,
    });
  }

  const L = st.zone_length_m;
  const bridge = Math.max(0, Math.round(st.merge_gap_s / step));
  const ids = sc.riders.map((r) => r.id);
  const pairKey = (a: string, b: string) => `${a}\u0000${b}`;
  const inZone = new Map<string, boolean[]>();
  const pairs = new Map<string, PairData>();
  let intervals: Interval[] = [];
  for (const f of ids) {
    for (const ld of ids) {
      if (f === ld) continue;
      const A = tracks.get(f)!;
      const B = tracks.get(ld)!;
      const valid = grid.map((_, i) => A.valid[i]! && B.valid[i]!);
      if (!valid.some(Boolean)) {
        inZone.set(pairKey(f, ld), grid.map(() => false));
        continue;
      }
      const pd: PairData = { gap: [], dist: [], sigma: [], latSep: [], latReliable: [], valid };
      const zone = grid.map((_, i) => {
        const gap = B.s[i]! - A.s[i]!;
        const dist = Math.hypot(B.x[i]! - A.x[i]!, B.y[i]! - A.y[i]!);
        const sa = Math.hypot(A.vx[i]!, A.vy[i]!);
        const sb = Math.hypot(B.vx[i]!, B.vy[i]!);
        const sigma = Math.sqrt(A.acc[i]! ** 2 + B.acc[i]! ** 2 + (st.timestamp_tolerance_s * Math.max(sa, sb)) ** 2);
        const moving = sa > MOVING_SPEED_MS && sb > MOVING_SPEED_MS;
        const sameDir = A.vx[i]! * B.vx[i]! + A.vy[i]! * B.vy[i]! > 0;
        const latReliable = A.acc[i]! <= st.lateral_accuracy_max_m && B.acc[i]! <= st.lateral_accuracy_max_m;
        const latSep = Math.abs(B.offset[i]! - A.offset[i]!);
        const latClear = latReliable && latSep > st.lateral_separation_m;
        pd.gap.push(gap);
        pd.dist.push(dist);
        pd.sigma.push(sigma);
        pd.latSep.push(latSep);
        pd.latReliable.push(latReliable);
        return valid[i]! && moving && sameDir && gap > 0 && gap <= L && !latClear && dist <= L + 3 * sigma;
      });
      inZone.set(pairKey(f, ld), zone);
      pairs.set(pairKey(f, ld), pd);
      for (const [i0, i1] of runs(zone, bridge)) intervals.push(classify(f, ld, i0, i1, grid, A, pd, st));
    }
  }
  intervals = mergeInconclusive(intervals, grid, bridge, tracks, pairs, st);
  intervals.sort((a, b) => grid[a.i0]! - grid[b.i0]! || (a.follower < b.follower ? -1 : a.follower > b.follower ? 1 : 0));

  const encounters: EncounterRaw[] = [];
  ids.forEach((a, ai) => {
    for (const b of ids.slice(ai + 1)) {
      const A = tracks.get(a)!;
      const B = tracks.get(b)!;
      const valid = grid.map((_, i) => A.valid[i]! && B.valid[i]!);
      if (!valid.some(Boolean)) continue;
      const zAB = inZone.get(pairKey(a, b))!;
      const zBA = inZone.get(pairKey(b, a))!;
      const dist = grid.map((_, i) => Math.hypot(B.x[i]! - A.x[i]!, B.y[i]! - A.y[i]!));
      const gap = grid.map((_, i) => B.s[i]! - A.s[i]!);
      const masks: Record<EncounterType, boolean[]> = {
        separate_course_section: [],
        stationary: [],
        opposite_direction: [],
        lateral_separation: [],
      };
      grid.forEach((_, i) => {
        const near = valid[i]! && dist[i]! <= L && !zAB[i] && !zBA[i];
        const sigma = Math.sqrt(A.acc[i]! ** 2 + B.acc[i]! ** 2);
        const sep = near && Math.abs(gap[i]!) > L + 3 * sigma;
        const rest = near && !sep;
        const moving = Math.hypot(A.vx[i]!, A.vy[i]!) > MOVING_SPEED_MS && Math.hypot(B.vx[i]!, B.vy[i]!) > MOVING_SPEED_MS;
        const sameDir = A.vx[i]! * B.vx[i]! + A.vy[i]! * B.vy[i]! > 0;
        const latReliable = A.acc[i]! <= st.lateral_accuracy_max_m && B.acc[i]! <= st.lateral_accuracy_max_m;
        const latClear = latReliable && Math.abs(B.offset[i]! - A.offset[i]!) > st.lateral_separation_m;
        masks.separate_course_section.push(sep);
        masks.stationary.push(rest && !moving);
        masks.opposite_direction.push(rest && moving && !sameDir);
        masks.lateral_separation.push(rest && moving && sameDir && latClear);
      });
      for (const type of Object.keys(masks) as EncounterType[]) {
        for (const [i0, i1] of runs(masks[type], bridge)) {
          const v: number[] = [];
          for (let i = i0; i <= i1; i++) if (valid[i]) v.push(i);
          encounters.push({
            a,
            b,
            type,
            i0,
            i1,
            duration: (i1 - i0 + 1) * step,
            minDist: minOf(finite(v.map((i) => dist[i]!))),
            medianGap: median(v.map((i) => Math.abs(gap[i]!))),
          });
        }
      }
    }
  });
  encounters.sort((x, y) => grid[x.i0]! - grid[y.i0]! || (x.a < y.a ? -1 : x.a > y.a ? 1 : 0));

  const limitations = [
    `Positions are GNSS estimates; accuracy values are treated as 1-σ horizontal error (or ${fmtG(st.assumed_accuracy_m)} m where not reported).`,
    'Course positions come from projecting each sample onto the course line; riders far from the line or on overlapping sections can be mis-placed.',
    `Device clocks are assumed aligned within ±${fmtG(st.timestamp_tolerance_s)} s; this is included in the gap uncertainty.`,
    `Lateral separation is used only when both riders report accuracy ≤ ${fmtG(st.lateral_accuracy_max_m)} m.`,
    `Positions are interpolated onto a ${fmtG(step)} s grid; gaps longer than ${fmtG(st.max_interpolation_gap_s)} s are treated as missing, and a ${fmtG(st.smoothing_s)} s rolling median reduces jitter.`,
    'Aerodynamic benefit is not estimated. Road layout, obstacles, marshal instructions, and other context are not known to this analysis.',
    'Settings are configurable and the bundled values are illustrative; they are not the rules of any particular event or governing body.',
  ];

  const f = (v: number, nd = 2) => (Number.isFinite(v) ? round(v, nd) : null);
  const events: DraftEvent[] = intervals.map((iv, n) => ({
    id: `E${String(n + 1).padStart(3, '0')}`,
    follower: iv.follower,
    leader: iv.leader,
    classification: iv.classification,
    flagged: iv.classification === 'potential_drafting',
    order_uncertain: iv.orderUncertain,
    start_s: round(grid[iv.i0]!, 1),
    end_s: round(grid[iv.i1]!, 1),
    duration_s: round(iv.duration, 1),
    counted_s: round(iv.counted, 1),
    exempt_s: round(iv.exempt, 1),
    missing_s: round(iv.missing, 1),
    started_by: iv.startedBy,
    ended_by: iv.endedBy,
    gap_median_m: f(iv.gapMedian),
    gap_min_m: f(iv.gapMin),
    gap_max_m: f(iv.gapMax),
    sigma_median_m: f(iv.sigmaMedian),
    probability_in_zone: f(iv.conf, 3),
    quality: iv.quality,
    lateral_used: iv.latUsed,
    course_start_m: f(iv.courseM[0], 1),
    course_end_m: f(iv.courseM[1], 1),
    limitations: iv.limitations,
    series: iv.series,
  }));
  const counts: Partial<Record<Classification, number>> = {};
  for (const e of events) counts[e.classification] = (counts[e.classification] ?? 0) + 1;
  const encCounts: Partial<Record<EncounterType, number>> = {};
  for (const e of encounters) encCounts[e.type] = (encCounts[e.type] ?? 0) + 1;
  const stride = Math.max(1, Math.ceil(course.xy.length / MAX_COURSE_POINTS_OUT));
  const xyOut = [...course.xy.filter((_, i) => i % stride === 0), course.xy[course.xy.length - 1]!];

  return {
    meta: {
      app_version: APP_VERSION,
      drafting_model_version: DRAFT_MODEL_VERSION,
      format: FORMAT_ID,
      disclaimer: DISCLAIMER,
      settings: st,
      settings_note: 'Settings are user-configurable; bundled values are illustrative, not official rules.',
    },
    scenario: {
      name: sc.name,
      description: sc.description,
      illustrative: sc.illustrative,
      source: sc.source,
      content_hash: sc.contentHash,
      start_time: sc.startTime,
      duration_s: round(grid[grid.length - 1]! - grid[0]!, 1),
      t_min_s: round(grid[0]!, 1),
      t_max_s: round(grid[grid.length - 1]!, 1),
      course: { name: sc.courseName, length_m: round(course.lengthM, 1), xy: xyOut.map(([x, y]) => [round(x, 1), round(y, 1)]) },
      riders,
    },
    limitations,
    counts,
    events,
    encounters: encounters.map((e) => ({
      rider_a: e.a,
      rider_b: e.b,
      type: e.type,
      start_s: round(grid[e.i0]!, 1),
      end_s: round(grid[e.i1]!, 1),
      duration_s: round(e.duration, 1),
      min_distance_m: f(e.minDist),
      median_course_gap_m: f(e.medianGap, 1),
    })),
    encounter_counts: encCounts,
  };
}
