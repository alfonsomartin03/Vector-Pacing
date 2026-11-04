import type { Classification, EncounterType } from './types';

export const CLASS_LABEL: Record<Classification, string> = {
  potential_drafting: 'Potential drafting',
  inconclusive: 'Inconclusive (data quality)',
  overtake: 'Overtake within allowance',
  drop_back: 'Drop-back within allowance',
  brief_encounter: 'Brief encounter',
  exempt: 'Exempt section',
};

// Colours chosen for ≥ 4.5:1 contrast of white text / against white background.
export const CLASS_COLOR: Record<Classification, string> = {
  potential_drafting: '#b42318',
  inconclusive: '#8a5a00',
  overtake: '#2e6b4f',
  drop_back: '#3d5a80',
  brief_encounter: '#5b6472',
  exempt: '#6b5b95',
};

export const ENC_LABEL: Record<EncounterType, string> = {
  separate_course_section: 'Separate course section',
  opposite_direction: 'Opposite direction',
  lateral_separation: 'Side by side (lateral separation)',
  stationary: 'Stationary',
};

/** Format seconds since scenario start as clock time if a start time is known, else as +m:ss. */
export function clock(t: number, startIso: string | null): string {
  if (startIso) {
    const d = new Date(Date.parse(startIso) + t * 1000);
    if (!Number.isNaN(d.getTime())) return d.toISOString().slice(11, 19) + ' UTC';
  }
  const m = Math.floor(t / 60);
  const s = Math.round(t % 60);
  return `+${m}:${String(s).padStart(2, '0')}`;
}

export const fmtM = (v: number | null, d = 1) => (v === null ? '—' : `${v.toFixed(d)} m`);
