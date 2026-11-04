import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError } from '../api';
import { downloadText } from '../export';
import { analyzeDrafting, fetchPresets } from './api';
import { CourseMap } from './CourseMap';
import { EventDetail } from './EventDetail';
import { buildDraftCsv, buildDraftJson, draftBaseName } from './export';
import { CLASS_COLOR, CLASS_LABEL, ENC_LABEL, clock, fmtM } from './format';
import { DFIELDS, formatExempt, formFromSettings, validateDraft, type DForm } from './settings';
import { Timeline } from './Timeline';
import type { Classification, DraftingResponse, DraftingSettings, PresetsResponse, ScenarioRef } from './types';

const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const ORDER: Classification[] = ['potential_drafting', 'inconclusive', 'overtake', 'drop_back', 'brief_encounter', 'exempt'];

type Run =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'error'; message: string }
  | { status: 'done'; key: string; data: DraftingResponse };

const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';
const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : 'Unexpected error.');
const scenarioId = (s: ScenarioRef | null) =>
  s ? (s.kind === 'demo' ? 'demo' : `upload:${[...s.riders, ...(s.course ? [s.course] : [])].map((f) => `${f.name}:${f.size}:${f.lastModified}`).join('|')}`) : null;

export function DraftingView() {
  const [presets, setPresets] = useState<PresetsResponse | null>(null);
  const [presetError, setPresetError] = useState<string | null>(null);
  const [scenario, setScenario] = useState<ScenarioRef | null>(null);
  const [form, setForm] = useState<DForm | null>(null);
  const [presetKey, setPresetKey] = useState('custom');
  const [run, setRun] = useState<Run>({ status: 'idle' });
  const [last, setLast] = useState<Extract<Run, { status: 'done' }> | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const [presetAttempt, setPresetAttempt] = useState(0);

  useEffect(() => {
    let alive = true;
    fetchPresets()
      .then((p) => {
        if (!alive) return;
        setPresets(p);
        setForm((f) => f ?? formFromSettings(p.defaults));
      })
      .catch((e: unknown) => {
        if (alive) setPresetError(msg(e));
      });
    return () => {
      alive = false;
    };
  }, [presetAttempt]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const { errors, settings } = useMemo(
    () => (form ? validateDraft(form) : { errors: {}, settings: null }),
    [form],
  );
  const currentKey = settings && scenario ? JSON.stringify({ s: scenarioId(scenario), settings }) : null;
  const result = last?.data ?? null;
  const stale = last !== null && last.key !== currentKey;
  const running = run.status === 'running';

  const execute = useCallback(async (sc: ScenarioRef, st: DraftingSettings) => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setRun({ status: 'running' });
    try {
      const data = await analyzeDrafting(sc, st, ctrl.signal);
      const done = { status: 'done' as const, key: JSON.stringify({ s: scenarioId(sc), settings: st }), data };
      setRun(done);
      setLast(done);
      const first = data.events.find((e) => e.flagged) ?? data.events.find((e) => e.classification === 'inconclusive');
      setSelectedId(first?.id ?? null);
    } catch (e) {
      if (isAbort(e)) setRun({ status: 'idle' });
      else setRun({ status: 'error', message: msg(e) });
    }
  }, []);

  const startDemo = () => {
    if (!presets || !form) return;
    const nextForm = { ...form, exempt: form.exempt.trim() ? form.exempt : formatExempt(presets.demo_exempt_sections) };
    setForm(nextForm);
    setScenario({ kind: 'demo' });
    setUploadError(null);
    setLast(null);
    const v = validateDraft(nextForm);
    if (v.settings) void execute({ kind: 'demo' }, v.settings);
  };

  const onFiles = (files: File[]) => {
    setUploadError(null);
    const riders = files.filter((f) => /\.fit$/i.test(f.name));
    const courses = files.filter((f) => /\.gpx$/i.test(f.name));
    const other = files.filter((f) => !riders.includes(f) && !courses.includes(f));
    if (other.length) return setUploadError(`"${other[0]!.name}" is not a .fit or .gpx file.`);
    if (riders.length < 2) return setUploadError('Select at least two .fit files (one per rider).');
    if (courses.length > 1) return setUploadError('Select at most one .gpx course file.');
    const big = files.find((f) => f.size > MAX_UPLOAD_BYTES);
    if (big) return setUploadError(`"${big.name}" is larger than 20 MB.`);
    const course = courses[0] ?? null;
    const sc: ScenarioRef = { kind: 'upload', riders, course };
    setScenario(sc);
    setLast(null);
    if (settings) void execute(sc, settings);
  };

  const applyPreset = (key: string) => {
    setPresetKey(key);
    const p = presets?.presets[key];
    if (!p || !form) return;
    setForm({
      ...form,
      values: { ...form.values, ...Object.fromEntries(Object.entries(p.settings).map(([k, v]) => [k, String(v)])) },
    });
  };

  const reset = () => {
    abortRef.current?.abort();
    setScenario(null);
    setLast(null);
    setRun({ status: 'idle' });
    setSelectedId(null);
    setUploadError(null);
    setPresetKey('custom');
    if (presets) setForm(formFromSettings(presets.defaults));
  };

  const doExport = (kind: 'json' | 'csv') => {
    if (!result || stale) return;
    const base = draftBaseName(result);
    if (kind === 'json') downloadText(`${base}.json`, buildDraftJson(result, new Date()), 'application/json');
    else downloadText(`${base}.csv`, buildDraftCsv(result), 'text/csv');
  };

  const visibleEvents = result
    ? result.events.filter((e) => showAll || e.classification === 'potential_drafting' || e.classification === 'inconclusive')
    : [];
  const selected = result?.events.find((e) => e.id === selectedId) ?? null;

  if (presetError) {
    return (
      <section className="panel">
        <div className="alert alert-error" role="alert">
          <p>Could not load drafting settings: {presetError}</p>
          <button
            type="button"
            className="btn btn-small"
            onClick={() => {
              setPresetError(null);
              setPresetAttempt((n) => n + 1);
            }}
          >
            Retry
          </button>
        </div>
      </section>
    );
  }
  if (!presets || !form) return <p className="status">Loading drafting settings…</p>;

  return (
    <>
      <section className="panel" aria-labelledby="draft-data-h">
        <h2 id="draft-data-h">1. Rider position data</h2>
        <p className="help">
          Detects <strong>potential</strong> drafting in events where drafting is restricted (e.g. individual time trials). It is
          a review aid: it does not confirm rules violations or issue penalties. Independent of the pacing optimizer.
        </p>
        <div className="course-actions">
          <button type="button" className="btn btn-primary" onClick={startDemo} disabled={running}>
            Try multi-rider demo
          </button>
          <span className="or">or</span>
          <label className={`btn btn-secondary file-btn${running ? ' is-disabled' : ''}`}>
            Upload rides (.fit)…
            <input
              ref={fileRef}
              type="file"
              multiple
              accept=".fit,.gpx"
              className="visually-hidden-input"
              disabled={running}
              onClick={(e) => {
                e.currentTarget.value = '';
              }}
              onChange={(e) => {
                const fs = Array.from(e.target.files ?? []);
                if (fs.length) onFiles(fs);
              }}
            />
          </label>
          {scenario && (
            <button type="button" className="btn btn-quiet" onClick={reset}>
              Reset
            </button>
          )}
        </div>
        <p className="help">
          Select one <code>.fit</code> activity per rider (as recorded by the head unit or exported from Garmin
          Connect, Strava, Wahoo…), plus optionally the course <code>.gpx</code>. Without a course file the first
          rider&apos;s track is used as the course line. See docs/DRAFTING.md.
        </p>
        {uploadError && (
          <div className="alert alert-error" role="alert">
            <p>{uploadError}</p>
            <button type="button" className="btn btn-small" onClick={() => fileRef.current?.click()}>
              Choose other files
            </button>
          </div>
        )}
        {!scenario && !uploadError && <p className="empty">No rider data loaded. Start with the bundled illustrative scenario.</p>}
      </section>

      {scenario && (
        <div className="workspace">
          <section className="panel settings-panel" aria-labelledby="draft-settings-h">
            <h2 id="draft-settings-h">2. Detection settings</h2>
            <div className="alert alert-warn">
              <p>
                <strong>Illustrative settings.</strong> These are not the rules of any governing body or event. {presets.note}
              </p>
            </div>
            <form className="params" onSubmit={(e) => e.preventDefault()} noValidate aria-label="Drafting detection settings">
              <div className="field">
                <label htmlFor="d-preset">Preset</label>
                <select id="d-preset" value={presetKey} onChange={(e) => applyPreset(e.target.value)} disabled={running}>
                  <option value="custom">Custom (edit below)</option>
                  {Object.entries(presets.presets).map(([k, p]) => (
                    <option key={k} value={k}>
                      {p.label}
                    </option>
                  ))}
                </select>
                {presetKey !== 'custom' && <p className="help">{presets.presets[presetKey]?.description}</p>}
              </div>
              {[false, true].map((adv) => {
                const body = DFIELDS.filter((f) => !!f.advanced === adv).map((f) => {
                  const id = `d-${f.key}`;
                  const err = errors[f.key];
                  return (
                    <div key={f.key} className={`field${err ? ' field-error' : ''}`}>
                      <label htmlFor={id}>
                        {f.label} <span className="unit">({f.unit})</span>
                      </label>
                      <input
                        id={id}
                        type="number"
                        inputMode="decimal"
                        min={f.min}
                        max={f.max}
                        step={f.step}
                        value={form.values[f.key] ?? ''}
                        disabled={running}
                        aria-invalid={err ? true : undefined}
                        aria-describedby={`${id}-help${err ? ` ${id}-err` : ''}`}
                        onChange={(e) => {
                          setPresetKey('custom');
                          setForm({ ...form, values: { ...form.values, [f.key]: e.target.value } });
                        }}
                      />
                      <p id={`${id}-help`} className="help">
                        {f.help}
                      </p>
                      {err && (
                        <p id={`${id}-err`} className="error-text" role="alert">
                          {err}
                        </p>
                      )}
                    </div>
                  );
                });
                return adv ? (
                  <details key="adv" className="advanced">
                    <summary>Data-quality settings</summary>
                    {body}
                  </details>
                ) : (
                  <fieldset key="rules" disabled={running}>
                    <legend>Event rules (configurable)</legend>
                    {body}
                    <div className={`field${errors.exempt ? ' field-error' : ''}`}>
                      <label htmlFor="d-exempt">Exempt course sections (one per line: start-end label, metres)</label>
                      <textarea
                        id="d-exempt"
                        rows={3}
                        value={form.exempt}
                        aria-invalid={errors.exempt ? true : undefined}
                        aria-describedby="d-exempt-help"
                        onChange={(e) => setForm({ ...form, exempt: e.target.value })}
                      />
                      <p id="d-exempt-help" className="help">
                        Time spent here is not counted (e.g. turnarounds, aid stations, technical sections).
                      </p>
                      {errors.exempt && (
                        <p className="error-text" role="alert">
                          {errors.exempt}
                        </p>
                      )}
                    </div>
                  </fieldset>
                );
              })}
              <button
                type="button"
                className="btn btn-quiet"
                disabled={running}
                onClick={() => {
                  setPresetKey('custom');
                  setForm({ ...formFromSettings(presets.defaults), exempt: scenario.kind === 'demo' ? formatExempt(presets.demo_exempt_sections) : '' });
                }}
              >
                Reset settings to defaults
              </button>
            </form>
          </section>

          <section className="panel results-panel" aria-labelledby="draft-results-h">
            <h2 id="draft-results-h">3. Potential drafting review</h2>
            <div className="run-bar">
              <button type="button" className="btn btn-primary" disabled={!settings || running} onClick={() => settings && void execute(scenario, settings)}>
                {result ? 'Re-run analysis' : 'Run analysis'}
              </button>
              {running && (
                <button type="button" className="btn btn-quiet" onClick={() => abortRef.current?.abort()}>
                  Cancel
                </button>
              )}
            </div>
            <div aria-live="polite">
              {!settings && <p className="alert alert-error">Fix the highlighted settings to run the analysis.</p>}
              {running && (
                <p className="status">
                  <span className="spinner" aria-hidden="true" />
                  Analysing rider positions…
                </p>
              )}
              {run.status === 'error' && (
                <div className="alert alert-error" role="alert">
                  <p>Analysis failed: {run.message}</p>
                  <div className="alert-actions">
                    <button type="button" className="btn btn-small" disabled={!settings} onClick={() => settings && void execute(scenario, settings)}>
                      Retry
                    </button>
                    {scenario.kind === 'upload' && (
                      <button type="button" className="btn btn-small" onClick={() => fileRef.current?.click()}>
                        Choose other files
                      </button>
                    )}
                  </div>
                </div>
              )}
              {stale && !running && (
                <div className="alert alert-warn" role="status">
                  <p>
                    <strong>Results are out of date.</strong> Settings or data changed after this analysis. Re-run to update;
                    export is disabled until then.
                  </p>
                </div>
              )}
            </div>

            {result && (
              <div className={stale ? 'results is-stale' : 'results'} aria-hidden={stale || undefined}>
                <div className="card scenario-card">
                  <h3>
                    {result.scenario.name} {result.scenario.illustrative && <span className="badge badge-warn">Illustrative data</span>}
                  </h3>
                  {result.scenario.description && <p className="help">{result.scenario.description}</p>}
                  <dl className="stats inline">
                    <dt>Riders</dt>
                    <dd>{result.scenario.riders.length}</dd>
                    <dt>Course</dt>
                    <dd>{(result.scenario.course.length_m / 1000).toFixed(2)} km</dd>
                    <dt>Window</dt>
                    <dd>
                      {clock(result.scenario.t_min_s, result.scenario.start_time)} – {clock(result.scenario.t_max_s, result.scenario.start_time)}
                    </dd>
                  </dl>
                </div>

                <ul className="count-chips" aria-label="Interval counts">
                  {ORDER.map((c) => (
                    <li key={c} style={{ borderColor: CLASS_COLOR[c] }}>
                      <span className="dot" style={{ background: CLASS_COLOR[c] }} aria-hidden="true" />
                      {CLASS_LABEL[c]}: <strong>{result.counts[c] ?? 0}</strong>
                    </li>
                  ))}
                </ul>
                {(result.counts.potential_drafting ?? 0) === 0 && (
                  <p className="alert alert-ok">No potential drafting found under these settings.</p>
                )}

                <label className="toggle">
                  <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show
                  non-flagged intervals (overtakes, drop-backs, brief encounters, exempt)
                </label>

                <h3 className="section-h">Timeline</h3>
                <Timeline
                  riders={result.scenario.riders}
                  events={visibleEvents}
                  tMin={result.scenario.t_min_s}
                  tMax={result.scenario.t_max_s}
                  startTime={result.scenario.start_time}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                />
                <CourseMap courseXy={result.scenario.course.xy} events={visibleEvents} selected={selected} onSelect={setSelectedId} />

                <div className="table-wrap" tabIndex={0} role="region" aria-label="Intervals table (scrollable)">
                  <table>
                    <caption>Intervals ({visibleEvents.length})</caption>
                    <thead>
                      <tr>
                        <th scope="col">ID (inspect)</th>
                        <th scope="col">Follower → leader</th>
                        <th scope="col">Classification</th>
                        <th scope="col">Start</th>
                        <th scope="col">Duration</th>
                        <th scope="col">Median gap</th>
                        <th scope="col">±1σ</th>
                        <th scope="col">Quality</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleEvents.map((e) => (
                        <tr key={e.id} className={e.id === selectedId ? 'is-selected' : undefined}>
                          <td>
                            <button
                              type="button"
                              className="btn btn-small"
                              aria-pressed={e.id === selectedId}
                              aria-label={`Inspect ${e.id}`}
                              onClick={() => setSelectedId(e.id)}
                            >
                              {e.id}
                            </button>
                          </td>
                          <td>
                            {e.follower} → {e.leader}
                            {e.order_uncertain ? ' (order?)' : ''}
                          </td>
                          <td>
                            <span className="dot" style={{ background: CLASS_COLOR[e.classification] }} aria-hidden="true" />
                            {CLASS_LABEL[e.classification]}
                          </td>
                          <td>{clock(e.start_s, result.scenario.start_time)}</td>
                          <td>{e.duration_s.toFixed(0)} s</td>
                          <td>{fmtM(e.gap_median_m)}</td>
                          <td>{fmtM(e.sigma_median_m)}</td>
                          <td>{e.quality}</td>
                        </tr>
                      ))}
                      {visibleEvents.length === 0 && (
                        <tr>
                          <td colSpan={8}>No intervals to show.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                {selected && <EventDetail event={selected} zoneLength={result.meta.settings.zone_length_m} startTime={result.scenario.start_time} />}

                <details className="solver">
                  <summary>
                    Proximity not treated as following ({result.encounters.length}):{' '}
                    {Object.entries(result.encounter_counts)
                      .map(([k, v]) => `${ENC_LABEL[k as keyof typeof ENC_LABEL]} ${v}`)
                      .join(', ') || 'none'}
                  </summary>
                  <div className="table-wrap" tabIndex={0} role="region" aria-label="Dismissed encounters (scrollable)">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Riders</th>
                          <th scope="col">Reason</th>
                          <th scope="col">Start</th>
                          <th scope="col">Duration</th>
                          <th scope="col">Closest distance</th>
                          <th scope="col">Course gap</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.encounters.map((e, i) => (
                          <tr key={i}>
                            <td>
                              {e.rider_a} / {e.rider_b}
                            </td>
                            <td>{ENC_LABEL[e.type]}</td>
                            <td>{clock(e.start_s, result.scenario.start_time)}</td>
                            <td>{e.duration_s.toFixed(0)} s</td>
                            <td>{fmtM(e.min_distance_m)}</td>
                            <td>{fmtM(e.median_course_gap_m, 0)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>

                <details className="solver">
                  <summary>Data quality by rider</summary>
                  <div className="table-wrap" tabIndex={0} role="region" aria-label="Data quality by rider (scrollable)">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Rider</th>
                          <th scope="col">Samples</th>
                          <th scope="col">Median interval</th>
                          <th scope="col">Max interval</th>
                          <th scope="col">Missing</th>
                          <th scope="col">Accuracy</th>
                          <th scope="col">Max off course</th>
                          <th scope="col">Notes</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.scenario.riders.map((r) => (
                          <tr key={r.id}>
                            <td>{r.label}</td>
                            <td>{r.samples}</td>
                            <td>{r.median_interval_s.toFixed(1)} s</td>
                            <td>{r.max_interval_s.toFixed(1)} s</td>
                            <td>{(r.missing_fraction * 100).toFixed(1)} %</td>
                            <td>
                              {r.accuracy_median_m.toFixed(1)} m{r.accuracy_reported ? '' : ' (assumed)'}
                            </td>
                            <td>{r.max_off_course_m.toFixed(1)} m</td>
                            <td className="wrap">{r.warnings.join(' ') || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>

                <details className="solver" open>
                  <summary>General limitations</summary>
                  <ul>
                    {result.limitations.map((l) => (
                      <li key={l}>{l}</li>
                    ))}
                  </ul>
                </details>
              </div>
            )}

            {result && (
              <div className="export-bar">
                <h3>4. Export findings</h3>
                <button type="button" className="btn btn-secondary" disabled={stale || running} onClick={() => doExport('json')}>
                  Export JSON (full)
                </button>
                <button type="button" className="btn btn-secondary" disabled={stale || running} onClick={() => doExport('csv')}>
                  Export CSV (intervals)
                </button>
                <p className="help">
                  Includes settings, data-quality limitations, supporting gap series, app {result.meta.app_version} and drafting
                  model {result.meta.drafting_model_version}. {result.meta.disclaimer}
                </p>
              </div>
            )}
          </section>
        </div>
      )}
    </>
  );
}
