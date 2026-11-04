// Course loading: GPX parsing (browser DOMParser), distance, resampling, smoothing and gradient.
// TypeScript port of backend/vector_pacing/course.py (same constants and pipeline):
//  1. Read <trk>/<trkseg>/<trkpt> (or <rte>/<rtept>) ignoring namespaces; segments are concatenated.
//  2. Reject invalid coordinates; drop consecutive duplicates closer than 5 cm.
//  3. Interpolate missing elevation by distance (flat course if < 2 points have elevation).
//  4. Resample onto a uniform grid (step = max(5 m, length / 2000)).
//  5. Smooth elevation with a centred ~100 m moving average (edges repeated).
//  6. Grade = Δelevation / step, clipped to ±25 %.
import type { CourseInfo } from '../types';
import { EngineError, hashText, interp, round, roundAll } from './util';

export const EARTH_RADIUS_M = 6_371_008.8;
const DUPLICATE_TOLERANCE_M = 0.05;
export const MIN_COURSE_LENGTH_M = 200;
export const MAX_COURSE_LENGTH_M = 300_000;
const MAX_POINTS = 200_000;
export const MAX_GPX_BYTES = 10 * 1024 * 1024;
const MIN_STEP_M = 5;
const TARGET_STEPS = 2000;
const SMOOTHING_WINDOW_M = 100;
const MAX_ABS_GRADE = 0.25;

/** Course on a uniform distance grid: `distance_m`/`elevation_m` have n + 1 entries, `grade` n. */
export interface CourseModel {
  name: string;
  source: string;
  distance_m: number[];
  elevation_m: number[];
  grade: number[];
  step_m: number;
  raw_point_count: number;
  content_hash: string;
  warnings: string[];
}

export interface RawPoint {
  lat: number;
  lon: number;
  ele: number | null;
}

const rad = (d: number) => (d * Math.PI) / 180;

export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = rad(lat1);
  const p2 = rad(lat2);
  const a = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin((rad(lon2) - rad(lon1)) / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(Math.min(1, Math.max(0, a))));
}

const kids = (el: Element, name: string) => Array.from(el.children).filter((c) => c.localName === name);
const childText = (el: Element, name: string) => {
  const c = kids(el, name)[0];
  return c ? (c.textContent ?? '').trim() : null;
};

function parsePoint(el: Element, index: number): RawPoint {
  const latS = el.getAttribute('lat');
  const lonS = el.getAttribute('lon');
  if (latS === null) throw new EngineError(`Point ${index + 1} is missing its lat attribute.`);
  if (lonS === null) throw new EngineError(`Point ${index + 1} is missing its lon attribute.`);
  const lat = Number(latS);
  const lon = Number(lonS);
  if (latS.trim() === '' || lonS.trim() === '' || Number.isNaN(lat) || Number.isNaN(lon)) {
    throw new EngineError(`Point ${index + 1} has a non-numeric latitude/longitude.`);
  }
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    throw new EngineError(`Point ${index + 1} has out-of-range coordinates (${lat}, ${lon}).`);
  }
  const eleS = childText(el, 'ele');
  let ele: number | null = eleS ? Number(eleS) : null;
  if (ele !== null && (!Number.isFinite(ele) || ele < -500 || ele > 9000)) ele = null;
  return { lat, lon, ele };
}

/** Parse GPX text into raw points. Needs DOMParser, so it runs on the main thread (not in the worker). */
export function parseGpxPoints(text: string): { name: string | null; points: RawPoint[]; warnings: string[] } {
  if (text.length === 0) throw new EngineError('The file is empty.');
  if (text.length > MAX_GPX_BYTES) throw new EngineError('The file is larger than 10 MB.');
  if (/<!DOCTYPE|<!ENTITY/i.test(text)) {
    throw new EngineError('The file uses forbidden XML features (entities/DTDs) and was rejected.');
  }
  const doc = new DOMParser().parseFromString(text, 'application/xml');
  if (doc.getElementsByTagName('parsererror').length > 0 || !doc.documentElement) {
    throw new EngineError('The file is not well-formed XML, so it cannot be read as GPX.');
  }
  const root = doc.documentElement;
  if (root.localName !== 'gpx') throw new EngineError('The file is XML but not GPX (root element is not <gpx>).');

  let name: string | null = null;
  const segments: Element[][] = [];
  for (const trk of kids(root, 'trk')) {
    name = name || childText(trk, 'name');
    for (const seg of kids(trk, 'trkseg')) {
      const pts = kids(seg, 'trkpt');
      if (pts.length) segments.push(pts);
    }
  }
  const warnings: string[] = [];
  if (segments.length === 0) {
    for (const rte of kids(root, 'rte')) {
      name = name || childText(rte, 'name');
      const pts = kids(rte, 'rtept');
      if (pts.length) segments.push(pts);
    }
    if (segments.length) warnings.push('No track found; the route (<rte>) points were used instead.');
  }
  if (segments.length === 0) throw new EngineError('No track or route points were found in the GPX file.');
  if (segments.length > 1) {
    warnings.push(`${segments.length} track segments were joined in file order; gaps between them count as distance.`);
  }
  if (!name) {
    const meta = kids(root, 'metadata')[0];
    name = meta ? childText(meta, 'name') : null;
  }
  const elements = segments.flat();
  if (elements.length > MAX_POINTS) throw new EngineError(`The course has more than ${MAX_POINTS.toLocaleString('en-US')} points.`);
  return { name, points: elements.map(parsePoint), warnings };
}

/** Turn raw points into a resampled course. Pure (no DOM), so it is unit-testable and worker-safe. */
export function buildCourse(
  points: RawPoint[],
  opts: { name: string; source: string; contentHash: string; warnings?: string[] },
): CourseModel {
  const warnings = [...(opts.warnings ?? [])];
  if (points.length < 2) throw new EngineError('A course needs at least two points.');
  const kept: RawPoint[] = [points[0]!];
  let dropped = 0;
  for (let i = 1; i < points.length; i++) {
    const p = points[i - 1]!;
    const q = points[i]!;
    if (haversineM(p.lat, p.lon, q.lat, q.lon) > DUPLICATE_TOLERANCE_M) kept.push(q);
    else dropped++;
  }
  if (dropped) warnings.push(`${dropped} duplicate point(s) were removed.`);
  if (kept.length < 2) throw new EngineError('All points are at the same location; the course has zero length.');

  const cum = [0];
  for (let i = 1; i < kept.length; i++) {
    const p = kept[i - 1]!;
    const q = kept[i]!;
    cum.push(cum[i - 1]! + haversineM(p.lat, p.lon, q.lat, q.lon));
  }
  const length = cum[cum.length - 1]!;
  if (length < MIN_COURSE_LENGTH_M) {
    throw new EngineError(`The course is only ${length.toFixed(0)} m long; at least ${MIN_COURSE_LENGTH_M} m is required.`);
  }
  if (length > MAX_COURSE_LENGTH_M) {
    throw new EngineError(`The course is ${(length / 1000).toFixed(0)} km long; the limit is ${MAX_COURSE_LENGTH_M / 1000} km.`);
  }

  let ele: number[];
  const withEle = kept.map((p, i) => [cum[i]!, p.ele] as const).filter((x): x is readonly [number, number] => x[1] !== null);
  if (withEle.length < 2) {
    warnings.push('The file has no usable elevation data; the course is treated as flat.');
    ele = cum.map(() => 0);
  } else {
    if (withEle.length < kept.length) {
      warnings.push(`${kept.length - withEle.length} point(s) had no elevation; values were interpolated by distance.`);
    }
    const xp = withEle.map((x) => x[0]);
    const fp = withEle.map((x) => x[1]);
    ele = cum.map((d) => interp(d, xp, fp));
  }

  let step = Math.max(MIN_STEP_M, length / TARGET_STEPS);
  const n = Math.max(1, Math.round(length / step));
  step = length / n;
  const grid = Array.from({ length: n + 1 }, (_, i) => (i === n ? length : (length * i) / n));
  let eleGrid = grid.map((d) => interp(d, cum, ele));
  let window = Math.max(1, Math.round(SMOOTHING_WINDOW_M / step));
  if (window % 2 === 0) window += 1;
  if (window > 1 && n + 1 > window) {
    const h = (window - 1) / 2;
    const src = eleGrid;
    eleGrid = src.map((_, i) => {
      let s = 0;
      for (let k = i - h; k <= i + h; k++) s += src[Math.min(src.length - 1, Math.max(0, k))]!;
      return s / window;
    });
  }
  let grade = eleGrid.slice(1).map((e, i) => (e - eleGrid[i]!) / step);
  const clipped = grade.filter((g) => Math.abs(g) > MAX_ABS_GRADE).length;
  if (clipped) {
    warnings.push(`${clipped} step(s) exceeded ±25% gradient after smoothing and were clipped.`);
    grade = grade.map((g) => Math.min(MAX_ABS_GRADE, Math.max(-MAX_ABS_GRADE, g)));
    const e0 = eleGrid[0]!;
    eleGrid = [e0];
    for (const g of grade) eleGrid.push(eleGrid[eleGrid.length - 1]! + g * step);
  }
  return {
    name: opts.name,
    source: opts.source,
    distance_m: grid,
    elevation_m: eleGrid,
    grade,
    step_m: step,
    raw_point_count: points.length,
    content_hash: opts.contentHash,
    warnings,
  };
}

export function parseGpx(text: string, source: string): CourseModel {
  const { name, points, warnings } = parseGpxPoints(text);
  return buildCourse(points, { name: (name || 'Uploaded course').slice(0, 120), source, contentHash: hashText(text), warnings });
}

export function elevationGain(ele: number[]): number {
  let g = 0;
  for (let i = 1; i < ele.length; i++) g += Math.max(0, ele[i]! - ele[i - 1]!);
  return g;
}

export function elevationLoss(ele: number[]): number {
  let g = 0;
  for (let i = 1; i < ele.length; i++) g += Math.max(0, ele[i - 1]! - ele[i]!);
  return g;
}

/** Display/export summary of a course (same shape the old API returned). */
export function courseInfo(c: CourseModel): CourseInfo {
  return {
    name: c.name,
    source: c.source,
    content_hash: c.content_hash,
    length_m: round(c.distance_m[c.distance_m.length - 1]!, 1),
    elevation_gain_m: round(elevationGain(c.elevation_m), 1),
    elevation_loss_m: round(elevationLoss(c.elevation_m), 1),
    step_m: round(c.step_m, 3),
    n_steps: c.grade.length,
    raw_point_count: c.raw_point_count,
    min_grade_pct: round(Math.min(...c.grade) * 100, 2),
    max_grade_pct: round(Math.max(...c.grade) * 100, 2),
    warnings: c.warnings,
    profile: {
      distance_m: roundAll(c.distance_m, 1),
      elevation_m: roundAll(c.elevation_m, 2),
      grade_pct: roundAll(
        c.grade.map((g) => g * 100),
        2,
      ),
    },
  };
}
