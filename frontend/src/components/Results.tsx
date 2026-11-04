import { formatDelta, formatDuration, kj, km, pct } from '../format';
import type { BaselineResult, Optimization, SimResult } from '../types';

function Feasible({ r }: { r: SimResult }) {
  return r.feasible ? (
    <span className="badge badge-ok">Feasible</span>
  ) : (
    <span className="badge badge-bad">Infeasible</span>
  );
}

function StrategyCard({ title, r, powerLabel }: { title: string; r: SimResult; powerLabel: string }) {
  return (
    <section className="card strategy" aria-label={title}>
      <h3>
        {title} <Feasible r={r} />
      </h3>
      <p className="big">{formatDuration(r.total_time_s)}</p>
      <dl className="stats">
        <dt>Power</dt>
        <dd>{powerLabel}</dd>
        <dt>Avg power</dt>
        <dd>{r.average_power_w.toFixed(0)} W</dd>
        <dt>Normalized power (approx.)</dt>
        <dd>{r.normalized_power_w.toFixed(0)} W</dd>
        <dt>Avg speed</dt>
        <dd>{r.average_speed_kmh.toFixed(1)} km/h</dd>
        <dt>Lowest W′ balance</dt>
        <dd className={r.min_w_bal_j < 0 ? 'neg' : undefined}>{kj(r.min_w_bal_j, 2)}</dd>
        <dt>Time coasting / speed-limited</dt>
        <dd>
          {pct(r.coasting_fraction, 1)} / {pct(r.speed_capped_fraction, 1)}
        </dd>
      </dl>
      {r.violations.length > 0 && (
        <ul className="violations">
          {r.violations.map((v) => (
            <li key={v}>{v}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function Summary({ baseline, optimization }: { baseline: BaselineResult; optimization: Optimization | null }) {
  const opt = optimization?.result ?? null;
  return (
    <div className="summary">
      <StrategyCard title="Baseline: constant power" r={baseline} powerLabel={`${baseline.power_w.toFixed(0)} W constant`} />
      {optimization && opt && (
        <StrategyCard
          title="Optimized: variable power"
          r={opt}
          powerLabel={`${optimization.segments.length} segment targets`}
        />
      )}
      {optimization && (
        <section className={`card verdict verdict-${optimization.status}`} aria-label="Comparison">
          <h3>Comparison</h3>
          {optimization.status === 'improved' && optimization.time_saved_s !== null && (
            <p className="big">{formatDelta(-optimization.time_saved_s)}</p>
          )}
          {optimization.status === 'no_improvement' && <p className="big">No meaningful gain</p>}
          {optimization.status === 'failed' && <p className="big">Optimization failed</p>}
          <p>{optimization.message}</p>
          {optimization.status !== 'failed' && (
            <p className="help">Estimated difference under the model; same course, rider, environment and simulator for both.</p>
          )}
        </section>
      )}
      <p className="help selection">Baseline selection: {baseline.selection}</p>
    </div>
  );
}

export function SegmentsTable({ optimization }: { optimization: Optimization }) {
  const hasPlan = optimization.segments.some((s) => s.planned_power_w !== undefined);
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Segment plan table (scrollable)">
      <table>
        <caption>Pacing plan by segment</caption>
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">From–to</th>
            <th scope="col">Avg grade</th>
            <th scope="col">Baseline power</th>
            {hasPlan && <th scope="col">Planned power</th>}
            {hasPlan && <th scope="col">Applied avg power</th>}
            <th scope="col">Baseline time</th>
            {hasPlan && <th scope="col">Optimized time</th>}
          </tr>
        </thead>
        <tbody>
          {optimization.segments.map((s) => (
            <tr key={s.index}>
              <td>{s.index + 1}</td>
              <td>
                {km(s.start_m, 1)}–{km(s.end_m, 1)}
              </td>
              <td>{s.mean_grade_pct.toFixed(1)} %</td>
              <td>{s.baseline_power_w.toFixed(0)} W</td>
              {hasPlan && <td>{s.planned_power_w?.toFixed(0)} W</td>}
              {hasPlan && <td>{s.applied_avg_power_w?.toFixed(0)} W</td>}
              <td>{formatDuration(s.baseline_time_s)}</td>
              {hasPlan && <td>{s.optimized_time_s !== undefined ? formatDuration(s.optimized_time_s) : '—'}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SolverDetails({ optimization }: { optimization: Optimization }) {
  const s = optimization.solver;
  return (
    <details className="solver">
      <summary>Solver details</summary>
      <ul>
        <li>Method: {s.method}</li>
        <li>Converged: {s.success ? 'yes' : 'no'} — {s.message || 'no message'}</li>
        <li>
          Iterations: {s.iterations}; simulations: {s.evaluations} (budget {s.max_evaluations} / {s.max_wall_time_s} s)
        </li>
        <li>Compute time: {s.elapsed_s.toFixed(2)} s</li>
        <li>Minimum reportable improvement: {s.min_reportable_improvement_s} s</li>
        {optimization.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </details>
  );
}
