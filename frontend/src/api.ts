import type { CourseInfo, CourseRef, OptimizeResponse, SimulateResponse } from './types';
import type { RequestParams } from './params';

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new ApiError(
      'Could not reach the Vector Pacing API. Is the backend running? (see README: "make dev" or "make serve")',
      0,
    );
  }
  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body && typeof (body as { detail: unknown }).detail === 'string'
        ? (body as { detail: string }).detail
        : `The server returned an error (HTTP ${res.status}).`;
    throw new ApiError(detail, res.status);
  }
  if (body === null) throw new ApiError('The server returned an unreadable response.', res.status);
  return body as T;
}

export function fetchDemoCourse(signal?: AbortSignal): Promise<CourseInfo> {
  return request<CourseInfo>('/api/courses/demo', { signal });
}

export function parseCourseFile(file: File, signal?: AbortSignal): Promise<CourseInfo> {
  const form = new FormData();
  form.append('file', file);
  return request<CourseInfo>('/api/courses/parse', { method: 'POST', body: form, signal });
}

function postJson<T>(path: string, course: CourseRef, params: RequestParams, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ course, ...params }),
    signal,
  });
}

export const simulate = (course: CourseRef, params: RequestParams, signal?: AbortSignal) =>
  postJson<SimulateResponse>('/api/simulate', course, params, signal);

export const optimize = (course: CourseRef, params: RequestParams, signal?: AbortSignal) =>
  postJson<OptimizeResponse>('/api/optimize', course, params, signal);
