import { buildCsvExport, buildJsonExport, exportBaseName } from '../export';
import { formatDelta, formatDuration } from '../format';
import { defaultForm, inputsKey, validate } from '../params';
import { buildRows } from '../components/Charts';
import { course, optimizeResponse } from './fixtures';

describe('format', () => {
  it('formats durations', () => {
    expect(formatDuration(59.6)).toBe('1:00');
    expect(formatDuration(3125.4)).toBe('52:05');
    expect(formatDuration(3725)).toBe('1:02:05');
    expect(formatDuration(Number.NaN)).toBe('—');
  });
  it('formats signed deltas', () => {
    expect(formatDelta(-61.2)).toBe('−61.2 s (−1:01)');
    expect(formatDelta(3)).toBe('+3.0 s');
    expect(formatDelta(0)).toBe('±0.0 s');
  });
});

describe('validate', () => {
  it('accepts defaults and maps groups', () => {
    const { errors, params } = validate(defaultForm());
    expect(errors).toEqual({});
    expect(params?.rider.cp_w).toBe(280);
    expect(params?.baseline).toEqual({ mode: 'auto' });
    expect(params?.optimizer.n_segments).toBe(12);
  });
  it('flags empty, non-numeric, out-of-range and non-integer values', () => {
    const f = defaultForm();
    f.values.cp_w = '';
    f.values.cda_m2 = 'abc';
    f.values.w_prime_kj = '500';
    f.values.n_segments = '3.5';
    const { errors, params } = validate(f);
    expect(params).toBeNull();
    expect(errors.cp_w).toMatch(/number/);
    expect(errors.cda_m2).toMatch(/number/);
    expect(errors.w_prime_kj).toMatch(/between 1 and 60 kJ/);
    expect(errors.n_segments).toMatch(/whole/);
  });
  it('checks coast speed against speed limit', () => {
    const f = defaultForm();
    f.values.coast_speed_kmh = '90';
    expect(validate(f).errors.coast_speed_kmh).toMatch(/speed limit/);
  });
  it('only requires baseline power in manual mode', () => {
    const f = defaultForm();
    f.values.power_w = '';
    expect(validate(f).params).not.toBeNull();
    f.baselineMode = 'manual';
    expect(validate(f).errors.power_w).toBeDefined();
    f.values.power_w = '250';
    expect(validate(f).params?.baseline).toEqual({ mode: 'manual', power_w: 250 });
  });
  it('produces a key that changes when inputs change', () => {
    const a = inputsKey('demo:x', validate(defaultForm()).params);
    const f = defaultForm();
    f.values.cp_w = '281';
    expect(inputsKey('demo:x', validate(f).params)).not.toBe(a);
    expect(inputsKey('other:y', validate(defaultForm()).params)).not.toBe(a);
    expect(inputsKey(null, validate(defaultForm()).params)).toBeNull();
  });
});

describe('export', () => {
  it('builds JSON with parameters, units, versions and comparison', () => {
    const doc = JSON.parse(buildJsonExport(optimizeResponse, new Date('2026-01-01T00:00:00Z')));
    expect(doc.schema).toBe('vector-pacing-export/1');
    expect(doc.model_version).toBe('vp-model-1.0');
    expect(doc.units.power).toBe('W');
    expect(doc.inputs.rider.cp_w).toBe(280);
    expect(doc.comparison.time_saved_s).toBe(12);
    expect(doc.course.profile.distance_m).toHaveLength(5);
  });
  it('builds CSV with baseline and optimized columns', () => {
    const csv = buildCsvExport(optimizeResponse).trim().split('\n');
    expect(csv[0]).toMatch(/model vp-model-1.0/);
    const header = csv[3]!.split(',');
    expect(header).toContain('baseline_w_bal_kj');
    expect(header).toContain('optimized_planned_power_w');
    expect(csv).toHaveLength(4 + 4);
    expect(csv[4]!.split(',')).toHaveLength(header.length);
  });
  it('names files by course and kind', () => {
    expect(exportBaseName(optimizeResponse)).toBe('vector-pacing_fixture-course_comparison');
  });
});

describe('chart rows', () => {
  it('covers course length and keeps step changes', () => {
    const rows = buildRows(course, optimizeResponse.baseline, optimizeResponse.optimization.result);
    expect(rows[0]!.km).toBe(0);
    expect(rows.at(-1)!.km).toBe(4);
    expect(rows.map((r) => r.oP)).toContain(270);
  });
});
