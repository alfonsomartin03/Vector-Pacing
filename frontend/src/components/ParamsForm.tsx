import { FIELDS, type Errors, type FieldDef, type FormState, type Group } from '../params';

const GROUPS: { id: Group; title: string; note?: string }[] = [
  { id: 'rider', title: 'Rider & bike' },
  { id: 'environment', title: 'Environment', note: 'No wind; constant air density.' },
  { id: 'baseline', title: 'Baseline (constant power)' },
  { id: 'optimizer', title: 'Optimizer' },
];

interface Props {
  form: FormState;
  errors: Errors;
  disabled: boolean;
  onChange: (key: string, value: string) => void;
  onModeChange: (mode: FormState['baselineMode']) => void;
  onReset: () => void;
}

function NumberField({ f, value, error, disabled, onChange }: { f: FieldDef; value: string; error?: string; disabled: boolean; onChange: (v: string) => void }) {
  const id = `f-${f.key}`;
  const unit = f.unit && f.unit !== 'count' ? f.unit : '';
  return (
    <div className={`field${error ? ' field-error' : ''}`}>
      <label htmlFor={id}>
        {f.label}
        {unit && <span className="unit"> ({unit})</span>}
      </label>
      <input
        id={id}
        name={f.key}
        type="number"
        inputMode="decimal"
        min={f.min}
        max={f.max}
        step={f.step}
        value={value}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={`${id}-help${error ? ` ${id}-err` : ''}`}
        onChange={(e) => onChange(e.target.value)}
      />
      <p id={`${id}-help`} className="help">
        {f.help} Range {f.min}–{f.max}.
      </p>
      {error && (
        <p id={`${id}-err`} className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function ParamsForm({ form, errors, disabled, onChange, onModeChange, onReset }: Props) {
  return (
    <form className="params" onSubmit={(e) => e.preventDefault()} aria-label="Simulation settings" noValidate>
      {GROUPS.map((g) => (
        <fieldset key={g.id} disabled={disabled}>
          <legend>{g.title}</legend>
          {g.note && <p className="help">{g.note}</p>}
          {g.id === 'baseline' && (
            <div className="radio-row" role="radiogroup" aria-label="Baseline mode">
              <label>
                <input type="radio" name="baseline-mode" value="auto" checked={form.baselineMode === 'auto'} onChange={() => onModeChange('auto')} />
                Auto — highest feasible constant power
              </label>
              <label>
                <input type="radio" name="baseline-mode" value="manual" checked={form.baselineMode === 'manual'} onChange={() => onModeChange('manual')} />
                Manual
              </label>
            </div>
          )}
          {FIELDS.filter((f) => f.group === g.id && (f.key !== 'power_w' || form.baselineMode === 'manual')).map((f) => (
            <NumberField key={f.key} f={f} value={form.values[f.key] ?? ''} error={errors[f.key]} disabled={disabled} onChange={(v) => onChange(f.key, v)} />
          ))}
        </fieldset>
      ))}
      <button type="button" className="btn btn-quiet" onClick={onReset} disabled={disabled}>
        Reset settings to defaults
      </button>
    </form>
  );
}
