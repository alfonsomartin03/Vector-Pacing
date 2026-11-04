// Shapes returned by /api/drafting/* (see backend/vector_pacing/drafting_api.py).

export type Classification =
  | 'potential_drafting'
  | 'inconclusive'
  | 'overtake'
  | 'drop_back'
  | 'brief_encounter'
  | 'exempt';

export type EncounterType = 'separate_course_section' | 'opposite_direction' | 'lateral_separation' | 'stationary';

export interface ExemptSection {
  start_m: number;
  end_m: number;
  label: string;
}

export interface DraftingSettings {
  zone_length_m: number;
  lateral_separation_m: number;
  allowed_pass_s: number;
  drop_back_s: number;
  exempt_sections: ExemptSection[];
  smoothing_s: number;
  max_interpolation_gap_s: number;
  merge_gap_s: number;
  timestamp_tolerance_s: number;
  assumed_accuracy_m: number;
  lateral_accuracy_max_m: number;
  analysis_step_s: number;
}

export interface Preset {
  label: string;
  description: string;
  settings: Partial<DraftingSettings>;
}

export interface PresetsResponse {
  defaults: DraftingSettings;
  presets: Record<string, Preset>;
  demo_exempt_sections: ExemptSection[];
  note: string;
}

export interface EventSeries {
  t_s: (number | null)[];
  gap_m: (number | null)[];
  sigma_m: (number | null)[];
  distance_m: (number | null)[];
  lateral_sep_m: (number | null)[];
  follower_x: (number | null)[];
  follower_y: (number | null)[];
}

export interface DraftEvent {
  id: string;
  follower: string;
  leader: string;
  classification: Classification;
  flagged: boolean;
  order_uncertain: boolean;
  start_s: number;
  end_s: number;
  duration_s: number;
  counted_s: number;
  exempt_s: number;
  missing_s: number;
  started_by: string;
  ended_by: string;
  gap_median_m: number | null;
  gap_min_m: number | null;
  gap_max_m: number | null;
  sigma_median_m: number | null;
  probability_in_zone: number | null;
  quality: 'good' | 'limited' | 'insufficient';
  lateral_used: boolean;
  course_start_m: number | null;
  course_end_m: number | null;
  limitations: string[];
  series: EventSeries;
}

export interface Encounter {
  rider_a: string;
  rider_b: string;
  type: EncounterType;
  start_s: number;
  end_s: number;
  duration_s: number;
  min_distance_m: number | null;
  median_course_gap_m: number | null;
}

export interface RiderInfo {
  id: string;
  label: string;
  samples: number;
  start_s: number;
  end_s: number;
  median_interval_s: number;
  irregular_interval_fraction: number;
  max_interval_s: number;
  missing_fraction: number;
  accuracy_median_m: number;
  accuracy_reported: boolean;
  max_off_course_m: number;
  warnings: string[];
}

export interface DraftingResponse {
  meta: {
    app_version: string;
    drafting_model_version: string;
    format: string;
    disclaimer: string;
    settings: DraftingSettings;
    settings_note: string;
  };
  scenario: {
    name: string;
    description: string;
    illustrative: boolean;
    source: string;
    content_hash: string;
    start_time: string | null;
    duration_s: number;
    t_min_s: number;
    t_max_s: number;
    course: { name: string; length_m: number; xy: [number, number][] };
    riders: RiderInfo[];
  };
  limitations: string[];
  counts: Partial<Record<Classification, number>>;
  events: DraftEvent[];
  encounters: Encounter[];
  encounter_counts: Partial<Record<EncounterType, number>>;
}

/** Uploaded rides: one .fit activity per rider, plus an optional course line (.gpx). */
export type ScenarioRef = { kind: 'demo' } | { kind: 'upload'; riders: File[]; course: File | null };
