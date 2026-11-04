// Rider / environment / optimizer settings: field definitions, validation and request mapping.
// Bounds mirror the backend's pydantic schema (backend/vector_pacing/api.py).

export type Group = 'rider' | 'environment' | 'baseline' | 'optimizer';

export interface FieldDef {
  key: string;
  group: Group;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  defaultValue: number;
  integer?: boolean;
  help: string;
}

export const FIELDS: FieldDef[] = [
  { key: 'cp_w', group: 'rider', label: 'Critical power (CP)', unit: 'W', min: 50, max: 700, step: 1, defaultValue: 280, help: 'Highest power the model treats as sustainable without drawing on W′.' },
  { key: 'w_prime_kj', group: 'rider', label: 'W′ capacity', unit: 'kJ', min: 1, max: 60, step: 0.1, defaultValue: 20, help: 'Finite work capacity above CP. Typical trained cyclists: 10–30 kJ.' },
  { key: 'rider_mass_kg', group: 'rider', label: 'Rider mass', unit: 'kg', min: 30, max: 200, step: 0.1, defaultValue: 72, help: 'Body mass including clothing.' },
  { key: 'bike_mass_kg', group: 'rider', label: 'Bike + equipment mass', unit: 'kg', min: 3, max: 40, step: 0.1, defaultValue: 8.5, help: 'Bike, bottles and other equipment.' },
  { key: 'cda_m2', group: 'rider', label: 'Drag area (CdA)', unit: 'm²', min: 0.1, max: 0.8, step: 0.005, defaultValue: 0.28, help: 'Aerodynamic drag area. Road hoods ≈ 0.32, TT position ≈ 0.22–0.26.' },
  { key: 'crr', group: 'rider', label: 'Rolling resistance (Crr)', unit: '', min: 0.001, max: 0.03, step: 0.0005, defaultValue: 0.004, help: 'Tyre/road rolling resistance coefficient. Good road tyres ≈ 0.003–0.005.' },
  { key: 'drivetrain_efficiency', group: 'rider', label: 'Drivetrain efficiency', unit: '0–1', min: 0.8, max: 1, step: 0.001, defaultValue: 0.975, help: 'Fraction of crank power that reaches the wheel.' },
  { key: 'air_density', group: 'environment', label: 'Air density', unit: 'kg/m³', min: 0.7, max: 1.5, step: 0.005, defaultValue: 1.225, help: 'Sea level at 15 °C ≈ 1.225. Still air is assumed (no wind).' },
  { key: 'max_speed_kmh', group: 'environment', label: 'Speed limit', unit: 'km/h', min: 20, max: 130, step: 1, defaultValue: 80, help: 'Rider brakes to stay at or below this speed.' },
  { key: 'coast_speed_kmh', group: 'environment', label: 'Coasting threshold', unit: 'km/h', min: 20, max: 130, step: 1, defaultValue: 60, help: 'On descents where gravity alone reaches this speed, the rider stops pedalling.' },
  { key: 'power_w', group: 'baseline', label: 'Baseline constant power', unit: 'W', min: 10, max: 1500, step: 1, defaultValue: 270, help: 'Used only when the baseline mode is "manual".' },
  { key: 'n_segments', group: 'optimizer', label: 'Pacing segments', unit: 'count', min: 2, max: 30, step: 1, defaultValue: 12, integer: true, help: 'Course is split into this many terrain-aware segments, one power target each.' },
  { key: 'min_power_pct_cp', group: 'optimizer', label: 'Minimum segment power', unit: '% of CP', min: 10, max: 100, step: 1, defaultValue: 60, help: 'Lower bound for any segment power target.' },
  { key: 'max_power_pct_cp', group: 'optimizer', label: 'Maximum segment power', unit: '% of CP', min: 100, max: 250, step: 1, defaultValue: 130, help: 'Upper bound for any segment power target.' },
];

export type BaselineMode = 'auto' | 'manual';

export interface FormState {
  values: Record<string, string>;
  baselineMode: BaselineMode;
}

export function defaultForm(): FormState {
  return {
    values: Object.fromEntries(FIELDS.map((f) => [f.key, String(f.defaultValue)])),
    baselineMode: 'auto',
  };
}

export interface RequestParams {
  rider: Record<string, number>;
  environment: Record<string, number>;
  baseline: { mode: BaselineMode; power_w?: number };
  optimizer: Record<string, number>;
}

export type Errors = Record<string, string>;

export function validate(form: FormState): { errors: Errors; params: RequestParams | null } {
  const errors: Errors = {};
  const nums: Record<string, number> = {};
  for (const f of FIELDS) {
    if (f.key === 'power_w' && form.baselineMode !== 'manual') continue;
    const raw = (form.values[f.key] ?? '').trim();
    const n = Number(raw);
    if (raw === '' || !Number.isFinite(n)) {
      errors[f.key] = 'Enter a number.';
    } else if (n < f.min || n > f.max) {
      errors[f.key] = `Must be between ${f.min} and ${f.max}${f.unit && f.unit !== 'count' ? ' ' + f.unit : ''}.`;
    } else if (f.integer && !Number.isInteger(n)) {
      errors[f.key] = 'Must be a whole number.';
    } else {
      nums[f.key] = n;
    }
  }
  if (nums.coast_speed_kmh !== undefined && nums.max_speed_kmh !== undefined && nums.coast_speed_kmh > nums.max_speed_kmh) {
    errors.coast_speed_kmh = 'Must not exceed the speed limit.';
  }
  if (Object.keys(errors).length > 0) return { errors, params: null };
  const pick = (g: Group) =>
    Object.fromEntries(FIELDS.filter((f) => f.group === g && f.key in nums).map((f) => [f.key, nums[f.key] as number]));
  const params: RequestParams = {
    rider: pick('rider'),
    environment: pick('environment'),
    baseline: form.baselineMode === 'manual' ? { mode: 'manual', power_w: nums.power_w } : { mode: 'auto' },
    optimizer: pick('optimizer'),
  };
  return { errors, params };
}

/** Identity of a run's inputs; a result is stale when this differs from the current inputs. */
export function inputsKey(courseId: string | null, params: RequestParams | null): string | null {
  if (!courseId || !params) return null;
  return JSON.stringify({ courseId, params });
}
