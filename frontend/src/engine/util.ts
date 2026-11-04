// Small numeric helpers shared by the browser engine (ports of the NumPy calls used by the Python reference).

/** A user-facing error raised by the in-browser engine (invalid file, infeasible inputs, solver failure). */
export class EngineError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'EngineError';
  }
}

/** Round to `nd` decimals; non-finite values pass through. */
export function round(x: number, nd: number): number {
  if (!Number.isFinite(x)) return x;
  const f = 10 ** nd;
  return Math.round(x * f) / f;
}

export const roundAll = (xs: ArrayLike<number>, nd: number): number[] => Array.from(xs, (x) => round(x, nd));

/** Like numpy.interp: linear interpolation with `xp` increasing, values clamped at the ends. */
export function interp(x: number, xp: ArrayLike<number>, fp: ArrayLike<number>): number {
  const n = xp.length;
  if (x <= xp[0]!) return fp[0]!;
  if (x >= xp[n - 1]!) return fp[n - 1]!;
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xp[mid]! <= x) lo = mid;
    else hi = mid;
  }
  const x0 = xp[lo]!;
  const x1 = xp[hi]!;
  return fp[lo]! + ((fp[hi]! - fp[lo]!) * (x - x0)) / (x1 - x0);
}

/** numpy.searchsorted for a sorted array. */
export function searchsorted(a: ArrayLike<number>, v: number, side: 'left' | 'right' = 'left'): number {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (side === 'left' ? a[mid]! < v : a[mid]! <= v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Min/max without spreading (safe for very long arrays). Empty → ±Infinity. */
export function minOf(a: ArrayLike<number>): number {
  let m = Infinity;
  for (let i = 0; i < a.length; i++) if (a[i]! < m) m = a[i]!;
  return m;
}
export function maxOf(a: ArrayLike<number>): number {
  let m = -Infinity;
  for (let i = 0; i < a.length; i++) if (a[i]! > m) m = a[i]!;
  return m;
}

/** Median of the finite values (NaN if none). */
export function median(values: ArrayLike<number>): number {
  const v = Array.from(values).filter(Number.isFinite).sort((a, b) => a - b);
  if (v.length === 0) return Number.NaN;
  const m = v.length >> 1;
  return v.length % 2 ? v[m]! : (v[m - 1]! + v[m]!) / 2;
}

/** Error function (Abramowitz & Stegun 7.1.26, |error| < 1.5e-7). */
export function erf(x: number): number {
  const s = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * ax);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-ax * ax);
  return s * y;
}

/** Standard normal CDF. */
export const phi = (z: number) => 0.5 * (1 + erf(z / Math.SQRT2));

/** Short, stable, non-cryptographic content hash (cyrb53) shown as 14 hex characters. */
function cyrb53(codes: Iterable<number>): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (const ch of codes) {
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (h2 >>> 0).toString(16).padStart(8, '0').slice(0, 6) + (h1 >>> 0).toString(16).padStart(8, '0');
}

export function hashText(text: string): string {
  return cyrb53((function* () {
    for (let i = 0; i < text.length; i++) yield text.charCodeAt(i);
  })());
}

export function hashBytes(chunks: Uint8Array[]): string {
  return cyrb53((function* () {
    for (const c of chunks) {
      yield* c;
      yield 0x100; // separator
    }
  })());
}

/** Python-style "%g" for the short numbers used in messages (10 → "10", 0.2 → "0.2"). */
export const fmtG = (x: number) => String(Number(x.toPrecision(6)));

export const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
