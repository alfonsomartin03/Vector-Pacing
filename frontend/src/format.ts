export function formatDuration(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds)) return '—';
  const s = Math.round(Math.abs(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const body = h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
  return totalSeconds < 0 ? `−${body}` : body;
}

/** Signed seconds difference, e.g. "−61.2 s (−1:01)". Negative = faster. */
export function formatDelta(seconds: number): string {
  const sign = seconds < 0 ? '−' : seconds > 0 ? '+' : '±';
  const abs = Math.abs(seconds);
  const clock = abs >= 60 ? ` (${sign}${formatDuration(abs)})` : '';
  return `${sign}${abs.toFixed(1)} s${clock}`;
}

export const km = (m: number, digits = 2) => `${(m / 1000).toFixed(digits)} km`;
export const kj = (j: number, digits = 1) => `${(j / 1000).toFixed(digits)} kJ`;
export const pct = (fraction: number, digits = 0) => `${(fraction * 100).toFixed(digits)} %`;
