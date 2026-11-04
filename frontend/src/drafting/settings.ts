import type { DraftingSettings, ExemptSection } from './types';

export interface DField {
  key: Exclude<keyof DraftingSettings, 'exempt_sections'>;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  help: string;
  advanced?: boolean;
}

// Bounds mirror DraftingSettingsIn in backend/vector_pacing/drafting_api.py.
export const DFIELDS: DField[] = [
  { key: 'zone_length_m', label: 'Zone length', unit: 'm', min: 1, max: 50, step: 0.5, help: 'Longitudinal distance behind a rider that counts as following.' },
  { key: 'allowed_pass_s', label: 'Allowed passing time', unit: 's', min: 0, max: 300, step: 1, help: 'Time a rider may spend in the zone while overtaking.' },
  { key: 'drop_back_s', label: 'Drop-back allowance', unit: 's', min: 0, max: 300, step: 1, help: 'Time an overtaken rider has to leave the zone.' },
  { key: 'lateral_separation_m', label: 'Lateral separation', unit: 'm', min: 0.5, max: 10, step: 0.1, help: 'Riders further apart sideways are not following (only used with precise data).' },
  { key: 'lateral_accuracy_max_m', label: 'Accuracy needed for lateral use', unit: 'm', min: 0.1, max: 5, step: 0.1, help: 'Lateral separation is used only when both riders report accuracy at or below this.', advanced: true },
  { key: 'assumed_accuracy_m', label: 'Assumed accuracy', unit: 'm', min: 0.1, max: 50, step: 0.1, help: 'Used where the data has no accuracy value.', advanced: true },
  { key: 'timestamp_tolerance_s', label: 'Clock misalignment tolerance', unit: 's', min: 0, max: 5, step: 0.1, help: 'Assumed device clock offset; adds speed × tolerance to the gap uncertainty.', advanced: true },
  { key: 'max_interpolation_gap_s', label: 'Max interpolation gap', unit: 's', min: 1, max: 30, step: 1, help: 'Longer gaps between samples are treated as missing data.', advanced: true },
  { key: 'merge_gap_s', label: 'Merge gap', unit: 's', min: 0, max: 30, step: 1, help: 'Brief exits from the zone (or missing data) up to this long do not split an interval.', advanced: true },
  { key: 'smoothing_s', label: 'Smoothing window', unit: 's', min: 1, max: 30, step: 1, help: 'Rolling median applied to positions to reduce GNSS jitter.', advanced: true },
  { key: 'analysis_step_s', label: 'Analysis step', unit: 's', min: 0.5, max: 5, step: 0.5, help: 'Common time grid used to synchronise riders.', advanced: true },
];

export interface DForm {
  values: Record<string, string>;
  exempt: string; // "start-end label" per line
}

export function formatExempt(sections: ExemptSection[]): string {
  return sections.map((s) => `${s.start_m}-${s.end_m}${s.label ? ' ' + s.label : ''}`).join('\n');
}

export function formFromSettings(s: DraftingSettings): DForm {
  return {
    values: Object.fromEntries(DFIELDS.map((f) => [f.key, String(s[f.key])])),
    exempt: formatExempt(s.exempt_sections),
  };
}

export function parseExempt(text: string): { sections: ExemptSection[]; error?: string } {
  const sections: ExemptSection[] = [];
  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
  if (lines.length > 20) return { sections, error: 'At most 20 exempt sections.' };
  for (const [i, line] of lines.entries()) {
    const m = /^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(.*)$/.exec(line);
    if (!m) return { sections, error: `Line ${i + 1}: use "start-end label", e.g. "3950-4085 Turnaround".` };
    const start = Number(m[1]);
    const end = Number(m[2]);
    if (!(end > start)) return { sections, error: `Line ${i + 1}: end must be greater than start.` };
    sections.push({ start_m: start, end_m: end, label: (m[3] ?? '').slice(0, 60) });
  }
  return { sections };
}

export function validateDraft(form: DForm): { errors: Record<string, string>; settings: DraftingSettings | null } {
  const errors: Record<string, string> = {};
  const out: Record<string, number> = {};
  for (const f of DFIELDS) {
    const raw = (form.values[f.key] ?? '').trim();
    const n = Number(raw);
    if (raw === '' || !Number.isFinite(n)) errors[f.key] = 'Enter a number.';
    else if (n < f.min || n > f.max) errors[f.key] = `Must be between ${f.min} and ${f.max} ${f.unit}.`;
    else out[f.key] = n;
  }
  const ex = parseExempt(form.exempt);
  if (ex.error) errors.exempt = ex.error;
  if (Object.keys(errors).length) return { errors, settings: null };
  return { errors, settings: { ...(out as Omit<DraftingSettings, 'exempt_sections'>), exempt_sections: ex.sections } };
}
