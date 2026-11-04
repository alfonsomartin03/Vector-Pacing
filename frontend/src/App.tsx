import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, fetchDemoCourse, optimize, parseCourseFile, simulate } from './api';
import { Charts } from './components/Charts';
import { ParamsForm } from './components/ParamsForm';
import { SegmentsTable, SolverDetails, Summary } from './components/Results';
import { buildCsvExport, buildJsonExport, downloadText, exportBaseName } from './export';
import { km } from './format';
import { defaultForm, inputsKey, validate, type FormState } from './params';
import type { CourseInfo, CourseRef, OptimizeResponse, SimulateResponse } from './types';

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

type Course = { ref: CourseRef; info: CourseInfo };
type RunKind = 'simulate' | 'optimize';
type RunState =
  | { status: 'idle' }
  | { status: 'running'; kind: RunKind }
  | { status: 'error'; kind: RunKind; message: string }
  | { status: 'done'; kind: RunKind; key: string; data: SimulateResponse | OptimizeResponse };

type CourseStatus = { loading: true } | { loading: false; error?: string; retry?: 'demo' | 'upload' | 'pick' };

const courseId = (c: Course | null) => (c ? `${c.info.source}:${c.info.content_hash}` : null);
const errorMessage = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : 'Unexpected error.');
const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';

export default function App() {
  const [course, setCourse] = useState<Course | null>(null);
  const [courseStatus, setCourseStatus] = useState<CourseStatus>({ loading: false });
  const [form, setForm] = useState<FormState>(defaultForm);
  const [run, setRun] = useState<RunState>({ status: 'idle' });
  const [lastResult, setLastResult] = useState<Extract<RunState, { status: 'done' }> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const resultsHeading = useRef<HTMLHeadingElement>(null);
  const lastUpload = useRef<File | null>(null);

  const { errors, params } = useMemo(() => validate(form), [form]);
  const currentKey = inputsKey(courseId(course), params);
  const result = lastResult;
  const stale = result !== null && result.key !== currentKey;
  const running = run.status === 'running';

  useEffect(() => () => abortRef.current?.abort(), []);

  const newSignal = () => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    return ctrl.signal;
  };

  const loadDemo = useCallback(async () => {
    const signal = newSignal();
    setCourseStatus({ loading: true });
    try {
      const info = await fetchDemoCourse(signal);
      setCourse({ ref: { kind: 'demo' }, info });
      setCourseStatus({ loading: false });
      setRun({ status: 'idle' });
      setLastResult(null);
    } catch (e) {
      if (!isAbort(e)) setCourseStatus({ loading: false, error: errorMessage(e), retry: 'demo' });
    }
  }, []);

  const loadFile = useCallback(async (file: File) => {
    lastUpload.current = file;
    if (!file.name.toLowerCase().endsWith('.gpx')) {
      setCourseStatus({ loading: false, error: `"${file.name}" is not a .gpx file.`, retry: 'pick' });
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setCourseStatus({ loading: false, error: `"${file.name}" is larger than 10 MB.`, retry: 'pick' });
      return;
    }
    const signal = newSignal();
    setCourseStatus({ loading: true });
    try {
      const [info, text] = await Promise.all([parseCourseFile(file, signal), file.text()]);
      setCourse({ ref: { kind: 'gpx', gpx_text: text, filename: file.name }, info });
      setCourseStatus({ loading: false });
      setRun({ status: 'idle' });
      setLastResult(null);
    } catch (e) {
      if (!isAbort(e)) {
        // Network/server failures can be retried with the same file; invalid files need a different one.
        const retry = e instanceof ApiError && e.status === 422 ? 'pick' : 'upload';
        setCourseStatus({ loading: false, error: `Could not load "${file.name}": ${errorMessage(e)}`, retry });
      }
    }
  }, []);

  const execute = useCallback(
    async (kind: RunKind) => {
      if (!course || !params || !currentKey) return;
      const signal = newSignal();
      setRun({ status: 'running', kind });
      try {
        const data = await (kind === 'simulate' ? simulate : optimize)(course.ref, params, signal);
        const done = { status: 'done' as const, kind, key: currentKey, data };
        setRun(done);
        setLastResult(done);
        requestAnimationFrame(() => resultsHeading.current?.focus());
      } catch (e) {
        if (isAbort(e)) setRun({ status: 'idle' });
        else setRun({ status: 'error', kind, message: errorMessage(e) });
      }
    },
    [course, params, currentKey],
  );

  const cancel = () => {
    abortRef.current?.abort();
    setRun({ status: 'idle' });
  };

  const startOver = () => {
    abortRef.current?.abort();
    setCourse(null);
    setCourseStatus({ loading: false });
    setForm(defaultForm());
    setRun({ status: 'idle' });
    setLastResult(null);
    if (fileRef.current) fileRef.current.value = '';
  };

  const exportResult = (format: 'json' | 'csv') => {
    if (!result || stale) return;
    const base = exportBaseName(result.data);
    if (format === 'json') downloadText(`${base}.json`, buildJsonExport(result.data, new Date()), 'application/json');
    else downloadText(`${base}.csv`, buildCsvExport(result.data), 'text/csv');
  };

  const optData = result && 'optimization' in result.data ? (result.data as OptimizeResponse).optimization : null;
  // Results are always drawn with the course they were computed for (dimmed when stale).
  const shownCourse = result ? result.data.course : course?.info;

  return (
    <div className="app">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <div>
          <h1>Vector Pacing</h1>
          <p className="tagline">Cycling pacing simulator · constant vs optimized power · model estimates only</p>
        </div>
        {course && (
          <button type="button" className="btn btn-quiet" onClick={startOver}>
            Start over
          </button>
        )}
      </header>

      <main id="main">
        <section className="panel course-panel" aria-labelledby="course-h">
          <h2 id="course-h">1. Course</h2>
          <div className="course-actions">
            <button type="button" className="btn btn-primary" onClick={loadDemo} disabled={courseStatus.loading || running}>
              Try demo course
            </button>
            <span className="or">or</span>
            <label className={`btn btn-secondary file-btn${courseStatus.loading || running ? ' is-disabled' : ''}`}>
              Upload GPX…
              <input
                ref={fileRef}
                type="file"
                accept=".gpx,application/gpx+xml"
                className="visually-hidden-input"
                disabled={courseStatus.loading || running}
                onClick={(e) => {
                  e.currentTarget.value = ''; // allow re-selecting the same file after an error
                }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void loadFile(f);
                }}
              />
            </label>
          </div>
          <div aria-live="polite">
            {courseStatus.loading && <p className="status">Loading course…</p>}
            {!courseStatus.loading && courseStatus.error && (
              <div className="alert alert-error" role="alert">
                <p>{courseStatus.error}</p>
                <div className="alert-actions">
                  {courseStatus.retry === 'demo' && (
                    <button type="button" className="btn btn-small" onClick={() => void loadDemo()}>
                      Retry
                    </button>
                  )}
                  {courseStatus.retry === 'upload' && (
                    <button type="button" className="btn btn-small" onClick={() => lastUpload.current && void loadFile(lastUpload.current)}>
                      Retry upload
                    </button>
                  )}
                  {(courseStatus.retry === 'pick' || courseStatus.retry === 'upload') && (
                    <button type="button" className="btn btn-small" onClick={() => fileRef.current?.click()}>
                      Choose another file
                    </button>
                  )}
                  <button type="button" className="btn btn-small btn-quiet" onClick={() => setCourseStatus({ loading: false })}>
                    Dismiss
                  </button>
                </div>
              </div>
            )}
          </div>
          {!course && !courseStatus.loading && (
            <p className="empty">
              No course loaded yet. Start with the bundled <strong>demo course</strong> (works offline), or upload a GPX file
              with a track or route. Elevation is taken from the file.
            </p>
          )}
          {course && (
            <div className="course-summary">
              <h3>{course.info.name}</h3>
              <dl className="stats inline">
                <dt>Distance</dt>
                <dd>{km(course.info.length_m)}</dd>
                <dt>Climbing</dt>
                <dd>{course.info.elevation_gain_m.toFixed(0)} m</dd>
                <dt>Grade range</dt>
                <dd>
                  {course.info.min_grade_pct.toFixed(1)} % to {course.info.max_grade_pct.toFixed(1)} %
                </dd>
                <dt>Source</dt>
                <dd>{course.ref.kind === 'demo' ? 'Bundled demo' : course.info.source}</dd>
              </dl>
              {course.info.warnings.length > 0 && (
                <div className="alert alert-warn">
                  <p>Course notes:</p>
                  <ul>
                    {course.info.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </section>

        {course && (
          <div className="workspace">
            <section className="panel settings-panel" aria-labelledby="settings-h">
              <h2 id="settings-h">2. Rider settings</h2>
              <ParamsForm
                form={form}
                errors={errors}
                disabled={running}
                onChange={(key, value) => setForm((f) => ({ ...f, values: { ...f.values, [key]: value } }))}
                onModeChange={(mode) => setForm((f) => ({ ...f, baselineMode: mode }))}
                onReset={() => setForm(defaultForm())}
              />
            </section>

            <section className="panel results-panel" aria-labelledby="results-h">
              <h2 id="results-h" tabIndex={-1} ref={resultsHeading}>
                3. Simulate, optimize &amp; compare
              </h2>
              <div className="run-bar">
                <button type="button" className="btn btn-secondary" disabled={!params || running} onClick={() => void execute('simulate')}>
                  Simulate baseline
                </button>
                <button type="button" className="btn btn-primary" disabled={!params || running} onClick={() => void execute('optimize')}>
                  Optimize pacing
                </button>
                {running && (
                  <button type="button" className="btn btn-quiet" onClick={cancel}>
                    Cancel
                  </button>
                )}
              </div>
              <div aria-live="polite" className="run-status">
                {!params && <p className="alert alert-error">Fix the highlighted settings to run the simulation.</p>}
                {running && (
                  <p className="status">
                    <span className="spinner" aria-hidden="true" />
                    {run.kind === 'optimize'
                      ? 'Optimizing pacing (bounded to 30 s)…'
                      : 'Simulating constant-power baseline…'}
                  </p>
                )}
                {run.status === 'error' && (
                  <div className="alert alert-error" role="alert">
                    <p>
                      {run.kind === 'optimize' ? 'Optimization' : 'Simulation'} failed: {run.message}
                    </p>
                    <div className="alert-actions">
                      <button type="button" className="btn btn-small" disabled={!params} onClick={() => void execute(run.kind)}>
                        Retry
                      </button>
                      <button type="button" className="btn btn-small btn-quiet" onClick={() => setRun({ status: 'idle' })}>
                        Dismiss
                      </button>
                    </div>
                  </div>
                )}
                {stale && !running && (
                  <div className="alert alert-warn" role="status">
                    <p>
                      <strong>Results are out of date.</strong> The course or settings changed after this{' '}
                      {result.kind === 'optimize' ? 'optimization' : 'simulation'} ran. Run again to update; export is
                      disabled until then.
                    </p>
                  </div>
                )}
              </div>

              {!result && !running && run.status !== 'error' && (
                <p className="empty">
                  Run <strong>Simulate baseline</strong> to see constant-power time and W′, or{' '}
                  <strong>Optimize pacing</strong> to also compute and verify a variable-power plan.
                </p>
              )}

              <div className={stale ? 'results is-stale' : 'results'} aria-hidden={stale || undefined}>
                {result && <Summary baseline={result.data.baseline} optimization={optData} />}
                {shownCourse && (
                  <Charts
                    course={shownCourse}
                    baseline={result ? result.data.baseline : null}
                    optimized={optData?.result ?? null}
                  />
                )}
                {optData && <SegmentsTable optimization={optData} />}
                {optData && <SolverDetails optimization={optData} />}
              </div>

              {result && (
                <div className="export-bar">
                  <h3>4. Export</h3>
                  <button type="button" className="btn btn-secondary" disabled={stale || running} onClick={() => exportResult('json')}>
                    Export JSON (full)
                  </button>
                  <button type="button" className="btn btn-secondary" disabled={stale || running} onClick={() => exportResult('csv')}>
                    Export CSV (per step)
                  </button>
                  <p className="help">
                    Includes all parameters, units, app {result.data.meta.app_version} and model {result.data.meta.model_version}.
                  </p>
                </div>
              )}
            </section>
          </div>
        )}
      </main>

      <footer className="footer">
        <p>
          <strong>Model estimates, not predictions.</strong> Vector Pacing uses a simplified steady-state physics model and
          a CP/W′ balance approximation with the parameters you enter. Results are not validated against real rides and
          are not medical or training advice.
        </p>
      </footer>
    </div>
  );
}
