// Shapes returned by the FastAPI backend (see backend/vector_pacing/api.py). SI units unless named otherwise.

export interface CourseProfile {
  distance_m: number[];
  elevation_m: number[];
  grade_pct: number[];
}

export interface CourseInfo {
  name: string;
  source: string;
  content_hash: string;
  length_m: number;
  elevation_gain_m: number;
  elevation_loss_m: number;
  step_m: number;
  n_steps: number;
  raw_point_count: number;
  min_grade_pct: number;
  max_grade_pct: number;
  warnings: string[];
  profile: CourseProfile;
}

export type CourseRef = { kind: 'demo' } | { kind: 'gpx'; gpx_text: string; filename: string };

export interface ResultSeries {
  distance_m: number[];
  elapsed_s: number[];
  planned_power_w: number[];
  power_w: number[];
  speed_kmh: number[];
  w_bal_j: number[];
}

export interface SimResult {
  total_time_s: number;
  average_speed_kmh: number;
  average_power_w: number;
  normalized_power_w: number;
  min_w_bal_j: number;
  final_w_bal_j: number;
  feasible: boolean;
  violations: string[];
  coasting_fraction: number;
  speed_capped_fraction: number;
  series: ResultSeries;
}

export interface BaselineResult extends SimResult {
  power_w: number;
  selection: string;
}

export interface Meta {
  app_version: string;
  model_version: string;
  disclaimer: string;
  inputs: Record<string, unknown>;
  derived: { min_power_w: number; max_power_w: number };
}

export interface SegmentRow {
  index: number;
  start_m: number;
  end_m: number;
  mean_grade_pct: number;
  baseline_power_w: number;
  baseline_time_s: number;
  planned_power_w?: number;
  applied_avg_power_w?: number;
  optimized_time_s?: number;
}

export type OptStatus = 'improved' | 'no_improvement' | 'failed';

export interface Optimization {
  status: OptStatus;
  message: string;
  notes: string[];
  time_saved_s: number | null;
  solver: {
    method: string;
    success: boolean;
    message: string;
    iterations: number;
    evaluations: number;
    elapsed_s: number;
    max_evaluations: number;
    max_wall_time_s: number;
    min_reportable_improvement_s: number;
  };
  segments: SegmentRow[];
  result: SimResult | null;
}

export interface SimulateResponse {
  meta: Meta;
  course: CourseInfo;
  baseline: BaselineResult;
}

export interface OptimizeResponse extends SimulateResponse {
  optimization: Optimization;
}
