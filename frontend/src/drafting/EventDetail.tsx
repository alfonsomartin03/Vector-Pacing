import { useMemo } from 'react';
import { Area, ComposedChart, CartesianGrid, Legend, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { DraftEvent } from './types';
import { CLASS_COLOR, CLASS_LABEL, clock, fmtM } from './format';

interface Props {
  event: DraftEvent;
  zoneLength: number;
  startTime: string | null;
}

const ENDED: Record<string, string> = {
  follower_passed: 'Follower moved ahead (passed)',
  gap_opened: 'Gap opened beyond the zone',
  zone_conditions_ended: 'Zone conditions ended (direction, lateral or consistency)',
  data_unavailable: 'Usable data stopped (dropout or end of track)',
};

export function EventDetail({ event: e, zoneLength, startTime }: Props) {
  const rows = useMemo(
    () =>
      e.series.t_s.map((t, i) => {
        const g = e.series.gap_m[i] ?? null;
        const sg = e.series.sigma_m[i] ?? null;
        return {
          t,
          gap: g,
          band: g !== null && sg !== null ? [g - sg, g + sg] : null,
          dist: e.series.distance_m[i] ?? null,
          lat: e.series.lateral_sep_m[i] ?? null,
        };
      }),
    [e],
  );
  const hasLat = e.series.lateral_sep_m.some((v) => v !== null);
  return (
    <section className="card event-detail" aria-labelledby="event-h">
      <h3 id="event-h">
        {e.id}: {e.follower} behind {e.leader}{' '}
        <span className="badge" style={{ background: CLASS_COLOR[e.classification], color: '#fff' }}>
          {CLASS_LABEL[e.classification]}
        </span>
      </h3>
      {e.order_uncertain && <p className="alert alert-warn">Rider order is uncertain for this interval.</p>}
      <dl className="stats two-col">
        <dt>Start – end</dt>
        <dd>
          {clock(e.start_s, startTime)} – {clock(e.end_s, startTime)}
        </dd>
        <dt>Duration (counted)</dt>
        <dd>
          {e.duration_s.toFixed(0)} s ({e.counted_s.toFixed(0)} s counted{e.exempt_s ? `, ${e.exempt_s.toFixed(0)} s exempt` : ''})
        </dd>
        <dt>Estimated gap (median, min–max)</dt>
        <dd>
          {fmtM(e.gap_median_m)} ({fmtM(e.gap_min_m)} – {fmtM(e.gap_max_m)})
        </dd>
        <dt>Gap uncertainty (1σ)</dt>
        <dd>±{fmtM(e.sigma_median_m)}</dd>
        <dt>Probability inside zone</dt>
        <dd>{e.probability_in_zone === null ? '—' : `${Math.round(e.probability_in_zone * 100)} %`}</dd>
        <dt>Data quality</dt>
        <dd>
          {e.quality}
          {e.missing_s ? `, ${e.missing_s.toFixed(0)} s missing` : ''}
        </dd>
        <dt>Lateral data used</dt>
        <dd>{e.lateral_used ? 'yes' : 'no'}</dd>
        <dt>Started / ended</dt>
        <dd>
          {e.started_by === 'overtaken' ? 'Just overtaken by the leader' : 'Approached from behind'} /{' '}
          {ENDED[e.ended_by] ?? e.ended_by}
        </dd>
        <dt>Course position</dt>
        <dd>
          {e.course_start_m === null ? '—' : `${(e.course_start_m / 1000).toFixed(2)}–${((e.course_end_m ?? 0) / 1000).toFixed(2)} km`}
        </dd>
      </dl>
      <figure className="chart">
        <figcaption className="chart-title">Gap to {e.leader} along the course (m) with ±1σ band; zone edge at {zoneLength} m</figcaption>
        <div style={{ width: '100%', height: 220 }} aria-hidden="true">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 4, right: 12, bottom: 4, left: 4 }}>
              <CartesianGrid stroke="#e4e7ec" vertical={false} />
              <XAxis dataKey="t" type="number" domain={['dataMin', 'dataMax']} tickFormatter={(t: number) => clock(t, startTime).replace(' UTC', '')} tick={{ fontSize: 11, fill: '#344054' }} />
              <YAxis tick={{ fontSize: 11, fill: '#344054' }} width={40} allowDecimals={false} />
              <ReferenceArea x1={e.start_s} x2={e.end_s} fill={CLASS_COLOR[e.classification]} fillOpacity={0.08} />
              <Area dataKey="band" name="±1σ" stroke="none" fill="#98a2b3" fillOpacity={0.35} isAnimationActive={false} connectNulls={false} />
              <ReferenceLine y={zoneLength} stroke="#b42318" strokeDasharray="5 3" label={{ value: 'zone edge', position: 'insideTopRight', fontSize: 11, fill: '#b42318' }} />
              <ReferenceLine y={0} stroke="#101828" />
              <Line dataKey="gap" name="Course gap (m)" stroke="#101828" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
              <Line dataKey="dist" name="Straight-line distance (m)" stroke="#3d5a80" strokeDasharray="4 3" dot={false} isAnimationActive={false} connectNulls={false} />
              {hasLat && <Line dataKey="lat" name="Lateral separation (m)" stroke="#2e6b4f" dot={false} isAnimationActive={false} connectNulls={false} />}
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Tooltip
                labelFormatter={(t) => clock(Number(t), startTime)}
                formatter={(v: unknown) => (Array.isArray(v) ? `${Number(v[0]).toFixed(1)}–${Number(v[1]).toFixed(1)} m` : typeof v === 'number' ? `${v.toFixed(1)} m` : String(v))}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </figure>
      <h4>Data-quality notes and limitations</h4>
      <ul className="limitations">
        {e.limitations.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </section>
  );
}
