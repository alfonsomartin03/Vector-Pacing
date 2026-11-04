import { useMemo } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { CourseInfo, SimResult } from '../types';

const MAX_POINTS = 700;
export const COLORS = { baseline: '#3d5a80', optimized: '#b4410b', elevation: '#4d7c6f', grid: '#d9dee5', danger: '#b42318' };

interface Row {
  km: number;
  ele?: number;
  bP?: number;
  oP?: number;
  bV?: number;
  oV?: number;
  bW?: number;
  oW?: number;
}

/** Pick evenly spaced indices, always keeping indices where the value of `keep` changes (power steps). */
function sampleIndices(n: number, keep?: number[]): number[] {
  const stride = Math.max(1, Math.ceil(n / MAX_POINTS));
  const out = new Set<number>();
  for (let i = 0; i < n; i += stride) out.add(i);
  out.add(n - 1);
  if (keep) for (let i = 1; i < n; i++) if (keep[i] !== keep[i - 1]) { out.add(i - 1); out.add(i); }
  return [...out].sort((a, b) => a - b);
}

export function buildRows(course: CourseInfo, baseline: SimResult | null, optimized: SimResult | null): Row[] {
  const n = course.profile.distance_m.length;
  const idx = sampleIndices(n, optimized?.series.planned_power_w);
  return idx.map((i) => {
    const row: Row = { km: (course.profile.distance_m[i] ?? 0) / 1000, ele: course.profile.elevation_m[i] };
    const j = Math.min(i, (baseline?.series.power_w.length ?? 1) - 1); // series have n-1 steps
    if (baseline) {
      row.bP = baseline.series.power_w[j];
      row.bV = baseline.series.speed_kmh[j];
      row.bW = (baseline.series.w_bal_j[j] ?? 0) / 1000;
    }
    if (optimized) {
      row.oP = optimized.series.power_w[j];
      row.oV = optimized.series.speed_kmh[j];
      row.oW = (optimized.series.w_bal_j[j] ?? 0) / 1000;
    }
    return row;
  });
}

const axisProps = { tick: { fontSize: 12, fill: '#344054' }, stroke: '#667085' };
const kmTick = (v: number) => v.toFixed(0);

interface ChartFrameProps {
  title: string;
  description: string;
  children: React.ReactElement;
  height?: number;
}

function ChartFrame({ title, description, children, height = 190 }: ChartFrameProps) {
  return (
    <figure className="chart" aria-label={`${title}. ${description}`}>
      <figcaption className="chart-title">{title}</figcaption>
      <div style={{ width: '100%', height }} aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

function XAxisKm({ show = false, maxKm }: { show?: boolean; maxKm: number }) {
  return (
    <XAxis
      dataKey="km"
      type="number"
      domain={[0, maxKm]}
      tickFormatter={kmTick}
      {...axisProps}
      label={show ? { value: 'Distance (km)', position: 'insideBottom', offset: -4, fontSize: 12, fill: '#344054' } : undefined}
      height={show ? 36 : 24}
    />
  );
}

const niceUp = (step: number) => (v: number) => Math.ceil(v / step) * step;
const niceDown = (step: number) => (v: number) => Math.floor(v / step) * step;

const fmt = (unit: string, digits = 0) => (v: unknown) =>
  typeof v === 'number' ? `${v.toFixed(digits)} ${unit}` : String(v ?? '');

export function Charts({
  course,
  baseline,
  optimized,
}: {
  course: CourseInfo;
  baseline: SimResult | null;
  optimized: SimResult | null;
}) {
  const rows = useMemo(() => buildRows(course, baseline, optimized), [course, baseline, optimized]);
  const maxKm = course.length_m / 1000;
  const lines = (prefix: 'P' | 'V' | 'W', unit: string, digits: number) => (
    <>
      {baseline && (
        <Line type="linear" dataKey={`b${prefix}`} name={`Baseline (${unit})`} stroke={COLORS.baseline} strokeWidth={2} dot={false} isAnimationActive={false} strokeDasharray="6 3" />
      )}
      {optimized && (
        <Line type="linear" dataKey={`o${prefix}`} name={`Optimized (${unit})`} stroke={COLORS.optimized} strokeWidth={2} dot={false} isAnimationActive={false} />
      )}
      <Tooltip formatter={fmt(unit, digits)} labelFormatter={(v) => `${Number(v).toFixed(2)} km`} />
    </>
  );
  const anyNegativeW = (baseline?.min_w_bal_j ?? 0) < 0 || (optimized?.min_w_bal_j ?? 0) < 0;

  return (
    <div className="charts">
      <ChartFrame
        title="Elevation (m)"
        description={`Course elevation from ${course.profile.elevation_m[0]?.toFixed(0)} m; gain ${course.elevation_gain_m.toFixed(0)} m over ${maxKm.toFixed(1)} km.`}
        height={150}
      >
        <AreaChart data={rows} syncId="vp" margin={{ top: 4, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid stroke={COLORS.grid} vertical={false} />
          <XAxisKm maxKm={maxKm} />
          <YAxis {...axisProps} width={48} domain={[niceDown(25), niceUp(25)]} allowDecimals={false} />
          <Area type="linear" dataKey="ele" name="Elevation (m)" stroke={COLORS.elevation} fill="#cfe3dc" isAnimationActive={false} />
          <Tooltip formatter={fmt('m')} labelFormatter={(v) => `${Number(v).toFixed(2)} km`} />
        </AreaChart>
      </ChartFrame>
      {baseline && (
        <>
          <ChartFrame title="Power (W)" description="Applied crank power along the course for each strategy.">
            <LineChart data={rows} syncId="vp" margin={{ top: 4, right: 12, bottom: 0, left: 4 }}>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxisKm maxKm={maxKm} />
              <YAxis {...axisProps} width={48} domain={[0, niceUp(50)]} allowDecimals={false} />
              <Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 12 }} />
              {lines('P', 'W', 0)}
            </LineChart>
          </ChartFrame>
          <ChartFrame title="Speed (km/h)" description="Modelled steady-state speed along the course.">
            <LineChart data={rows} syncId="vp" margin={{ top: 4, right: 12, bottom: 0, left: 4 }}>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxisKm maxKm={maxKm} />
              <YAxis {...axisProps} width={48} domain={[0, niceUp(10)]} allowDecimals={false} />
              {lines('V', 'km/h', 1)}
            </LineChart>
          </ChartFrame>
          <ChartFrame title="W′ balance (kJ)" description="Remaining anaerobic work capacity; below zero means the effort exceeds the model's capacity.">
            <LineChart data={rows} syncId="vp" margin={{ top: 4, right: 12, bottom: 4, left: 4 }}>
              <CartesianGrid stroke={COLORS.grid} vertical={false} />
              <XAxisKm show maxKm={maxKm} />
              <YAxis {...axisProps} width={48} domain={[anyNegativeW ? 'dataMin' : 0, 'dataMax']} tickFormatter={(v: number) => v.toFixed(0)} />
              <ReferenceLine y={0} stroke={COLORS.danger} strokeWidth={1.5} label={{ value: 'empty', position: 'insideBottomRight', fontSize: 11, fill: COLORS.danger }} />
              {lines('W', 'kJ', 2)}
            </LineChart>
          </ChartFrame>
        </>
      )}
    </div>
  );
}
