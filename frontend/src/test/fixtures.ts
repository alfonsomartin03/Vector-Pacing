import type { CourseInfo, OptimizeResponse, SimResult } from '../types';

const n = 4;
export const course: CourseInfo = {
  name: 'Fixture course',
  source: 'demo',
  content_hash: 'abc123',
  length_m: 4000,
  elevation_gain_m: 40,
  elevation_loss_m: 0,
  step_m: 1000,
  n_steps: n,
  raw_point_count: 5,
  min_grade_pct: 0,
  max_grade_pct: 2,
  warnings: [],
  profile: { distance_m: [0, 1000, 2000, 3000, 4000], elevation_m: [0, 10, 20, 30, 40], grade_pct: [1, 1, 1, 1] },
};

const series = (p: number[]): SimResult['series'] => ({
  distance_m: [0, 1000, 2000, 3000],
  elapsed_s: [100, 200, 300, 400],
  planned_power_w: p,
  power_w: p,
  speed_kmh: [36, 36, 36, 36],
  w_bal_j: [19000, 18000, 17000, 16000],
});

const sim = (time: number, p: number[]): SimResult => ({
  total_time_s: time,
  average_speed_kmh: 36,
  average_power_w: 280,
  normalized_power_w: 282,
  min_w_bal_j: 16000,
  final_w_bal_j: 16000,
  feasible: true,
  violations: [],
  coasting_fraction: 0,
  speed_capped_fraction: 0,
  series: series(p),
});

export const optimizeResponse: OptimizeResponse = {
  meta: {
    app_version: '1.0.0',
    model_version: 'vp-model-1.0',
    disclaimer: 'Model estimates only.',
    inputs: { rider: { cp_w: 280 } },
    derived: { min_power_w: 168, max_power_w: 364 },
  },
  course,
  baseline: { ...sim(400, [286, 286, 286, 286]), power_w: 286, selection: 'Auto: 286 W' },
  optimization: {
    status: 'improved',
    message: 'The variable-power plan is estimated to be 12.0 s faster than the baseline under this model.',
    notes: ['Local optimization'],
    time_saved_s: 12,
    solver: {
      method: 'SLSQP',
      success: true,
      message: 'ok',
      iterations: 10,
      evaluations: 100,
      elapsed_s: 0.5,
      max_evaluations: 4000,
      max_wall_time_s: 30,
      min_reportable_improvement_s: 0.5,
    },
    segments: [
      { index: 0, start_m: 0, end_m: 2000, mean_grade_pct: 1, baseline_power_w: 286, baseline_time_s: 200, planned_power_w: 300, applied_avg_power_w: 300, optimized_time_s: 194 },
      { index: 1, start_m: 2000, end_m: 4000, mean_grade_pct: 1, baseline_power_w: 286, baseline_time_s: 200, planned_power_w: 270, applied_avg_power_w: 270, optimized_time_s: 194 },
    ],
    result: sim(388, [300, 300, 270, 270]),
  },
};
