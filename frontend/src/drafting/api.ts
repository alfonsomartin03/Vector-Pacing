import { ApiError } from '../api';
import type { DraftingResponse, DraftingSettings, PresetsResponse, ScenarioRef } from './types';

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${(import.meta.env.VITE_API_URL || '').replace(/\/$/, '')}${path}`, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new ApiError('Could not reach the Vector Pacing API. Is the backend running?', 0);
  }
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const detail = body && typeof body === 'object' && typeof (body as { detail?: unknown }).detail === 'string'
      ? (body as { detail: string }).detail
      : `The server returned an error (HTTP ${res.status}).`;
    throw new ApiError(detail, res.status);
  }
  if (body === null) throw new ApiError('The server returned an unreadable response.', res.status);
  return body as T;
}

export async function fetchPresets(signal?: AbortSignal): Promise<PresetsResponse> {
  const p = await call<PresetsResponse>('/api/drafting/presets', { signal });
  if (!p || typeof p !== 'object' || !p.defaults || typeof p.defaults.zone_length_m !== 'number' || !p.presets) {
    throw new ApiError('The server returned unexpected drafting settings.', 200);
  }
  return p;
}

export function analyzeDrafting(scenario: ScenarioRef, settings: DraftingSettings, signal?: AbortSignal) {
  if (scenario.kind === 'demo') {
    return call<DraftingResponse>('/api/drafting/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario, settings }),
      signal,
    });
  }
  const form = new FormData();
  for (const f of scenario.riders) form.append('files', f, f.name);
  if (scenario.course) form.append('course', scenario.course, scenario.course.name);
  form.append('settings', JSON.stringify(settings));
  return call<DraftingResponse>('/api/drafting/analyze-fit', { method: 'POST', body: form, signal });
}
