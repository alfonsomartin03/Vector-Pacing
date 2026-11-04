import type { DraftEvent, RiderInfo } from './types';
import { CLASS_COLOR, CLASS_LABEL, clock } from './format';

interface Props {
  riders: RiderInfo[];
  events: DraftEvent[];
  tMin: number;
  tMax: number;
  startTime: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const ROW = 22;
const LEFT = 56;
const WIDTH = 1000;

export function Timeline({ riders, events, tMin, tMax, startTime, selectedId, onSelect }: Props) {
  const height = riders.length * ROW + 34;
  const span = Math.max(1, tMax - tMin);
  const x = (t: number) => LEFT + ((t - tMin) / span) * (WIDTH - LEFT - 10);
  const row = new Map(riders.map((r, i) => [r.id, i]));
  const tickStep = span > 3600 ? 900 : span > 1200 ? 300 : 120;
  const ticks: number[] = [];
  for (let t = Math.ceil(tMin / tickStep) * tickStep; t <= tMax; t += tickStep) ticks.push(t);

  return (
    <div className="svg-scroll" role="region" aria-label="Timeline of intervals by following rider (scrollable)" tabIndex={0}>
      <svg viewBox={`0 0 ${WIDTH} ${height}`} className="timeline" role="group" aria-label="Interval timeline">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={4} y2={height - 26} stroke="#e4e7ec" />
            <text x={x(t)} y={height - 10} fontSize={11} textAnchor="middle" fill="#344054">
              {clock(t, startTime).replace(' UTC', '')}
            </text>
          </g>
        ))}
        {riders.map((r, i) => (
          <g key={r.id}>
            <text x={LEFT - 8} y={i * ROW + 19} fontSize={12} textAnchor="end" fill="#101828">
              {r.id}
            </text>
            <rect x={x(r.start_s)} y={i * ROW + 9} width={Math.max(1, x(r.end_s) - x(r.start_s))} height={8} fill="#e4e7ec" rx={2} />
          </g>
        ))}
        {events.map((e) => {
          const i = row.get(e.follower) ?? 0;
          const sel = e.id === selectedId;
          const label = `${e.id}: ${e.follower} behind ${e.leader}, ${CLASS_LABEL[e.classification]}, ${Math.round(e.duration_s)} s from ${clock(e.start_s, startTime)}`;
          return (
            <g
              key={e.id}
              role="button"
              tabIndex={0}
              aria-label={label}
              aria-pressed={sel}
              className="tl-event"
              onClick={() => onSelect(e.id)}
              onKeyDown={(ev) => {
                if (ev.key === 'Enter' || ev.key === ' ') {
                  ev.preventDefault();
                  onSelect(e.id);
                }
              }}
            >
              <title>{label}</title>
              <rect
                x={x(e.start_s) - 1}
                y={i * ROW + 4}
                width={Math.max(4, x(e.end_s) - x(e.start_s) + 2)}
                height={18}
                rx={3}
                fill={CLASS_COLOR[e.classification]}
                stroke={sel ? '#101828' : 'none'}
                strokeWidth={sel ? 2.5 : 0}
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}
