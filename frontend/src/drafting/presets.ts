// Drafting settings defaults and illustrative presets (formerly served by GET /api/drafting/presets).
import type { DraftingSettings, PresetsResponse } from './types';

export const DEFAULT_SETTINGS: DraftingSettings = {
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
};

export const PRESETS: PresetsResponse = {
  defaults: DEFAULT_SETTINGS,
  presets: {
    illustrative_standard: {
      label: 'Illustrative — standard (10 m / 20 s)',
      description: 'Example values for demonstration. Not taken from any governing body.',
      settings: { zone_length_m: 10, allowed_pass_s: 20, drop_back_s: 20, lateral_separation_m: 2 },
    },
    illustrative_long_zone: {
      label: 'Illustrative — long zone (15 m / 30 s)',
      description: 'Example of a longer zone with more time to pass. Not taken from any governing body.',
      settings: { zone_length_m: 15, allowed_pass_s: 30, drop_back_s: 30, lateral_separation_m: 2 },
    },
    illustrative_short_zone: {
      label: 'Illustrative — short zone (7 m / 15 s)',
      description: 'Example of a shorter zone and stricter passing time. Not taken from any governing body.',
      settings: { zone_length_m: 7, allowed_pass_s: 15, drop_back_s: 15, lateral_separation_m: 1.5 },
    },
  },
  demo_exempt_sections: [{ start_m: 3950, end_m: 4085, label: 'Turnaround (illustrative)' }],
  note: 'All presets are illustrative examples. No governing-body presets are bundled because their current rules could not be verified for this release; enter your event\'s rules manually.',
};
