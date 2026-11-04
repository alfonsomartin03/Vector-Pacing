import type { PowerScreen } from './types';
import { clock } from './format';

interface Props {
  screen: PowerScreen;
  startTime: string | null;
  onSelectEvent: (id: string) => void;
}

const w = (v: number | null) => (v === null ? '—' : `${Math.round(v)} W`);

/** Within-rider power screen (ported from RaceGuard): low power against the rider's own comparable sections. */
export function PowerScreenCard({ screen, startTime, onSelectEvent }: Props) {
  const withPower = screen.riders.filter((r) => r.has_power);
  const notes = screen.riders.flatMap((r) => r.warnings.map((m) => ({ id: r.id, m })));
  return (
    <section className="card power-screen" aria-labelledby="power-h">
      <h3 id="power-h">Power screen (within rider)</h3>
      <p className="help">{screen.note}</p>
      {withPower.length === 0 ? (
        <p className="empty">No power data in the uploaded rides, so the power screen could not run.</p>
      ) : (
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Power anomalies table (scrollable)">
          <table>
            <caption>
              Power anomalies ({screen.anomalies.length}) — {withPower.length} of {screen.riders.length} rider
              {screen.riders.length === 1 ? '' : 's'} with power
            </caption>
            <thead>
              <tr>
                <th scope="col">ID</th>
                <th scope="col">Rider</th>
                <th scope="col">Start – end</th>
                <th scope="col">Duration</th>
                <th scope="col">Speed</th>
                <th scope="col">Power vs own reference</th>
                <th scope="col">Review priority</th>
                <th scope="col">GPS interval</th>
              </tr>
            </thead>
            <tbody>
              {screen.anomalies.map((a) => (
                <tr key={a.id}>
                  <td>{a.id}</td>
                  <td>{a.rider}</td>
                  <td>
                    {clock(a.start_s, startTime)} – {clock(a.end_s, startTime)}
                  </td>
                  <td>{a.duration_s.toFixed(0)} s</td>
                  <td>{a.average_speed_kmh.toFixed(1)} km/h</td>
                  <td title={a.notes.join(' ')}>
                    {w(a.average_power_w)} vs {w(a.reference_power_w)} (−{Math.round(a.deficit_w)} W, {a.peer_count} sections
                    {a.speed_normalised ? ', speed-normalised' : ''})
                  </td>
                  <td>{Math.round(a.score * 100)} %</td>
                  <td>
                    {a.gps_event_ids.length
                      ? a.gps_event_ids.map((id) => (
                          <button key={id} type="button" className="btn btn-small" aria-label={`Inspect ${id}`} onClick={() => onSelectEvent(id)}>
                            {id}
                          </button>
                        ))
                      : '—'}
                  </td>
                </tr>
              ))}
              {screen.anomalies.length === 0 && (
                <tr>
                  <td colSpan={8}>No power anomalies found.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {notes.length > 0 && (
        <ul className="help">
          {notes.map((n, i) => (
            <li key={i}>
              {n.id}: {n.m}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
