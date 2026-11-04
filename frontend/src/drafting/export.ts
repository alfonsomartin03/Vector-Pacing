import type { DraftingResponse } from './types';
import { clock } from './format';

export const DRAFT_EXPORT_SCHEMA = 'vector-pacing-drafting-export/1';

export function buildDraftJson(r: DraftingResponse, generatedAt: Date): string {
  const { course, ...scenario } = r.scenario;
  return JSON.stringify(
    {
      schema: DRAFT_EXPORT_SCHEMA,
      generated_at: generatedAt.toISOString(),
      app_version: r.meta.app_version,
      drafting_model_version: r.meta.drafting_model_version,
      input_format: r.meta.format,
      disclaimer: r.meta.disclaimer,
      settings: r.meta.settings,
      settings_note: r.meta.settings_note,
      units: { distance: 'm', time: 's (since scenario start)', probability: '0–1' },
      scenario: { ...scenario, course: { name: course.name, length_m: course.length_m } },
      limitations: r.limitations,
      counts: r.counts,
      events: r.events,
      dismissed_encounters: r.encounters,
      power_screen: r.power_screen,
    },
    null,
    2,
  );
}

const q = (s: string) => `"${s.replace(/"/g, '""')}"`;

export function buildDraftCsv(r: DraftingResponse): string {
  const st = r.meta.settings;
  const header = [
    'event_id', 'follower', 'leader', 'classification', 'start_time', 'end_time', 'start_s', 'end_s', 'duration_s',
    'counted_s', 'exempt_s', 'missing_s', 'gap_median_m', 'gap_min_m', 'gap_max_m', 'gap_sigma_m',
    'probability_in_zone', 'data_quality', 'lateral_used', 'order_uncertain', 'course_start_m', 'course_end_m', 'power_anomaly_ids', 'limitations',
  ];
  const lines = [
    `# Vector Pacing ${r.meta.app_version} | drafting model ${r.meta.drafting_model_version} | scenario "${r.scenario.name.replace(/"/g, "'")}" (${r.scenario.content_hash})`,
    `# ${r.meta.disclaimer}`,
    `# settings: zone ${st.zone_length_m} m, allowed pass ${st.allowed_pass_s} s, drop-back ${st.drop_back_s} s, lateral ${st.lateral_separation_m} m, exempt [${st.exempt_sections.map((e) => `${e.start_m}-${e.end_m} ${e.label}`).join('; ')}], clock tolerance ${st.timestamp_tolerance_s} s, assumed accuracy ${st.assumed_accuracy_m} m — ${r.meta.settings_note}`,
    header.join(','),
  ];
  for (const e of r.events) {
    lines.push(
      [
        e.id, e.follower, e.leader, e.classification, clock(e.start_s, r.scenario.start_time), clock(e.end_s, r.scenario.start_time),
        e.start_s, e.end_s, e.duration_s, e.counted_s, e.exempt_s, e.missing_s, e.gap_median_m ?? '', e.gap_min_m ?? '',
        e.gap_max_m ?? '', e.sigma_median_m ?? '', e.probability_in_zone ?? '', e.quality, e.lateral_used, e.order_uncertain,
        e.course_start_m ?? '', e.course_end_m ?? '', e.power_anomaly_ids.join(' '), q(e.limitations.join(' | ')),
      ].join(','),
    );
  }
  const ps = r.power_screen;
  if (ps && ps.anomalies.length) {
    lines.push('', `# Power screen (within rider, ${ps.version}): ${ps.note}`);
    lines.push(
      [
        'anomaly_id', 'rider', 'start_time', 'end_time', 'start_s', 'end_s', 'duration_s', 'review_priority',
        'average_speed_kmh', 'average_power_w', 'reference_power_w', 'deficit_w', 'comparable_sections',
        'speed_normalised', 'lat', 'lon', 'gps_event_ids',
      ].join(','),
    );
    for (const a of ps.anomalies) {
      lines.push(
        [
          a.id, a.rider, clock(a.start_s, r.scenario.start_time), clock(a.end_s, r.scenario.start_time), a.start_s, a.end_s,
          a.duration_s, a.score, a.average_speed_kmh, a.average_power_w ?? '', a.reference_power_w, a.deficit_w,
          a.peer_count, a.speed_normalised, a.lat, a.lon, a.gps_event_ids.join(' '),
        ].join(','),
      );
    }
  }
  return lines.join('\n') + '\n';
}

export function draftBaseName(r: DraftingResponse): string {
  const slug = r.scenario.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'scenario';
  return `vector-pacing_drafting_${slug}`;
}
