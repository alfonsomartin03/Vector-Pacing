import { useMemo, useState } from 'react';
import type { DraftEvent } from './types';
import { CLASS_COLOR, CLASS_LABEL } from './format';

interface Props {
  courseXy: [number, number][];
  events: DraftEvent[];
  selected: DraftEvent | null;
  onSelect: (id: string) => void;
}

const W = 1000;

function path(xs: (number | null)[], ys: (number | null)[], sx: (v: number) => number, sy: (v: number) => number): string {
  let d = '';
  let pen = false;
  xs.forEach((xv, i) => {
    const yv = ys[i];
    if (xv === null || yv === null || yv === undefined) {
      pen = false;
      return;
    }
    d += `${pen ? 'L' : 'M'}${sx(xv).toFixed(1)},${sy(yv).toFixed(1)}`;
    pen = true;
  });
  return d;
}

export function CourseMap({ courseXy, events, selected, onSelect }: Props) {
  const [zoom, setZoom] = useState(false);
  const bounds = useMemo(() => {
    let pts: [number, number][] = courseXy;
    if (zoom && selected) {
      pts = selected.series.follower_x
        .map((x, i) => [x, selected.series.follower_y[i]] as [number | null, number | null | undefined])
        .filter((p): p is [number, number] => p[0] !== null && p[1] !== null && p[1] !== undefined);
      if (!pts.length) pts = courseXy;
    }
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const pad = zoom ? 40 : 30;
    return { x0: Math.min(...xs) - pad, x1: Math.max(...xs) + pad, y0: Math.min(...ys) - pad, y1: Math.max(...ys) + pad };
  }, [courseXy, zoom, selected]);

  // Equal scale on both axes (no distortion); height follows the aspect ratio within limits.
  const scale = W / (bounds.x1 - bounds.x0);
  const H = Math.min(420, Math.max(140, (bounds.y1 - bounds.y0) * scale));
  const s = Math.min(scale, H / (bounds.y1 - bounds.y0));
  const ox = (W - (bounds.x1 - bounds.x0) * s) / 2;
  const oy = (H - (bounds.y1 - bounds.y0) * s) / 2;
  const sx = (x: number) => ox + (x - bounds.x0) * s;
  const sy = (y: number) => H - (oy + (y - bounds.y0) * s);
  const course = path(courseXy.map((p) => p[0]), courseXy.map((p) => p[1]), sx, sy);
  const shown = events.filter((e) => e.classification === 'potential_drafting' || e.classification === 'inconclusive' || e === selected);
  const start = courseXy[0];

  return (
    <figure className="course-map">
      <figcaption className="chart-title">
        Course view (local metres, north up){zoom && selected ? ` — zoomed to ${selected.id}` : ''}
        {selected && (
          <button type="button" className="btn btn-small inline-btn" onClick={() => setZoom((z) => !z)}>
            {zoom ? 'Show whole course' : `Zoom to ${selected.id}`}
          </button>
        )}
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Course map with ${shown.length} highlighted intervals. Selected: ${selected ? selected.id : 'none'}.`}>
        <path d={course} fill="none" stroke="#98a2b3" strokeWidth={zoom ? 3 : 2} />
        {start && <circle cx={sx(start[0])} cy={sy(start[1])} r={5} fill="#101828"><title>Start</title></circle>}
        {shown.map((e) => {
          const sel = e === selected;
          return (
            <path
              key={e.id}
              d={path(e.series.follower_x, e.series.follower_y, sx, sy)}
              fill="none"
              stroke={CLASS_COLOR[e.classification]}
              strokeWidth={sel ? 7 : 4}
              strokeLinecap="round"
              opacity={sel || !selected ? 0.95 : 0.45}
              onClick={() => onSelect(e.id)}
              style={{ cursor: 'pointer' }}
            >
              <title>{`${e.id}: ${e.follower} behind ${e.leader} — ${CLASS_LABEL[e.classification]}`}</title>
            </path>
          );
        })}
      </svg>
      <p className="help">
        Highlighted: potential-drafting and inconclusive intervals (follower&apos;s path, with 15 s context). Click a path or a
        timeline bar to inspect it.
      </p>
    </figure>
  );
}
