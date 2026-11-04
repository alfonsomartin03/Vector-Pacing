import type { DraftEvent, DraftingResponse, PresetsResponse } from '../drafting/types';

export const presets: PresetsResponse = {
  defaults: {
    zone_length_m: 10,
    lateral_separation_m: 2,
    allowed_pass_s: 20,
    drop_back_s: 20,
    exempt_sections: [],
    smoothing_s: 5,
    max_interpolation_gap_s: 5,
    merge_gap_s: 5,
    timestamp_tolerance_s: 0.2,
    assumed_accuracy_m: 5,
    lateral_accuracy_max_m: 1,
    analysis_step_s: 1,
  },
  presets: {
    illustrative_long_zone: {
      label: 'Illustrative — long zone (15 m / 30 s)',
      description: 'Example. Not taken from any governing body.',
      settings: { zone_length_m: 15, allowed_pass_s: 30, drop_back_s: 30, lateral_separation_m: 2 },
    },
  },
  demo_exempt_sections: [{ start_m: 3950, end_m: 4085, label: 'Turnaround (illustrative)' }],
  note: 'All presets are illustrative examples.',
};

const series = {
  t_s: [100, 101, 102, 103],
  gap_m: [12, 7, 6.5, null],
  sigma_m: [3, 3, 3, null],
  distance_m: [12.1, 7.2, 6.6, null],
  lateral_sep_m: [null, null, null, null],
  follower_x: [0, 10, 20, null],
  follower_y: [0, 0, 1, null],
};

const ev = (id: string, classification: DraftEvent['classification'], follower: string, leader: string): DraftEvent => ({
  id,
  follower,
  leader,
  classification,
  flagged: classification === 'potential_drafting',
  order_uncertain: false,
  start_s: 101,
  end_s: 160,
  duration_s: 60,
  counted_s: 60,
  exempt_s: 0,
  missing_s: 0,
  started_by: 'approached',
  ended_by: 'gap_opened',
  gap_median_m: 6.8,
  gap_min_m: 5.1,
  gap_max_m: 9.9,
  sigma_median_m: 3,
  probability_in_zone: 0.8,
  quality: 'good',
  lateral_used: false,
  course_start_m: 1000,
  course_end_m: 1600,
  limitations: ['Combined gap uncertainty ≈ ±3.0 m (1σ) against a 10 m zone.'],
  series,
  power_anomaly_ids: [],
});

export const draftingResponse: DraftingResponse = {
  meta: {
    app_version: '1.0.0',
    drafting_model_version: 'vp-draft-1.0',
    format: 'vector-pacing-riders/1',
    disclaimer: 'Potential drafting only. Not confirmed rules violations.',
    settings: { ...presets.defaults, exempt_sections: presets.demo_exempt_sections },
    settings_note: 'Bundled values are illustrative.',
  },
  scenario: {
    name: 'Fixture scenario',
    description: 'Synthetic.',
    illustrative: true,
    source: 'demo',
    content_hash: 'h',
    start_time: '2026-06-14T08:00:00Z',
    duration_s: 600,
    t_min_s: 0,
    t_max_s: 600,
    course: { name: 'C', length_m: 2000, xy: [[0, 0], [1000, 0], [2000, 50]] },
    riders: ['R01', 'R02', 'R03'].map((id, i) => ({
      id,
      label: id,
      samples: 500,
      start_s: i * 30,
      end_s: 500 + i * 30,
      median_interval_s: 1,
      irregular_interval_fraction: 0,
      max_interval_s: 1,
      missing_fraction: 0,
      accuracy_median_m: 1.5,
      accuracy_reported: true,
      max_off_course_m: 3,
      warnings: [],
    })),
  },
  limitations: ['Positions are GNSS estimates.'],
  counts: { potential_drafting: 1, overtake: 1 },
  events: [ev('E001', 'potential_drafting', 'R02', 'R01'), ev('E002', 'overtake', 'R03', 'R02')],
  encounters: [
    { rider_a: 'R01', rider_b: 'R03', type: 'opposite_direction', start_s: 300, end_s: 300, duration_s: 1, min_distance_m: 2.1, median_course_gap_m: 1 },
  ],
  encounter_counts: { opposite_direction: 1 },
  power_screen: null,
};

/** A .fit upload response: R02's GPS interval E001 overlaps power anomaly P001. */
export const fitResponse: DraftingResponse = {
  ...draftingResponse,
  scenario: { ...draftingResponse.scenario, name: '3 riders from .fit files', illustrative: false, source: 'R01.fit, R02.fit, R03.fit' },
  events: [{ ...ev('E001', 'potential_drafting', 'R02', 'R01'), power_anomaly_ids: ['P001'] }, ev('E002', 'overtake', 'R03', 'R02')],
  power_screen: {
    version: 'raceguard-matched-sections-1',
    note: 'Within-rider power screen (from RaceGuard).',
    riders: [
      { id: 'R01', has_power: true, speed_from_gps: false, anomaly_count: 0, warnings: [] },
      { id: 'R02', has_power: true, speed_from_gps: false, anomaly_count: 1, warnings: [] },
      { id: 'R03', has_power: false, speed_from_gps: false, anomaly_count: 0, warnings: ['No power recorded; the power screen was skipped.'] },
    ],
    anomalies: [
      {
        id: 'P001', rider: 'R02', start_s: 105, end_s: 155, duration_s: 51, score: 0.8, power_score: 0.76, duration_score: 1,
        average_speed_kmh: 43.2, average_power_w: 269, reference_power_w: 367, deficit_w: 98, peer_count: 146,
        speed_normalised: false, heading_deg: 90, lat: 47, lon: 8.05, distance_m: 1300,
        notes: ['Power was lower than 146 comparable sections from this rider\'s activity.'], gps_event_ids: ['E001'],
      },
    ],
  },
};
