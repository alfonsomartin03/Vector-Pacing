import type { OptimizeResponse, SimulateResponse } from './types';

export const EXPORT_SCHEMA = 'vector-pacing-export/1';

function isOptimize(r: SimulateResponse | OptimizeResponse): r is OptimizeResponse {
  return 'optimization' in r;
}

/** Full JSON export: parameters, model/version, summaries, segments and per-step series. */
export function buildJsonExport(r: SimulateResponse | OptimizeResponse, generatedAt: Date): string {
  const opt = isOptimize(r) ? r.optimization : null;
  const { profile, ...courseSummary } = r.course;
  const doc = {
    schema: EXPORT_SCHEMA,
    generated_at: generatedAt.toISOString(),
    app_version: r.meta.app_version,
    model_version: r.meta.model_version,
    disclaimer: r.meta.disclaimer,
    units: {
      distance: 'm',
      time: 's',
      power: 'W',
      speed: 'km/h',
      w_prime: 'J',
      elevation: 'm',
      grade: '%',
    },
    inputs: r.meta.inputs,
    derived_bounds_w: r.meta.derived,
    course: { ...courseSummary, profile },
    baseline: r.baseline,
    optimization: opt,
    comparison: opt
      ? {
          status: opt.status,
          baseline_time_s: r.baseline.total_time_s,
          optimized_time_s: opt.result?.total_time_s ?? null,
          time_saved_s: opt.time_saved_s,
          note: 'Same course, rider, environment and simulation model for both strategies. Model estimates only.',
        }
      : null,
  };
  return JSON.stringify(doc, null, 2);
}

/** Per-step CSV comparing baseline and (if present) optimized strategies on the same distance grid. */
export function buildCsvExport(r: SimulateResponse | OptimizeResponse): string {
  const b = r.baseline.series;
  const o = isOptimize(r) ? r.optimization.result?.series ?? null : null;
  const header = [
    'distance_km',
    'elevation_m',
    'grade_pct',
    'baseline_power_w',
    'baseline_speed_kmh',
    'baseline_w_bal_kj',
    'baseline_elapsed_s',
  ];
  if (o) header.push('optimized_planned_power_w', 'optimized_power_w', 'optimized_speed_kmh', 'optimized_w_bal_kj', 'optimized_elapsed_s');
  const lines = [
    `# Vector Pacing ${r.meta.app_version} | model ${r.meta.model_version} | course "${r.course.name.replace(/"/g, "'")}" (${r.course.content_hash})`,
    `# ${r.meta.disclaimer}`,
    `# power columns are applied power at the crank (W); planned power can differ when coasting or speed-limited`,
    header.join(','),
  ];
  const elev = r.course.profile.elevation_m;
  const grade = r.course.profile.grade_pct;
  for (let i = 0; i < b.distance_m.length; i++) {
    const row = [
      ((b.distance_m[i] ?? 0) / 1000).toFixed(4),
      String(elev[i] ?? ''),
      String(grade[i] ?? ''),
      String(b.power_w[i]),
      String(b.speed_kmh[i]),
      ((b.w_bal_j[i] ?? 0) / 1000).toFixed(4),
      String(b.elapsed_s[i]),
    ];
    if (o) {
      row.push(
        String(o.planned_power_w[i]),
        String(o.power_w[i]),
        String(o.speed_kmh[i]),
        ((o.w_bal_j[i] ?? 0) / 1000).toFixed(4),
        String(o.elapsed_s[i]),
      );
    }
    lines.push(row.join(','));
  }
  return lines.join('\n') + '\n';
}

export function exportBaseName(r: SimulateResponse | OptimizeResponse): string {
  const slug = r.course.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'course';
  return `vector-pacing_${slug}_${isOptimize(r) ? 'comparison' : 'baseline'}`;
}

export function downloadText(filename: string, text: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
